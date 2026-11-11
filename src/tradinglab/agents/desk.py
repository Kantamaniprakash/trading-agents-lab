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
import math
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
# The only calendar error seen is one hour (a venue whose daylight-saving
# rule Yahoo had wrong). A last trade stamped later than that after the
# calendar's close is a late-reported print, not a session still running.
_MAX_CALENDAR_ERROR = pd.Timedelta(hours=1)
# A last price this far outside the day's range, as a fraction of the bound,
# is rounding. Further out it is not that day's close.
_RANGE_TOLERANCE = 1e-4
_BAR_COLUMNS = ("open", "high", "low", "close", "volume")
# Instrument types that are not checked for a session missing behind the bar
# in progress. For currencies and futures Yahoo lists a holiday as an empty
# daily row, so a row without a bar is no proof that a session was lost. A
# cryptocurrency is left out as it is from the late-close fill.
_NO_GAP_CHECK = frozenset({"CURRENCY", "FUTURE", "CRYPTOCURRENCY"})
# Quote currencies that are a hundredth of the currency people mean.
_MINOR_UNITS = {"GBp": ("pence", "GBP"), "GBX": ("pence", "GBP"),
                "ZAc": ("cents", "ZAR"), "ILA": ("agorot", "ILS")}
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
    # (UTC ISO or None), "last_price" (the price of that trade, or None),
    # "name"}; None when Yahoo's chart metadata could not be read.
    session: dict | None = None
    # True when Yahoo returned nothing now and ``df`` is an earlier download.
    refresh_failed: bool = False
    # True when only the second look at an undecided bar failed and ``df`` is
    # the first look, minutes old: not behind, but the caller must be told.
    recheck_failed: bool = False
    # Set when Yahoo's daily row for a finished session had no close yet and
    # the last regular-market price was used instead: that bar, as {"date",
    # "open", "high", "low", "close", "volume"}. ``df`` already holds it, as
    # its newest bar or, when a later download carried the fill over, just
    # before the bar of the session in progress.
    late_close: dict | None = None
    # Set when Yahoo lists a session between the last two usable bars, the
    # newer of them unfinished, and sent no usable bar for it: {"missing":
    # ["YYYY-MM-DD", ...], "left_out": "YYYY-MM-DD"}. ``df`` then ends at the
    # last complete session; the unfinished bar (``left_out``) is not in it.
    gap: dict | None = None


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
        price = meta.get("regularMarketPrice")
        priced = isinstance(price, (int, float)) and math.isfinite(price)
        return {
            "exchange_tz": exchange_tz,
            "instrument_type": str(meta.get("instrumentType") or ""),
            "currency": str(currency) if currency else None,
            "regular_end": regular_end.isoformat(),
            "last_trade": last_trade,
            "last_price": float(price) if priced else None,
            "name": str(name) if name else None,
        }
    except Exception:
        return None


