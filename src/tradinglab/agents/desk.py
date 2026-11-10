"""One-shot "trading desk" helpers: one ticker, one snapshot, one decision.

``run_agent_backtest`` walks the pipeline over a historical window. This
module covers the other use: analyse a single ticker as of its newest bar.
It backs the ``snapshot`` and ``desk`` CLI subcommands.

Building the snapshot needs no LLM credentials, so an external agent (Claude
Code, Codex, Cursor) can fetch it and run the role workflow itself; ``desk``
runs the same snapshot through this repo's own API pipeline.

The guiding rule is that a data problem is never analysed silently. Every
gap, fallback and uncertainty becomes a note, and the notes that change how
the numbers must be read are written into the prompt as well.
"""
from __future__ import annotations

import json
import re
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
    "public data; nothing here places orders. This workflow has not been "
    "backtested: a reduced, price-only version of the desk did not beat "
    "buy-and-hold in the project's one-ticker, one-quarter test."
)

# A newest bar older than this many calendar days is flagged as stale
# (long weekends and holidays stay under it; halted or delisted symbols do not).
_STALE_AFTER_DAYS = 5
# Yahoo rate-limits bursts and then answers with empty payloads rather than
# errors. One short pause and a second attempt recovers most of those.
_RETRY_WAIT_S = 6.0
# A price download this recent is reused, so an immediate re-run (for example
# the skill's "fetch once more" step) does not hit Yahoo for prices again.
_PRICE_REUSE_S = 15 * 60
# After the regular close, the closing print takes a few minutes to settle.
_SETTLE = pd.Timedelta(minutes=15)
# Desk downloads are kept apart from the research cache: they can end in an
# intraday bar, which must never reach a backtest through a shared file.
_DESK_CACHE_SUBDIR = "desk"

_NY_TZ = "America/New_York"
_US_REGULAR_CLOSE_MINUTE = 16 * 60
# Plain US-style symbols (AAPL, BRK-B). Yahoo marks other venues with a suffix
# or prefix (7203.T, SAP.DE, ^GSPC, BTC-USD, EURUSD=X), which this rejects.
_US_STYLE_SYMBOL = re.compile(r"[A-Z]{1,5}(-[A-Z])?")


def normalize_ticker(ticker: str) -> str:
    """Upper-case and strip a user-supplied symbol; ValueError when empty."""
    cleaned = str(ticker).strip().upper()
    if not cleaned:
        raise ValueError("ticker must not be empty")
    return cleaned


def _utc(ts: pd.Timestamp | None) -> pd.Timestamp:
    """``ts`` as a tz-aware UTC timestamp; the current time when None."""
    if ts is None:
        return pd.Timestamp.now(tz="UTC")
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


# ---------------------------------------------------------------------------
# prices
# ---------------------------------------------------------------------------

@dataclass
class LatestPrices:
    """A price history plus what is known about the download that produced it."""

    df: pd.DataFrame
    downloaded_at: pd.Timestamp          # tz-aware UTC
    # Exchange facts captured with the download: {"exchange_tz",
    # "instrument_type", "regular_end" (UTC ISO), "last_trade" (UTC ISO or
    # None), "name"}; None when Yahoo's chart metadata could not be read.
    session: dict | None = None
    # True when Yahoo returned nothing now and ``df`` is an earlier download.
    refresh_failed: bool = False


def _fetch_session_info(ticker: str) -> dict | None:
    """Exchange calendar facts for ``ticker`` from Yahoo's chart metadata.

    Costs one chart request. Returns None on any failure: the caller then
    decides only what it can be certain of and reports the rest as unknown.
    """
    try:
        import yfinance as yf

        meta = yf.Ticker(ticker).get_history_metadata()
        exchange_tz = str(meta["exchangeTimezoneName"])
        pd.Timestamp.now(tz=exchange_tz)  # reject a zone this machine cannot resolve
        end = meta["currentTradingPeriod"]["regular"]["end"]
        if isinstance(end, pd.Timestamp):
            regular_end = _utc(end)
        else:
            regular_end = pd.Timestamp(int(end), unit="s", tz="UTC")
        name = meta.get("longName") or meta.get("shortName")
        traded = meta.get("regularMarketTime")
        if isinstance(traded, pd.Timestamp):
            last_trade = _utc(traded).isoformat()
        elif isinstance(traded, (int, float)) and traded > 0:
            last_trade = pd.Timestamp(int(traded), unit="s", tz="UTC").isoformat()
        else:
            last_trade = None
        return {
            "exchange_tz": exchange_tz,
            "instrument_type": str(meta.get("instrumentType") or ""),
            "regular_end": regular_end.isoformat(),
            "last_trade": last_trade,
            "name": str(name) if name else None,
        }
    except Exception:
        return None


