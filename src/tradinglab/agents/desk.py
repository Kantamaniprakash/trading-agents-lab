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
import os
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

# A newest bar older than this many calendar days is flagged as stale, which
# also stops the paid desk. The longest scheduled exchange closures (Lunar New
# Year in Shanghai and Taipei) run to about twelve days, so anything older has
# no innocent explanation. Between the two thresholds the age is only noted:
# it may be a holiday week, or the symbol may be halted.
_STALE_AFTER_DAYS = 12
_OLD_BAR_NOTE_DAYS = 5
# Yahoo rate-limits bursts and then answers with empty payloads rather than
# errors. One short pause and a second attempt recovers most of those.
_RETRY_WAIT_S = 6.0
# A price download this recent is reused, so an immediate re-run (for example
# the skill's "fetch once more" step) does not hit Yahoo for prices again.
_PRICE_REUSE_S = 15 * 60
# After the regular close, the closing print takes a few minutes to settle.
_SETTLE = pd.Timedelta(minutes=15)
# Yahoo's quotes for many venues run 10 to 20 minutes behind. Until this long
# after the close, a last trade stamped before the close may just be the delay.
_MAX_FEED_DELAY = pd.Timedelta(minutes=30)
# If trades are still arriving after the calendar's close, the calendar is
# wrong for that session. Once none has arrived for this long, it is over.
_FEED_QUIET = pd.Timedelta(minutes=45)
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
    # "instrument_type", "currency", "regular_end" (UTC ISO), "last_trade"
    # (UTC ISO or None), "name"}; None when Yahoo's chart metadata could not
    # be read.
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
        currency = meta.get("currency")
        return {
            "exchange_tz": exchange_tz,
            "instrument_type": str(meta.get("instrumentType") or ""),
            "currency": str(currency) if currency else None,
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
        # The sidecar must describe THIS price file. A run interrupted between
        # the two writes leaves new prices beside an old sidecar, whose download
        # time and calendar would then be applied to the wrong bar.
        if meta["last_bar"] != df.index[-1].strftime("%Y-%m-%d"):
            raise ValueError("sidecar belongs to a different download")
        downloaded_at = _utc(pd.Timestamp(meta["downloaded_at"]))
        session = meta.get("session") if isinstance(meta.get("session"), dict) else None
    except Exception:
        # No usable sidecar: the file's own timestamp is the best record there is.
        downloaded_at = pd.Timestamp(cache_file.stat().st_mtime, unit="s", tz="UTC")
        session = None
    return LatestPrices(df=df, downloaded_at=downloaded_at, session=session)


def _write_sidecar(meta_file: Path, df: pd.DataFrame, downloaded_at: pd.Timestamp,
                   session: dict | None) -> None:
    """Record when ``df`` was downloaded and the exchange facts that came with it.

    Written through a temporary file so a reader never sees half a document.
    The cache is a convenience: if it cannot be written the sidecar is removed
    (no sidecar is safe, a stale one is not) and the run carries on.
    """
    payload = {
        "downloaded_at": downloaded_at.isoformat(),
        "last_bar": df.index[-1].strftime("%Y-%m-%d"),
        "session": session,
    }
    tmp = meta_file.with_name(meta_file.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, meta_file)
    except OSError:
        for leftover in (tmp, meta_file):
            try:
                leftover.unlink()
            except OSError:
                pass


def fetch_latest_prices(ticker: str, cache_dir: Path | str | None = None,
                        now: pd.Timestamp | None = None) -> LatestPrices:
    """Daily history for ``ticker`` up to the newest bar Yahoo has right now.

    The desk does not trust an old price file. Prices are downloaded on every
    call unless the previous download is younger than ``_PRICE_REUSE_S`` and
    is not already known to be missing the newest session. An empty answer
    (Yahoo's response to rate limiting) is retried once. If it is still empty
    and an earlier download is on disk, that is returned with
    ``refresh_failed=True`` so the caller can say the prices may be behind.
    With nothing on disk, or when Yahoo says the symbol does not exist, the
    ``ValueError`` from ``fetch_prices`` propagates.
    """
    now = _utc(now)
    start = DataConfig().start
    root = (Path(cache_dir) if cache_dir is not None else DATA_CACHE_DIR) / _DESK_CACHE_SUBDIR
    cache_file = root / f"{ticker}_{start}_latest.parquet"
    meta_file = root / f"{ticker}_{start}_latest.meta.json"

    cached = _read_cached_prices(cache_file, meta_file)
    if cached is not None:
        age = (now - cached.downloaded_at).total_seconds()
        traded = _last_trade_day(cached)
        behind = traded is not None and traded > cached.df.index[-1].date()
        # age < 0 is a download stamped in the future (clock changed, cache
        # copied between machines): its timestamp says nothing, so refresh.
        if 0 <= age < _PRICE_REUSE_S and not behind:
            return cached

    for attempt in (1, 2):
        try:
            df = fetch_prices(ticker, start, None, cache_dir=root, force=True)[ticker]
            break
        except ValueError as exc:
            if getattr(exc, "reason", "") == "not_found":
                raise  # a retry cannot make an unknown symbol exist
            if attempt == 1:
                time.sleep(_RETRY_WAIT_S)
                continue
            if cached is not None:
                return replace(cached, refresh_failed=True)
            raise

    # Stamp the new prices at once, so an interruption during the metadata
    # request below cannot leave them paired with the previous run's sidecar.
    _write_sidecar(meta_file, df, now, None)
    session = _fetch_session_info(ticker)
    if session is not None:
        _write_sidecar(meta_file, df, now, session)
    return LatestPrices(df=df, downloaded_at=now, session=session)


def _last_bar_is_partial(ticker: str, as_of: pd.Timestamp,
                         prices: LatestPrices) -> tuple[bool | None, str]:
    """Was the newest bar an unfinished session when it was downloaded?

    Returns ``(state, reason)``. ``state`` is True or False when that can be
    established and None when it cannot; ``reason`` then says why:
    ``"no_calendar"`` or ``"calendar_contradicted"``.

    Yahoo dates a daily bar by the exchange's local day and includes the
    session in progress, so the question needs the exchange's own clock.
    Yahoo's calendar supplies it, including half-days, but it is not gospel:
    it has been seen an hour early for a venue, with trades still printing.
    The last-trade time from the same response is therefore used as a
    cross-check. Without a calendar, only plainly old bars and US-style
    symbols are decided. Anything else is reported as unknown, never guessed
    as finished.
    """
    downloaded = prices.downloaded_at
    bar_day = as_of.date()
    session = prices.session
    if session:
        local_day = downloaded.tz_convert(session["exchange_tz"]).date()
        if bar_day < local_day:
            return False, ""
        if bar_day > local_day:
            return True, ""  # a bar that began after the download started
        if session.get("instrument_type") == "CRYPTOCURRENCY":
            return True, ""  # trades around the clock: today's bar is never final
        end = pd.Timestamp(session["regular_end"])
        if downloaded < end + _SETTLE:
            return True, ""
        last = pd.Timestamp(session["last_trade"]) if session.get("last_trade") else None
        if last is None:
            return False, ""
        if last > end + _SETTLE:
            # Trades after the calendar's close: the calendar is wrong for
            # this session. Still arriving means unknown; gone quiet means over.
            if downloaded - last < _FEED_QUIET:
                return None, "calendar_contradicted"
            return False, ""
        if last >= end:
            return False, ""  # the closing print is in hand
        # Last trade before the close: either a quiet stock or a delayed feed.
        return bool(downloaded < end + _SETTLE + _MAX_FEED_DELAY), ""

    # No calendar. A bar dated more than 36 hours before the download has
    # ended in every time zone.
    if bar_day < (downloaded - pd.Timedelta(hours=36)).date():
        return False, ""
    if _US_STYLE_SYMBOL.fullmatch(ticker):
        ny = downloaded.tz_convert(_NY_TZ)
        if bar_day < ny.date():
            return False, ""
        if bar_day > ny.date():
            return None, "no_calendar"  # only a wrong clock gets here
        settle_minutes = int(_SETTLE.total_seconds() // 60)
        return ny.hour * 60 + ny.minute < _US_REGULAR_CLOSE_MINUTE + settle_minutes, ""
    return None, "no_calendar"


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
    """How long ago, in the largest sensible unit, for notes."""
    if seconds < 0:
        return "at an unknown time (its saved timestamp is in the future)"

    def plural(count: int, unit: str) -> str:
        return f"{count} {unit}{'' if count == 1 else 's'} ago"

    if seconds < 5400:
        return plural(max(int(seconds // 60), 1), "minute")
    if seconds < 172800:
        return plural(round(seconds / 3600), "hour")
    return plural(round(seconds / 86400), "day")


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
    # Fundamentals and news were asked for (newest bar, not masked, not
    # switched off). ``live_attempted and not live`` is the rate-limit signature.
    live_attempted: bool = False
    fetched_at: str = ""         # when the prices left Yahoo, UTC "YYYY-MM-DDTHH:MMZ"
    company: str | None = None   # Yahoo's name for the symbol, when known
    instrument_type: str | None = None   # Yahoo's label: EQUITY, ETF, CRYPTOCURRENCY ...
    currency: str | None = None  # what prices and fundamentals are quoted in
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
    live_attempted = False
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

        live_attempted = True
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
    session = prices.session or {}
    currency = session.get("currency")
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
                f"{_age_phrase(age)} and newer bars may be missing."
            )
        partial, reason = _last_bar_is_partial(ticker, as_of, prices)
        if partial:
            session_notes.append(
                f"The last bar ({as_of:%Y-%m-%d}) is an unfinished trading "
                "session. Its close is only the latest price, its volume is a "
                "part-day count that must not be compared with full days, and "
                "every indicator value will change by the close."
            )
        elif partial is None and reason == "calendar_contradicted":
            session_notes.append(
                f"Yahoo's calendar says the session of the last bar ({as_of:%Y-%m-%d}) "
                "has ended, but it was still reporting trades after that time. "
                "The session may still be open: treat the close and volume as "
                "provisional."
            )
        elif partial is None:
            session_notes.append(
                f"It could not be confirmed that the last bar ({as_of:%Y-%m-%d}) "
                "is a finished session, because the exchange calendar was "
                "unavailable. Treat its close and volume as provisional."
            )
        local_tz = session.get("exchange_tz") or _NY_TZ
        age_days = (now.tz_convert(local_tz).date() - as_of.date()).days
        stale = age_days > _STALE_AFTER_DAYS
        if stale:
            session_notes.append(
                f"The newest available bar is {age_days} days old ({as_of:%Y-%m-%d}). "
                "No exchange holiday is that long: the symbol is probably halted, "
                "delisted or illiquid. Treat the snapshot as stale."
            )
        elif age_days > _OLD_BAR_NOTE_DAYS:
            session_notes.append(
                f"The newest available bar is {age_days} days old ({as_of:%Y-%m-%d}). "
                "That is normal during a long exchange holiday; otherwise the "
                "symbol may be halted."
            )
        if currency and currency != "USD" and not anonymize:
            session_notes.append(f"Prices and fundamentals are quoted in {currency}.")
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
        live_attempted=live_attempted,
        fetched_at=prices.downloaded_at.strftime("%Y-%m-%dT%H:%MZ"),
        company=None if anonymize else session.get("name"),
        instrument_type=session.get("instrument_type") or None,
        currency=None if anonymize else currency,
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
    is anonymized, ``last_close``, ``company`` and ``currency`` are withheld
    so nothing that identifies the stock sits next to the masked block.
    """
    snap = desk.snapshot
    return {
        "ticker": snap.ticker,
        "company": desk.company,
        "instrument_type": desk.instrument_type,
        "currency": desk.currency,
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
        "instrument_type": desk.instrument_type,
        "currency": desk.currency,
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