def _fetch_raw_tail(ticker: str, since: pd.Timestamp) -> pd.DataFrame | None:
    """Yahoo's daily rows from ``since`` on as it sends them: unadjusted, none dropped.

    The normal download adjusts prices by the ratio of adjusted close to
    close, so a row without a close leaves it with no open, high or low
    either, and the price loader then drops the row. This is the only way to
    see which sessions Yahoo lists and what it did send for them. Costs one
    request, and a download makes at most one: when the newest session is
    missing, or when a session may be missing behind an unfinished one.
    Returns None on any failure.
    """
    try:
        import yfinance as yf

        # keepna: yfinance otherwise leaves out a row that is all nulls and
        # zeros, and a day Yahoo sent empty would not be seen as missing.
        raw = yf.Ticker(ticker).history(start=f"{since:%Y-%m-%d}", interval="1d",
                                        auto_adjust=False, actions=False, keepna=True)
        raw = raw.rename(columns=lambda c: str(c).strip().lower())
        tail = raw.loc[:, list(_BAR_COLUMNS)].astype(float)
        idx = pd.DatetimeIndex(tail.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        tail.index = idx.normalize()
        return tail.sort_index()
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
        # the two writes, or one whose price file could not be written, leaves
        # a sidecar beside prices it does not belong to, and its download time
        # and calendar would then be applied to the wrong bar.
        if meta["fingerprint"] != _fingerprint(df):
            raise ValueError("sidecar belongs to a different download")
        downloaded_at = _utc(pd.Timestamp(meta["downloaded_at"]))
        session = meta.get("session") if isinstance(meta.get("session"), dict) else None
        # Sidecars written before late closes were filled, or before missing
        # sessions were looked for, have no such fields.
        late_close = meta.get("late_close")
        gap = meta.get("gap")
        usable = df if late_close is None else _with_late_close(df, late_close)
        if gap is not None:
            usable = _before_gap(usable, gap)
    except Exception:
        # No usable sidecar: the file's own timestamp is the best record there is.
        downloaded_at = pd.Timestamp(cache_file.stat().st_mtime, unit="s", tz="UTC")
        session = late_close = gap = None
        usable = df
    return LatestPrices(df=usable, downloaded_at=downloaded_at, session=session,
                        late_close=late_close, gap=gap)


def _fingerprint(df: pd.DataFrame) -> list:
    """Identifies one download: its length and its newest row.

    The date alone is not enough, because two downloads on the same day can
    hold different readings of the same unfinished bar.
    """
    last = df.iloc[-1]
    return [len(df), df.index[-1].strftime("%Y-%m-%d"), float(last["close"]),
            float(last["volume"])]


def _write_sidecar(meta_file: Path, df: pd.DataFrame, downloaded_at: pd.Timestamp,
                   session: dict | None, late_close: dict | None = None,
                   gap: dict | None = None) -> None:
    """Record when ``df`` was downloaded and the exchange facts that came with it.

    ``df`` is the frame as it sits in the price file. A late-close bar is
    kept here and never written into that file, so prices cannot hold a
    filled close without the record that says so. A missing session is
    recorded here too, and the price file keeps the unfinished bar the view
    leaves out: the file stays exactly what Yahoo delivered.

    Written through a temporary file so a reader never sees half a document.
    The cache is a convenience: if it cannot be written the sidecar is removed
    (no sidecar is safe, a stale one is not) and the run carries on.
    """
    payload = {
        "downloaded_at": downloaded_at.isoformat(),
        "fingerprint": _fingerprint(df),
        "session": session,
        "late_close": late_close,
        "gap": gap,
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
    is neither known to be missing the newest session nor undecided about
    whether its last bar is final. An empty answer
    (Yahoo's response to rate limiting) is retried once. If it is still empty
    and an earlier download is on disk, that is returned with
    ``refresh_failed=True`` so the caller can say the prices may be behind
    (or with ``recheck_failed=True`` when only a second look inside the reuse
    window failed and the prices are still minutes old).
    With nothing on disk, or when Yahoo says the symbol does not exist, the
    ``ValueError`` from ``fetch_prices`` propagates.

    When Yahoo's daily row for the newest, finished session has no close yet,
    the bar is completed with Yahoo's last regular-market price (see
    ``_late_close_bar``) and returned in ``late_close`` so the caller says so.

    When the newest usable bar is an unfinished session and Yahoo lists a
    session just behind it that it sent no usable bar for (see
    ``_gap_behind_open_session``), the frame is cut back to the last complete
    session and the missing dates are returned in ``gap``, unless an earlier
    download filled that very session, in which case its bar is put back.
    Either way Yahoo's unadjusted rows are read at most once per download.
    """
    now = _utc(now)
    start = DataConfig().start
    root = (Path(cache_dir) if cache_dir is not None else DATA_CACHE_DIR) / _DESK_CACHE_SUBDIR
    cache_file = root / f"{ticker}_{start}_latest.parquet"
    meta_file = root / f"{ticker}_{start}_latest.meta.json"

    cached = _read_cached_prices(cache_file, meta_file)
    fresh_enough = False
    if cached is not None:
        age = (now - cached.downloaded_at).total_seconds()
        traded = _last_trade_day(cached)
        behind = cached.gap is not None or (
            traded is not None and traded > cached.df.index[-1].date())
        # A download whose session state could not be decided is not reused
        # either: a second look is exactly what settles it.
        undecided = _last_bar_is_partial(ticker, cached.df.index[-1], cached)[0] is None
        # age < 0 is a download stamped in the future (clock changed, cache
        # copied between machines): its timestamp says nothing, so refresh.
        fresh_enough = 0 <= age < _PRICE_REUSE_S and not behind
        if fresh_enough and not undecided:
            return cached

    for attempt in (1, 2):
        try:
            df = fetch_prices(ticker, start, None, cache_dir=root, force=True)[ticker]
            break
        except ValueError as exc:
            if getattr(exc, "reason", "") == "not_found":
                # Yahoo itself said the symbol does not exist. A retry cannot
                # change that, and showing an earlier download would hide it.
                raise
            if attempt == 1:
                time.sleep(_RETRY_WAIT_S)
                continue
            if cached is not None:
                if fresh_enough:
                    # Only a second look at an undecided bar failed. The
                    # prices are minutes old: still undecided, not behind.
                    return replace(cached, recheck_failed=True)
                return replace(cached, refresh_failed=True)
            raise

    # Stamp the new prices at once, so an interruption during the metadata
    # request below cannot leave them paired with the previous run's sidecar.
    _write_sidecar(meta_file, df, now, None)
    session = _fetch_session_info(ticker)
    late_close = gap = None
    probe = LatestPrices(df=df, downloaded_at=now, session=session)
    # Yahoo's unadjusted rows are read at most once per download. The newest
    # usable bar is either a session in progress, which can hide a missing
    # session behind it, or it is not, and then the newest session may only
    # lack its close. (``_late_close_bar`` refuses whenever that bar is
    # unfinished, so nothing is lost by not asking it.)
    if session is not None and _last_bar_is_partial(ticker, df.index[-1], probe)[0] is True:
        late_close, gap = _gap_behind_open_session(ticker, df, session, now, cached)
    else:
        late_close = _late_close_bar(ticker, df, session, now)
    if session is not None:
        _write_sidecar(meta_file, df, now, session, late_close, gap)
    if late_close is not None:
        df = _with_late_close(df, late_close)
    if gap is not None:
        df = _before_gap(df, gap)
    return LatestPrices(df=df, downloaded_at=now, session=session, late_close=late_close,
                        gap=gap)


def _late_close_bar(ticker: str, df: pd.DataFrame, session: dict | None,
                    downloaded_at: pd.Timestamp) -> dict | None:
    """The newest session's bar when Yahoo's daily history has no close for it yet.

    Seen daily on London and Frankfurt shares: for many hours after the close
    the newest daily row has open, high, low and volume but no close, while
    the chart metadata already carries the last regular-market price. The
    price loader drops the row, so the snapshot ends one session early.

    Returns the bar as ``{"date", "open", "high", "low", "close", "volume"}``
    with that price as its close, or None unless all of this holds: the last
    trade is dated (exchange time) after the newest usable bar; that session
    is over by the rules of ``_last_bar_is_partial``; the instrument is not a
    cryptocurrency; Yahoo's unadjusted row for that date is its newest (an
    empty row for a session not yet begun aside), follows the newest usable
    bar with no row in between, empty or not, and lacks only the close; that
    usable bar reads the same adjusted and unadjusted; and the price lies
    within the row's low-to-high range. Otherwise nothing changes: the
    snapshot ends at the newest usable bar and says it is a session behind.
    """
    if not session or session.get("instrument_type") == "CRYPTOCURRENCY":
        return None
    price = session.get("last_price")
    if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        return None
    probe = LatestPrices(df=df, downloaded_at=downloaded_at, session=session)
    traded = _last_trade_day(probe)
    if traded is None or traded <= df.index[-1].date():
        return None
    day = pd.Timestamp(traded)
    if _last_bar_is_partial(ticker, day, probe)[0] is not False:
        return None  # still trading, or undecided: never filled
    tail = _fetch_raw_tail(ticker, df.index[-1])
    if tail is None:
        return None
    # Yahoo may already list the session about to open, with nothing in it.
    # That row is overlooked. An empty row before the trade day stays: it is
    # a missing session, and the checks below then refuse.
    empty = (tail.isna() | (tail == 0)).all(axis=1)
    tail = tail[~(empty & (tail.index > day))]
    if len(tail) < 2 or tail.index[-1] != day or tail.index[-2] != df.index[-1]:
        return None
    before, bar = tail.iloc[-2], tail.iloc[-1]
    # The raw row is unadjusted. It belongs in this adjusted series only if
    # the bar before it reads the same in both; a dividend that is due or
    # just applied breaks that, and the row is then left out as before. (A
    # split cannot be seen here: Yahoo restates both closes for it together.)
    if not math.isclose(before["close"], df["close"].iloc[-1], rel_tol=1e-6):
        return None
    if not pd.isna(bar["close"]):
        return None
    filled = {"date": f"{day:%Y-%m-%d}", "open": float(bar["open"]),
              "high": float(bar["high"]), "low": float(bar["low"]), "close": float(price),
              "volume": float(bar["volume"])}
    return filled if _is_whole_bar(filled) else None


def _is_whole_bar(bar: dict) -> bool:
    """Positive open, high, low, close and volume, with the close in the day's range.

    yfinance writes a missing volume as 0, so a zero counts as missing. A
    close up to ``_RANGE_TOLERANCE`` outside low-to-high is rounding.
    """
    try:
        values = {column: float(bar[column]) for column in _BAR_COLUMNS}
    except (KeyError, TypeError, ValueError):
        return False
    if not all(math.isfinite(value) and value > 0 for value in values.values()):
        return False
    return (values["low"] * (1 - _RANGE_TOLERANCE) <= values["close"]
            <= values["high"] * (1 + _RANGE_TOLERANCE))


def _with_late_close(df: pd.DataFrame, bar: dict) -> pd.DataFrame:
    """``df`` with a late-close bar put in at its date; raises when the record does not fit.

    The bar goes after the newest bar (the session Yahoo has not closed yet)
    or between the last two (a fill carried over into the next session).
    """
    day = pd.Timestamp(bar["date"])
    values = {column: float(bar[column]) for column in _BAR_COLUMNS}
    newest = day > df.index[-1]
    carried = len(df) > 1 and df.index[-2] < day < df.index[-1]
    if not (newest or carried) or not all(math.isfinite(value) for value in values.values()):
        raise ValueError("late-close record does not fit these prices")
    index = pd.DatetimeIndex([day], name=df.index.name).astype(df.index.dtype)
    row = pd.DataFrame(values, index=index)[list(df.columns)]
    # yfinance names the column axis and the price table prints that name;
    # a row without it would make concat drop it.
    row.columns = df.columns
    row = row.astype(df.dtypes.to_dict())
    return pd.concat([df, row] if newest else [df.iloc[:-1], row, df.iloc[-1:]])


def _gap_behind_open_session(ticker: str, df: pd.DataFrame, session: dict,
                             downloaded_at: pd.Timestamp, earlier: LatestPrices | None,
                             ) -> tuple[dict | None, dict | None]:
    """A session Yahoo lists just behind the unfinished newest bar but sent no bar for.

    Seen on London and Frankfurt shares for about half an hour after the
    open: the row of the session that finished the evening before is empty
    while the session in progress already has values. The price loader drops
    the empty row, so the frame runs from the session before straight to the
    unfinished bar, and the one-day change and every indicator are computed
    across a day that is not there.

    Called only when the newest usable bar is an unfinished session. Yahoo's
    unadjusted rows are asked for (one request) only when a calendar day lies
    between the last two usable bars: no session can hide between two
    consecutive days. Returns ``(late_close, gap)``, at most one of them set.
    ``gap`` names every date Yahoo lists strictly between the two bars,
    whatever its row holds: each is a session these prices do not have. A
    holiday is not listed for a share, a fund or an index, so it is not
    reported; where Yahoo does list holidays (``_NO_GAP_CHECK``) nothing is
    looked for. ``late_close`` is the bar an earlier download filled for that
    one date (``_carried_fill``), which closes the gap instead of reporting
    it. ``(None, None)`` when nothing is missing or Yahoo could not be read.
    """
    if session.get("instrument_type") in _NO_GAP_CHECK or len(df) < 2:
        return None, None
    before, newest = df.index[-2].normalize(), df.index[-1].normalize()
    if (newest - before).days < 2:
        return None, None
    tail = _fetch_raw_tail(ticker, before)
    if tail is None:
        return None, None
    listed = tail.index[(tail.index > before) & (tail.index < newest)].unique()
    if listed.empty:
        return None, None
    carried = _carried_fill(ticker, df, tail, listed, session, downloaded_at, earlier)
    if carried is not None:
        return carried, None
    return None, {"missing": [f"{day:%Y-%m-%d}" for day in listed],
                  "left_out": f"{newest:%Y-%m-%d}"}


def _carried_fill(ticker: str, df: pd.DataFrame, tail: pd.DataFrame,
                  listed: pd.DatetimeIndex, session: dict, downloaded_at: pd.Timestamp,
                  earlier: LatestPrices | None) -> dict | None:
    """The bar an earlier download filled, when it is the one session missing here.

    Before the open the desk may have completed yesterday's bar from the last
    regular-market price (``_late_close_bar``). Once the next session trades,
    that price is today's and Yahoo's row for yesterday can be empty, so the
    bar cannot be built again: the record in the earlier sidecar is the only
    copy. It is put back under the guards the fill itself uses, read for a
    bar that is no longer the newest: the record is for the single date Yahoo
    lists between the last two usable bars; it is a whole bar with its close
    in its own range; that session is over; Yahoo still has no close for it
    (a close Yahoo has is the one to show, and a fresh download brings it);
    the usable bar before it reads the same adjusted and unadjusted; and that
    bar is the one the record followed, unchanged since, so nothing was
    restated in between. Otherwise None, and the gap is reported.
    """
    record = earlier.late_close if earlier is not None else None
    if not record or len(listed) != 1:
        return None
    day = listed[0]
    if record.get("date") != f"{day:%Y-%m-%d}" or not _is_whole_bar(record):
        return None
    probe = LatestPrices(df=df, downloaded_at=downloaded_at, session=session)
    if _last_bar_is_partial(ticker, day, probe)[0] is not False:
        return None
    before, close_before = df.index[-2], float(df["close"].iloc[-2])
    row, row_before = tail[tail.index == day], tail[tail.index == before.normalize()]
    if len(row) != 1 or not pd.isna(row["close"].iloc[0]):
        return None
    # The record is unadjusted, like the row it was built from. It belongs in
    # this adjusted series only if the bar before it reads the same in both.
    if len(row_before) != 1 or not math.isclose(row_before["close"].iloc[0], close_before,
                                                rel_tol=1e-6):
        return None
    # A split restates adjusted and unadjusted closes together, which the
    # check above cannot see. The earlier download can: it holds the bar the
    # record followed as it read when the record was made.
    held = earlier.df
    at = held.index.get_indexer([day])[0]
    if at < 1 or held.index[at - 1] != before or not math.isclose(
            float(held["close"].iloc[at - 1]), close_before, rel_tol=1e-6):
        return None
    return {"date": f"{day:%Y-%m-%d}",
            **{column: float(record[column]) for column in _BAR_COLUMNS}}


def _before_gap(df: pd.DataFrame, gap: dict) -> pd.DataFrame:
    """``df`` cut back to the last complete session; raises when the record does not fit."""
    left_out = pd.Timestamp(gap["left_out"])
    missing = [pd.Timestamp(day) for day in gap["missing"]]
    if (len(df) < 2 or df.index[-1].normalize() != left_out or not missing
            or not all(df.index[-2].normalize() < day < left_out for day in missing)):
        raise ValueError("gap record does not fit these prices")
    return df.iloc[:-1]


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
    cross-check (a late stamp is ignored for indices, whose closes are
    published about an hour after the bell). Without a calendar, only plainly old bars and US-style
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
            return False, ""  # nothing to cross-check with
        if last > end + _SETTLE:
            # A stamp after the calendar's close. On an index it says nothing:
            # Yahoo publishes index closes about an hour after the bell. (Cost:
            # an index on a venue whose calendar is early is called a close.)
            # Far after the close it is a print reported late, common in London.
            if (session.get("instrument_type") == "INDEX"
                    or last > end + _MAX_CALENDAR_ERROR + _SETTLE):
                return False, ""
            # Within an hour: the calendar may be early and the session still
            # running. Still arriving means unknown; gone quiet means over.
            if downloaded - last < _FEED_QUIET:
                return None, "calendar_contradicted"
            return False, ""
        if last >= end:
            # The closing print is in hand. Known limit: where the calendar is
            # an hour early, a trade stamped in the 15 minutes after its close
            # lands here and the bar is called final while the session still
            # has most of an hour to run. With a delayed feed that lasts up to
            # 15 minutes; for a thinly traded symbol, as long as its latest
            # trade sits in that window.
            return False, ""
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
    currency: str | None = None  # what the prices are quoted in (statements may differ)
    # True: the last bar is an unfinished session. False: it is a close.
    # None: it could not be established (no exchange calendar).
    partial_session: bool | None = False
    stale: bool = False          # newest bar is older than a long weekend
    # Newer prices than these may exist: the download failed and an earlier
    # one is shown, Yahoo's bar for the latest session was unusable, or a
    # session just behind the one in progress had no usable bar and the view
    # ends at the last complete session.
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
    refreshed, a session with no usable bar, a close taken from the last
    regular-market price, incomplete fundamentals) are also written into the
    snapshot itself so the agents see them.

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
                    kind = (prices.session or {}).get("instrument_type") or ""
                    if kind and kind != "EQUITY":
                        why = f" That is normal for this kind of instrument ({kind})."
                    else:
                        why = (" Some foreign listings lack these fields; otherwise "
                               "Yahoo is probably rate-limiting and a re-run in a "
                               "minute fills them in.")
                    notes.append(incomplete + why)
                reported_in = raw.get("financial_currency")
                priced_in = (prices.session or {}).get("currency")
                major = _MINOR_UNITS.get(priced_in, ("", priced_in))[1]
                if reported_in and major and reported_in != major:
                    mixed = (
                        f"The company reports its statements in {reported_in}, not in "
                        f"the currency the shares trade in ({major}). Revenue and net "
                        "income are in the statement currency."
                    )
                    data_notes.append(mixed)
                    notes.append(mixed)
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
    session_notes: list[str] = []
    late = prices.late_close
    filled_day = pd.Timestamp(late.get("date")) if late else pd.NaT
    # Said wherever the filled bar is in view. It is the newest bar, or, once
    # carried over into the next session, the one before it, which a past
    # date can then land on. (No record compares as not in view.)
    if filled_day <= as_of:
        # A masked snapshot must not carry a real price level.
        shown = "" if anonymize else f" ({float(late['close']):.10g})"
        session_notes.append(
            f"The close for {filled_day:%Y-%m-%d} is Yahoo's last regular-market "
            f"price{shown}, because Yahoo's daily bar for that session had no "
            "close yet. The close Yahoo publishes later may differ slightly."
        )
    if not historical:
        behind = prices.refresh_failed
        gap = prices.gap
        traded = _last_trade_day(prices)
        if gap:
            # The frame already ends at the last complete session. Yahoo did
            # send a bar for the session it reports trading on, so the note
            # below would name the wrong day.
            behind = True
            session_notes.append(
                f"Yahoo returned no usable bar for {', '.join(gap['missing'])}, between "
                f"the last complete session ({as_of:%Y-%m-%d}) and the session in "
                f"progress ({gap['left_out']}). A change measured across that gap "
                "would be wrong, so the unfinished bar is left out and this snapshot "
                f"ends at {as_of:%Y-%m-%d}. Prices are at least one session behind."
            )
        elif traded is not None and traded > as_of.date():
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
            zone = session["exchange_tz"]
            ended = pd.Timestamp(session["regular_end"]).tz_convert(zone)
            last = pd.Timestamp(session["last_trade"]).tz_convert(zone)
            session_notes.append(
                f"Yahoo's calendar says the session of the last bar ({as_of:%Y-%m-%d}) "
                f"ended at {ended:%H:%M} exchange time, yet it reports a trade at "
                f"{last:%H:%M}. Either a closing price was published late, or the "
                "calendar is early and the session is still open. Treat the close "
                f"and volume as provisional; a fetch after {last + _FEED_QUIET:%H:%M} "
                "exchange time settles it."
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
        # How old the bar was when it was downloaded is what points to a
        # holiday or a halt. After a failed refresh the bar can also be old
        # simply because the download is, which the note above already says.
        age_then = (prices.downloaded_at.tz_convert(local_tz).date() - as_of.date()).days
        if prices.downloaded_at > now:
            age_then = 0  # a download stamped in the future dates nothing
        if age_then > _STALE_AFTER_DAYS:
            session_notes.append(
                f"The newest available bar was {age_then} days old when it was "
                f"downloaded ({as_of:%Y-%m-%d}). No exchange holiday is that long: "
                "the symbol is probably halted, delisted or illiquid. Treat the "
                "snapshot as stale."
            )
        elif age_then > _OLD_BAR_NOTE_DAYS:
            session_notes.append(
                f"The newest available bar was {age_then} days old when it was "
                f"downloaded ({as_of:%Y-%m-%d}). That is normal during a long "
                "exchange holiday; otherwise the symbol may be halted."
            )
    # The currency describes the whole price table, so it is stated for past
    # dates too, not only for the newest bar.
    if currency and currency != "USD" and not anonymize:
        unit, major = _MINOR_UNITS.get(currency, ("", ""))
        detail = f" ({unit}, 1/100 of {major})" if unit else ""
        session_notes.append(f"Prices are quoted in {currency}{detail}.")
    notes += session_notes
    data_notes = session_notes + data_notes
    if prices.recheck_failed and not historical:
        # For the caller only: the prices, and what the agents must know about
        # them, are exactly those of the first look, so the prompt is unchanged.
        age = (now - prices.downloaded_at).total_seconds()
        notes.append(
            "A fresh download to settle the state of the last bar was attempted "
            "just now and failed (Yahoo returned nothing, which usually means it "
            f"is rate-limiting). These are still the prices downloaded "
            f"{_age_phrase(age)}, so nothing above has changed. Wait a minute "
            "before fetching again."
        )

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