def _read_cached_prices(cache_file: Path, meta_file: Path) -> LatestPrices | None:
    """The previous desk download for this ticker, or None when unusable."""
    try:
        df = pd.read_parquet(cache_file)
        df.index = pd.DatetimeIndex(df.index)
        if df.empty:
            return None
    except Exception:
        return None
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        downloaded_at = _utc(pd.Timestamp(meta["downloaded_at"]))
        session = meta.get("session") if isinstance(meta.get("session"), dict) else None
    except Exception:
        # No sidecar: the file's own timestamp is the best record there is.
        downloaded_at = pd.Timestamp(cache_file.stat().st_mtime, unit="s", tz="UTC")
        session = None
    return LatestPrices(df=df, downloaded_at=downloaded_at, session=session)


def fetch_latest_prices(ticker: str, cache_dir: Path | str | None = None,
                        now: pd.Timestamp | None = None) -> LatestPrices:
    """Daily history for ``ticker`` up to the newest bar Yahoo has right now.

    The desk does not trust an old price file. Prices are downloaded on every
    call unless the previous download is younger than ``_PRICE_REUSE_S``. An
    empty answer (Yahoo's response to rate limiting) is retried once. If it
    is still empty and an earlier download is on disk, that is returned with
    ``refresh_failed=True`` so the caller can say the prices may be behind.
    With nothing on disk the ``ValueError`` from ``fetch_prices`` propagates.
    """
    now = _utc(now)
    start = DataConfig().start
    root = (Path(cache_dir) if cache_dir is not None else DATA_CACHE_DIR) / _DESK_CACHE_SUBDIR
    cache_file = root / f"{ticker}_{start}_latest.parquet"
    meta_file = root / f"{ticker}_{start}_latest.meta.json"

    cached = _read_cached_prices(cache_file, meta_file)
    if cached is not None:
        age = (now - cached.downloaded_at).total_seconds()
        if 0 <= age < _PRICE_REUSE_S:
            return cached

    for attempt in (1, 2):
        try:
            df = fetch_prices(ticker, start, None, cache_dir=root, force=True)[ticker]
            break
        except ValueError:
            if attempt == 1:
                time.sleep(_RETRY_WAIT_S)
                continue
            if cached is not None:
                return replace(cached, refresh_failed=True)
            raise

    session = _fetch_session_info(ticker)
    meta_file.write_text(
        json.dumps({"downloaded_at": now.isoformat(), "session": session}, indent=2),
        encoding="utf-8",
    )
    return LatestPrices(df=df, downloaded_at=now, session=session)


def _last_bar_is_partial(ticker: str, as_of: pd.Timestamp,
                         prices: LatestPrices) -> bool | None:
    """Was the newest bar an unfinished session when it was downloaded?

    True or False when that can be established, None when it cannot. Yahoo
    dates a daily bar by the exchange's local day and includes the session in
    progress, so the question needs the exchange's own clock. With Yahoo's
    calendar that is exact (it also covers half-days). Without it, only
    plainly old bars and US-style symbols are decided; anything else is
    reported as unknown, never guessed as finished.
    """
    downloaded = prices.downloaded_at
    bar_day = as_of.date()
    session = prices.session
    if session:
        if bar_day != downloaded.tz_convert(session["exchange_tz"]).date():
            return False
        if session.get("instrument_type") == "CRYPTOCURRENCY":
            return True  # trades around the clock: today's bar is never final
        return bool(downloaded < pd.Timestamp(session["regular_end"]) + _SETTLE)

    # No calendar. A bar dated more than 36 hours before the download has
    # ended in every time zone.
    if bar_day < (downloaded - pd.Timedelta(hours=36)).date():
        return False
    if _US_STYLE_SYMBOL.fullmatch(ticker):
        ny = downloaded.tz_convert(_NY_TZ)
        if bar_day != ny.date():
            return False
        settle_minutes = int(_SETTLE.total_seconds() // 60)
        return ny.hour * 60 + ny.minute < _US_REGULAR_CLOSE_MINUTE + settle_minutes
    return None


def _last_trade_day(prices: LatestPrices) -> object | None:
    """The exchange-local date of the last trade Yahoo reports, when known."""
    session = prices.session or {}
    if not session.get("last_trade"):
        return None
    try:
        return pd.Timestamp(session["last_trade"]).tz_convert(session["exchange_tz"]).date()
    except Exception:
        return None


def _age_phrase(seconds: float) -> str:
    """A duration in the largest sensible unit, for notes."""
    if seconds < 5400:
        return f"{max(int(seconds // 60), 1)} minutes"
    if seconds < 172800:
        return f"{seconds / 3600:.0f} hours"
    return f"{seconds / 86400:.0f} days"


# ---------------------------------------------------------------------------
# live inputs
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------

@dataclass
class DeskSnapshot:
    """A market snapshot plus the facts a caller needs to judge its quality."""

    snapshot: MarketSnapshot
    as_of: str                   # date of the last bar used, "YYYY-MM-DD"
    last_close: float
    history_rows: int
    live: bool                   # fundamentals and/or news were injected
    fetched_at: str = ""         # when the prices left Yahoo, UTC "YYYY-MM-DDTHH:MMZ"
    company: str | None = None   # Yahoo's name for the symbol, when known
    # True: the last bar is an unfinished session. False: it is a close.
    # None: it could not be established (no exchange calendar).
    partial_session: bool | None = False
    stale: bool = False          # newest bar is older than a long weekend
    # Newer prices than these may exist: the download failed and an earlier
    # one is shown, or Yahoo's bar for the latest session was unusable.
    prices_behind: bool = False
    fundamentals_missing: list[str] = field(default_factory=list)
    # Everything a caller should know. The subset that changes how the data
    # must be read is also in ``snapshot.data_notes`` and so reaches the agents.
    notes: list[str] = field(default_factory=list)


def _parse_date(date: str, now: pd.Timestamp) -> pd.Timestamp:
    """A user-supplied analysis date as a tz-naive day; ValueError when unusable."""
    try:
        ts = pd.Timestamp(date)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"--date {date!r} is not a date (expected YYYY-MM-DD)") from exc
    if pd.isna(ts):
        raise ValueError(f"--date {date!r} is not a date (expected YYYY-MM-DD)")
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)  # keep the calendar day as written
    ts = ts.normalize()
    # One day of slack: an exchange ahead of UTC is already on "tomorrow".
    if ts.date() > (now + pd.Timedelta(days=1)).date():
        raise ValueError(f"--date {ts:%Y-%m-%d} is in the future")
    return ts


