"""Daily OHLCV price data via yfinance, with a per-ticker parquet cache.

Every frame follows the canonical schema: tz-naive ascending ``DatetimeIndex``,
lowercase columns ``open, high, low, close, volume``, auto-adjusted prices
(``close`` is the adjusted close).
"""
from __future__ import annotations

import logging
import time
import warnings
from pathlib import Path

import pandas as pd
import yfinance as yf

from tradinglab.config import DATA_CACHE_DIR, DataConfig

_OHLCV = ["open", "high", "low", "close", "volume"]
_STALE_SECONDS = 3 * 24 * 3600  # "latest" caches expire after 3 days


class PriceDataError(ValueError):
    """Yahoo returned no usable prices.

    ``reason`` is ``"rate_limited"``, ``"not_found"`` or ``"unknown"``. Yahoo
    answers a rate-limited request and an unknown symbol with the same empty
    frame; the two need opposite responses (wait and retry, or stop), so the
    reason is recovered from what yfinance logged during the download.
    """

    def __init__(self, message: str, reason: str = "unknown"):
        super().__init__(message)
        self.reason = reason


class _YahooLog(logging.Handler):
    """Collects what yfinance logs while a download runs."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.messages.append(record.getMessage())
        except Exception:
            pass


_RATE_LIMIT_MARKS = ("ratelimit", "rate limit", "too many requests")
_NOT_FOUND_MARKS = ("quote not found", "not found for symbol", "possibly delisted",
                    "no timezone found", "no data found", "no price data found")


def _empty_download_error(ticker: str, start: str, end: str | None,
                          logged: list[str]) -> PriceDataError:
    """Build the error for an empty download from what Yahoo said about it."""
    text = " ".join(logged).lower()
    prefix = f"Yahoo returned no price data for ticker '{ticker}' (start={start}, end={end})"
    if any(mark in text for mark in _RATE_LIMIT_MARKS):
        return PriceDataError(
            f"{prefix}: Yahoo is rate-limiting requests from this machine. "
            "Wait a minute and retry.", "rate_limited")
    if any(mark in text for mark in _NOT_FOUND_MARKS):
        return PriceDataError(
            f"{prefix}: Yahoo does not know this symbol, or has no prices for "
            "it in that range. Check the symbol.", "not_found")
    return PriceDataError(
        f"{prefix}: no reason was given. The symbol or date range may be "
        "wrong, or Yahoo may be rate-limiting: wait a minute and retry.", "unknown")


def _warn_cache_unwritable(cache_root: Path, exc: OSError) -> None:
    warnings.warn(
        f"price cache {cache_root} is not writable ({exc.strerror or exc}); "
        "continuing without it. Set TRADINGLAB_HOME to a writable directory.",
        RuntimeWarning, stacklevel=3,
    )


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse yfinance MultiIndex columns to whichever level holds the OHLCV fields.

    Depending on the yfinance version, level 0 may be the field name or the
    ticker; pick the level that actually contains open/high/low/close.
    """
    if not isinstance(df.columns, pd.MultiIndex):
        return df
    required = {"open", "high", "low", "close"}
    df = df.copy()
    for level in range(df.columns.nlevels):
        values = {str(v).strip().lower() for v in df.columns.get_level_values(level)}
        if required.issubset(values):
            df.columns = df.columns.get_level_values(level)
            return df
    df.columns = df.columns.get_level_values(0)
    return df


def _normalize(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Normalize a raw yfinance download to the canonical price schema."""
    df = _flatten_columns(raw)
    df = df.rename(columns=lambda c: str(c).strip().lower())
    missing = [c for c in _OHLCV if c not in df.columns]
    if missing:
        raise ValueError(
            f"Ticker '{ticker}': downloaded data is missing columns {missing}."
        )
    df = df.loc[:, _OHLCV].copy()
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx
    df = df.dropna(how="any")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def _cache_is_fresh(path: Path, end: str | None) -> bool:
    """A cache file is fresh if it exists and, for open-ended ('latest') ranges,
    is younger than 3 days."""
    if not path.exists():
        return False
    if end is None and (time.time() - path.stat().st_mtime) > _STALE_SECONDS:
        return False
    return True


def fetch_prices(
    tickers: list[str] | str,
    start: str,
    end: str | None = None,
    cache_dir: Path | None = None,
    force: bool = False,
) -> dict[str, pd.DataFrame]:
    """Fetch daily auto-adjusted OHLCV frames for one or more tickers.

    ``end`` is INCLUSIVE: yfinance treats its own ``end`` as exclusive, so one
    calendar day is added to the download request internally; the cache
    filename keeps the user-facing ``end``.

    Results are cached per ticker at ``{cache_dir}/{ticker}_{start}_{end}.parquet``
    ("latest" stands in for ``end`` when it is None, and such caches go stale
    after 3 days). ``force=True`` bypasses the cache and re-downloads.

    Returns ``{ticker: df}`` in the canonical schema. Raises ``PriceDataError``
    (a ``ValueError``) if Yahoo returns nothing for a ticker, with the reason
    it gave, and ``ValueError`` if every row it returned was unusable. A cache
    directory that cannot be written is reported as a warning, not an error.
    """
    if isinstance(tickers, str):
        tickers = [tickers]
    cache_root = Path(cache_dir) if cache_dir is not None else DATA_CACHE_DIR
    # The cache is a convenience. A read-only data directory must not turn
    # prices that Yahoo did deliver into a traceback.
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _warn_cache_unwritable(cache_root, exc)
    end_tag = "latest" if end is None else end
    # yfinance 'end' is exclusive; request one extra day so 'end' is inclusive.
    yf_end = None if end is None else (
        (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    )

    out: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        cache_file = cache_root / f"{ticker}_{start}_{end_tag}.parquet"
        if not force and _cache_is_fresh(cache_file, end):
            df = pd.read_parquet(cache_file)
            df.index = pd.DatetimeIndex(df.index)
            out[ticker] = df
            continue
        yahoo_log = _YahooLog()
        yf_logger = logging.getLogger("yfinance")
        yf_logger.addHandler(yahoo_log)
        try:
            raw = yf.download(ticker, start=start, end=yf_end, auto_adjust=True, progress=False)
        finally:
            yf_logger.removeHandler(yahoo_log)
        if raw is None or len(raw) == 0:
            raise _empty_download_error(ticker, start, end, yahoo_log.messages)
        df = _normalize(raw, ticker)
        if df.empty:
            raise ValueError(
                f"Ticker '{ticker}': every downloaded row had NaN OHLCV values "
                f"(start={start}, end={end})."
            )
        try:
            df.to_parquet(cache_file)
        except OSError as exc:
            _warn_cache_unwritable(cache_root, exc)
        out[ticker] = df
    return out


def load_universe(cfg: DataConfig) -> dict[str, pd.DataFrame]:
    """Fetch the configured ticker universe (convenience wrapper around fetch_prices)."""
    return fetch_prices(cfg.tickers, cfg.start, cfg.end, cache_dir=cfg.cache_dir)
