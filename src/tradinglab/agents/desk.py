"""One-shot "trading desk" helpers: one ticker, one snapshot, one decision.

``run_agent_backtest`` walks the pipeline over a historical window. This
module covers the other use: analyse a single ticker as of its latest close.
It backs the ``snapshot`` and ``desk`` CLI subcommands.

Building the snapshot needs no LLM credentials, so an external agent (Claude
Code, Codex, Cursor) can fetch it and run the role workflow itself; ``desk``
runs the same snapshot through this repo's own API pipeline.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import pandas as pd

from tradinglab.agents.graph import snapshot_from_history
from tradinglab.agents.state import AgentDayLog, MarketSnapshot
from tradinglab.config import DATA_CACHE_DIR, DataConfig
from tradinglab.data.prices import fetch_prices

DISCLAIMER = (
    "Research output, not investment advice. This is a simulated analysis on "
    "public data; nothing here places orders. In this project's own "
    "leakage-controlled backtest the agent desk did not beat buy-and-hold."
)

# A latest bar older than this many calendar days is flagged as stale
# (long weekends and holidays stay under it; halted or delisted symbols do not).
_STALE_AFTER_DAYS = 5
# US cash session ends 16:00 New York; allow the closing print to settle.
# The partial-bar logic below is US-centric by design: for other exchanges it
# is an approximation, and the notes say so.
_NY_TZ = "America/New_York"
_US_CLOSE_MINUTE = 16 * 60 + 15
# Yahoo rate-limits bursts and then answers with empty payloads rather than
# errors. One short pause and a second attempt recovers most of those.
_RETRY_WAIT_S = 6.0


def _before_us_close(ts: pd.Timestamp) -> bool:
    """True when a New York timestamp falls before the US closing print."""
    return ts.hour * 60 + ts.minute < _US_CLOSE_MINUTE


def normalize_ticker(ticker: str) -> str:
    """Upper-case and strip a user-supplied symbol; ValueError when empty."""
    cleaned = str(ticker).strip().upper()
    if not cleaned:
        raise ValueError("ticker must not be empty")
    return cleaned


def _cache_holds_partial_bar(cache_file: Path) -> bool:
    """True when the cache's last bar was downloaded while its session was open.

    Such a bar is an intraday reading, not a close; reusing it after the
    session ended would present stale mid-session prices as the day's close.
    An unreadable cache also returns True so it gets replaced.
    """
    try:
        last_bar = pd.Timestamp(pd.read_parquet(cache_file).index[-1])
    except Exception:
        return True
    written = pd.Timestamp(cache_file.stat().st_mtime, unit="s", tz="UTC").tz_convert(_NY_TZ)
    same_day = last_bar.normalize() == written.normalize().tz_localize(None)
    return same_day and _before_us_close(written)


def fetch_latest_prices(ticker: str, max_age_hours: float = 24.0,
                        cache_dir: Path | str | None = None) -> pd.DataFrame:
    """Full daily history for ``ticker`` up to the latest available bar.

    Uses the standard open-ended price cache but re-downloads when that cache
    is older than ``max_age_hours`` or ends in a bar captured mid-session, so
    a desk run sees yesterday's close or newer and never a stale intraday
    reading. Raises ``ValueError`` for unknown symbols.
    """
    start = DataConfig().start
    cache_root = Path(cache_dir) if cache_dir is not None else DATA_CACHE_DIR
    cache_file = cache_root / f"{ticker}_{start}_latest.parquet"
    force = cache_file.exists() and (
        (time.time() - cache_file.stat().st_mtime) > max_age_hours * 3600.0
        or _cache_holds_partial_bar(cache_file)
    )
    return fetch_prices(ticker, start, None, cache_dir=cache_root, force=force)[ticker]


@dataclass
class DeskSnapshot:
    """A market snapshot plus the facts a caller needs to judge its quality."""

    snapshot: MarketSnapshot
    as_of: str                   # date of the last bar used, "YYYY-MM-DD"
    last_close: float
    history_rows: int
    live: bool                   # fundamentals and/or news were injected
    fetched_at: str = ""         # when the snapshot was built, UTC "YYYY-MM-DDTHH:MMZ"
    partial_session: bool = False    # last bar is an unfinished US session
    stale: bool = False              # latest bar is older than a long weekend
    fundamentals_missing: list[str] = field(default_factory=list)
    # Everything a caller should know. The subset that changes how the data
    # must be read is also in ``snapshot.data_notes`` and so reaches the agents.
    notes: list[str] = field(default_factory=list)


def _session_state(as_of: pd.Timestamp, now: pd.Timestamp) -> tuple[bool, bool, list[str]]:
    """``(partial_session, stale, notes)`` for the last bar relative to ``now``.

    ``now`` is a New York timestamp. US-centric by design (see ``_NY_TZ``).
    """
    notes: list[str] = []
    today = now.normalize().tz_localize(None)
    partial = bool(as_of == today and _before_us_close(now))
    if partial:
        notes.append(
            f"The last bar ({as_of:%Y-%m-%d}) is dated today and US markets have "
            "not closed yet: for a US listing it is a partial session. Its close "
            "is only the latest price, its volume is a part-day count that must "
            "not be compared with full days, and every indicator value will "
            "change by the close."
        )
    age_days = (today - as_of).days
    stale = age_days > _STALE_AFTER_DAYS
    if stale:
        notes.append(
            f"The latest available bar is {age_days} days old ({as_of:%Y-%m-%d}). "
            "The symbol may be halted, delisted or illiquid; treat the snapshot "
            "as stale."
        )
    return partial, stale, notes


def _fetch_live_inputs(ticker: str) -> tuple[dict, list[dict]]:
    """Current fundamentals and headlines, with one retry for empty answers."""
    from tradinglab.data.fundamentals import (
        RATIO_LABELS,
        fetch_fundamentals_snapshot,
        missing_ratio_fields,
    )
    from tradinglab.data.news import fetch_current_news

    def empty(raw: dict) -> bool:
        return not raw.get("available") or len(missing_ratio_fields(raw)) == len(RATIO_LABELS)

    raw = fetch_fundamentals_snapshot(ticker)
    items = fetch_current_news(ticker)
    if empty(raw) or not items:
        time.sleep(_RETRY_WAIT_S)
        if empty(raw):
            second = fetch_fundamentals_snapshot(ticker)
            if not empty(second):
                raw = second
        if not items:
            items = fetch_current_news(ticker)
    return raw, items


def build_desk_snapshot(ticker: str, date: str | None = None, live: bool = True,
                        anonymize: bool = False, lookback: int = 60,
                        df: pd.DataFrame | None = None,
                        now: pd.Timestamp | None = None) -> DeskSnapshot:
    """Build the snapshot the agents see for ``ticker`` as of ``date``.

    ``date=None`` means the latest available bar. Fundamentals and news are
    only injected when ``live`` is set AND the snapshot is for the latest bar
    AND it is not anonymized: Yahoo serves current values only, so adding them
    to a historical date would leak the future, and company headlines defeat
    masking. Each skipped, failed or incomplete input is recorded in ``notes``
    rather than silently dropped, and the notes that change how the data must
    be read (partial session, stale bar, incomplete fundamentals) are also
    written into the snapshot itself so the agents see them.

    ``df`` (canonical price frame) and ``now`` (tz-aware timestamp) exist for
    tests; by default prices are fetched and ``now`` is the New York clock.
    """
    ticker = normalize_ticker(ticker)
    if df is None:
        df = fetch_latest_prices(ticker)
    if df.empty:
        raise ValueError(f"No price history for {ticker}")

    if date is None:
        history = df
    else:
        history = df.loc[:pd.Timestamp(date)]
        if history.empty:
            raise ValueError(
                f"No price history for {ticker} on or before {date} "
                f"(data starts {df.index[0]:%Y-%m-%d})"
            )
    as_of = history.index[-1]
    historical = as_of < df.index[-1]

    notes: list[str] = []
    data_notes: list[str] = []
    fundamentals: str | None = None
    fundamentals_missing: list[str] = []
    news: list[str] | None = None
    if historical and not anonymize:
        notes.append(
            "This is a past date shown with its real ticker, dates and prices: a "
            "language model may remember what happened next. Use --anonymize, "
            "and probe the window, before trusting a decision made on it."
        )
    if live and historical:
        notes.append(
            "Fundamentals and news were NOT added: Yahoo serves current values "
            "only, and injecting them into a past date would leak the future."
        )
    elif live and anonymize:
        notes.append(
            "Fundamentals and news were NOT added: company figures and "
            "headlines would defeat the anonymization."
        )
    elif live:
        from tradinglab.data.fundamentals import (
            RATIO_LABELS,
            format_fundamentals,
            missing_ratio_fields,
        )

        raw, items = _fetch_live_inputs(ticker)
        if not raw.get("available"):
            notes.append(
                "Fundamentals unavailable from Yahoo "
                f"({raw.get('error', 'unknown error')}); the fundamentals "
                "analyst has nothing to work with."
            )
        else:
            fundamentals_missing = missing_ratio_fields(raw)
            has_quarters = bool(raw.get("quarterly_revenue") or raw.get("quarterly_net_income"))
            if len(fundamentals_missing) == len(RATIO_LABELS) and not has_quarters:
                notes.append(
                    "Fundamentals unavailable: Yahoo answered with no figures at "
                    "all (it does this when rate-limiting, and for symbols it has "
                    "no company data on). The fundamentals analyst has nothing to "
                    "work with; re-running in a minute may fix it."
                )
            else:
                fundamentals = format_fundamentals(raw)
                if fundamentals_missing:
                    incomplete = (
                        "Fundamentals are incomplete: no value was available for "
                        f"{', '.join(fundamentals_missing)}. A missing figure is "
                        "unknown, not zero."
                    )
                    data_notes.append(incomplete)
                    notes.append(
                        incomplete + " Funds and some foreign listings lack these "
                        "fields; otherwise Yahoo is probably rate-limiting and a "
                        "re-run in a minute fills them in."
                    )
        news = [
            f"{item.get('date', '')} [{item.get('publisher', '')}] {item.get('title', '')}"
            for item in items
        ] or None
        if news is None:
            notes.append(
                "No news headlines returned by Yahoo; the news and sentiment "
                "analysts have nothing to work with."
            )

    partial = stale = False
    if not historical:
        if now is None:
            now = pd.Timestamp.now(tz=_NY_TZ)
        partial, stale, session_notes = _session_state(as_of, now)
        notes += session_notes
        data_notes = session_notes + data_notes

    snapshot = snapshot_from_history(
        df, as_of, ticker, lookback=lookback, anonymize=anonymize,
        fundamentals=fundamentals, news=news,
    )
    if data_notes:
        snapshot = replace(snapshot, data_notes=data_notes)
    return DeskSnapshot(
        snapshot=snapshot,
        as_of=as_of.strftime("%Y-%m-%d"),
        last_close=float(history["close"].iloc[-1]),
        history_rows=len(history),
        live=bool(fundamentals or news),
        fetched_at=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%MZ"),
        partial_session=partial,
        stale=stale,
        fundamentals_missing=fundamentals_missing,
        notes=notes,
    )


def snapshot_to_dict(desk: DeskSnapshot) -> dict:
    """JSON-serializable view of a desk snapshot.

    ``context`` is the exact text block the agents receive. When the snapshot
    is anonymized, ``last_close`` is withheld so no real price level appears
    next to the masked block.
    """
    snap = desk.snapshot
    return {
        "ticker": snap.ticker,
        "as_of": desk.as_of,
        "fetched_at": desk.fetched_at,
        "last_close": None if snap.anonymize else round(desk.last_close, 4),
        "partial_session": desk.partial_session,
        "stale": desk.stale,
        "history_rows": desk.history_rows,
        "anonymized": snap.anonymize,
        "has_fundamentals": bool(snap.fundamentals),
        "fundamentals_missing": list(desk.fundamentals_missing),
        "news_headlines": len(snap.news or []),
        "notes": list(desk.notes),
        "context": snap.context_block(),
        "disclaimer": DISCLAIMER,
    }


def exposure_phrase(action: str, size: float) -> str:
    """Plain-language meaning of an (action, size) pair."""
    if action == "BUY":
        return f"long, target exposure {size:.2f} of capital"
    if action == "SELL":
        return f"short or avoid, target exposure -{size:.2f} of capital"
    return "no change to the existing stance"


def decision_to_dict(log: AgentDayLog, desk: DeskSnapshot,
                     usage: dict | None = None,
                     cost_usd: float | None = None) -> dict:
    """JSON-serializable record of one desk decision and its full reasoning."""
    return {
        "ticker": log.ticker,
        "as_of": desk.as_of,
        "last_close": None if desk.snapshot.anonymize else round(desk.last_close, 4),
        "final_action": log.final_action,
        "final_size": float(log.final_size),
        "meaning": exposure_phrase(log.final_action, float(log.final_size)),
        "research_verdict": log.debate.verdict,
        "research_rationale": log.debate.rationale,
        "trader": {
            "action": log.decision.action,
            "size": float(log.decision.size),
            "rationale": log.decision.rationale,
        },
        "fund_manager": {
            "approved": bool(log.risk.approved),
            "final_action": log.risk.final_action,
            "final_size": float(log.risk.final_size),
            "rationale": log.risk.rationale,
        },
        "analyst_reports": [
            {"role": report.role, "summary": report.summary} for report in log.reports
        ],
        "debate": [
            {"speaker": turn.speaker, "text": turn.text}
            for turn in log.debate.transcript
        ],
        "fetched_at": desk.fetched_at,
        "partial_session": desk.partial_session,
        "stale": desk.stale,
        "live": desk.live,
        "anonymized": desk.snapshot.anonymize,
        "notes": list(desk.notes),
        "usage": usage or {},
        "cost_usd": cost_usd,
        "disclaimer": DISCLAIMER,
    }


def price_phrase(desk: DeskSnapshot) -> str:
    """How to describe ``last_close``: a close, or a mid-session price."""
    if desk.snapshot.anonymize:
        return "close masked"
    if desk.partial_session:
        return f"last price {desk.last_close:.2f}, session still open"
    return f"close {desk.last_close:.2f}"


def render_decision_card(log: AgentDayLog, desk: DeskSnapshot) -> str:
    """Short plain-text summary of a desk decision for terminal output."""
    size = float(log.final_size)
    lines = [
        f"=== AI trading desk — {log.ticker} — as of {desk.as_of} ({price_phrase(desk)}) ===",
        f"FINAL DECISION:   {log.final_action} {size:.2f}  "
        f"({exposure_phrase(log.final_action, size)})",
        f"Research verdict: {log.debate.verdict} — {log.debate.rationale}",
        f"Trader proposal:  {log.decision.action} {float(log.decision.size):.2f} — "
        f"{log.decision.rationale}",
        f"Fund manager:     {'approved' if log.risk.approved else 'REJECTED'} — "
        f"{log.risk.rationale}",
    ]
    if desk.notes:
        lines.append("Data notes:")
        lines += [f"  - {note}" for note in desk.notes]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)