def build_desk_snapshot(ticker: str, date: str | None = None, live: bool = True,
                        anonymize: bool = False, lookback: int = 60,
                        df: pd.DataFrame | None = None,
                        prices: LatestPrices | None = None,
                        now: pd.Timestamp | None = None) -> DeskSnapshot:
    """Build the snapshot the agents see for ``ticker`` as of ``date``.

    ``date=None`` means the newest available bar. Fundamentals and news are
    only injected when ``live`` is set AND the snapshot is for the newest bar
    AND it is not anonymized: Yahoo serves current values only, so adding them
    to a historical date would leak the future, and company headlines defeat
    masking. Each skipped, failed or incomplete input is recorded in ``notes``
    rather than silently dropped, and the notes that change how the data must
    be read (unfinished session, stale bar, prices that could not be
    refreshed, incomplete fundamentals) are also written into the snapshot
    itself so the agents see them.

    ``prices`` (a finished download), ``df`` (a bare price frame, treated as
    downloaded at ``now``) and ``now`` exist for tests; by default prices are
    fetched and ``now`` is the current time.
    """
    ticker = normalize_ticker(ticker)
    now = _utc(now)
    if prices is None:
        if df is not None:
            prices = LatestPrices(df=df, downloaded_at=now)
        else:
            prices = fetch_latest_prices(ticker, now=now)
    df = prices.df
    if df.empty:
        raise ValueError(f"No price history for {ticker}")

    notes: list[str] = []
    data_notes: list[str] = []
    if date is None:
        history = df
    else:
        requested = _parse_date(date, now)
        history = df.loc[:requested]
        if history.empty:
            raise ValueError(
                f"No price history for {ticker} on or before {requested:%Y-%m-%d} "
                f"(data starts {df.index[0]:%Y-%m-%d})"
            )
        if history.index[-1] != requested:
            notes.append(
                f"There is no bar for the requested date {requested:%Y-%m-%d}; "
                f"the previous one, {history.index[-1]:%Y-%m-%d}, is used."
            )
    as_of = history.index[-1]
    historical = as_of < df.index[-1]

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
                fundamentals_missing = []
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

    partial: bool | None = False
    stale = False
    behind = False
    if not historical:
        session_notes: list[str] = []
        behind = prices.refresh_failed
        traded = _last_trade_day(prices)
        if traded is not None and traded > as_of.date():
            # Yahoo sometimes returns the newest row with volume but no prices;
            # the price loader drops such rows, which must not pass unremarked.
            behind = True
            session_notes.append(
                f"Yahoo reports trading on {traded:%Y-%m-%d} but returned no usable "
                f"bar for it, so this snapshot ends at {as_of:%Y-%m-%d}. Prices "
                "are at least one session behind."
            )
        if prices.refresh_failed:
            age = (now - prices.downloaded_at).total_seconds()
            session_notes.append(
                "Prices could not be refreshed (Yahoo returned nothing, which "
                "usually means it is rate-limiting). These prices were downloaded "
                f"{_age_phrase(age)} ago and newer bars may be missing."
            )
        partial = _last_bar_is_partial(ticker, as_of, prices)
        if partial:
            session_notes.append(
                f"The last bar ({as_of:%Y-%m-%d}) is an unfinished trading "
                "session. Its close is only the latest price, its volume is a "
                "part-day count that must not be compared with full days, and "
                "every indicator value will change by the close."
            )
        elif partial is None:
            session_notes.append(
                f"It could not be confirmed that the last bar ({as_of:%Y-%m-%d}) "
                "is a finished session, because the exchange calendar was "
                "unavailable. Treat its close and volume as provisional."
            )
        local_tz = prices.session["exchange_tz"] if prices.session else _NY_TZ
        age_days = (now.tz_convert(local_tz).date() - as_of.date()).days
        stale = age_days > _STALE_AFTER_DAYS
        if stale:
            session_notes.append(
                f"The newest available bar is {age_days} days old ({as_of:%Y-%m-%d}). "
                "The symbol may be halted, delisted or illiquid; treat the "
                "snapshot as stale."
            )
        notes += session_notes
        data_notes = session_notes + data_notes

    snapshot = snapshot_from_history(
        df, as_of, ticker, lookback=lookback, anonymize=anonymize,
        fundamentals=fundamentals, news=news,
    )
    if data_notes:
        snapshot = replace(snapshot, data_notes=data_notes)
    company = prices.session.get("name") if prices.session else None
    return DeskSnapshot(
        snapshot=snapshot,
        as_of=as_of.strftime("%Y-%m-%d"),
        last_close=float(history["close"].iloc[-1]),
        history_rows=len(history),
        live=bool(fundamentals or news),
        fetched_at=prices.downloaded_at.strftime("%Y-%m-%dT%H:%MZ"),
        company=None if anonymize else company,
        partial_session=partial,
        stale=stale,
        prices_behind=behind,
        fundamentals_missing=fundamentals_missing,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def snapshot_to_dict(desk: DeskSnapshot) -> dict:
    """JSON-serializable view of a desk snapshot.

    ``context`` is the exact text block the agents receive. When the snapshot
    is anonymized, ``last_close`` and ``company`` are withheld so nothing that
    identifies the stock sits next to the masked block.
    """
    snap = desk.snapshot
    return {
        "ticker": snap.ticker,
        "company": desk.company,
        "as_of": desk.as_of,
        "fetched_at": desk.fetched_at,
        "last_close": None if snap.anonymize else round(desk.last_close, 4),
        "partial_session": desk.partial_session,
        "stale": desk.stale,
        "prices_behind": desk.prices_behind,
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
    return "no trade; a prior position, if any, is left as it is"


def price_phrase(desk: DeskSnapshot) -> str:
    """How to describe ``last_close``: a close, or a mid-session price."""
    if desk.snapshot.anonymize:
        return "close masked"
    if desk.partial_session:
        return f"last price {desk.last_close:.2f}, session still open"
    if desk.partial_session is None:
        return f"last price {desk.last_close:.2f}, session status unknown"
    return f"close {desk.last_close:.2f}"


def decision_to_dict(log: AgentDayLog, desk: DeskSnapshot,
                     usage: dict | None = None,
                     cost_usd: float | None = None) -> dict:
    """JSON-serializable record of one desk decision and its full reasoning."""
    return {
        "ticker": log.ticker,
        "company": desk.company,
        "as_of": desk.as_of,
        "fetched_at": desk.fetched_at,
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
        "partial_session": desk.partial_session,
        "stale": desk.stale,
        "prices_behind": desk.prices_behind,
        "live": desk.live,
        "anonymized": desk.snapshot.anonymize,
        "notes": list(desk.notes),
        "usage": usage or {},
        "cost_usd": cost_usd,
        "disclaimer": DISCLAIMER,
    }


def render_decision_card(log: AgentDayLog, desk: DeskSnapshot) -> str:
    """Short plain-text summary of a desk decision for terminal output."""
    size = float(log.final_size)
    lines = [
        f"=== AI trading desk - {log.ticker} - as of {desk.as_of} ({price_phrase(desk)}) ===",
        f"FINAL DECISION:   {log.final_action} {size:.2f}  "
        f"({exposure_phrase(log.final_action, size)})",
        f"Research verdict: {log.debate.verdict} - {log.debate.rationale}",
        f"Trader proposal:  {log.decision.action} {float(log.decision.size):.2f} - "
        f"{log.decision.rationale}",
        f"Fund manager:     {'approved' if log.risk.approved else 'REJECTED'} - "
        f"{log.risk.rationale}",
    ]
    if desk.notes:
        lines.append("Data notes:")
        lines += [f"  - {note}" for note in desk.notes]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)
