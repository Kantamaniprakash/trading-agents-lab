"""Daily OHLCV price data via yfinance, with a per-ticker parquet cache.

Every frame follows the canonical schema: tz-naive ascending ``DatetimeIndex``,
lowercase columns ``open, high, low, close, volume``, auto-adjusted prices
(``close`` is the adjusted close).
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import yfinance as yf

from tradinglab.config import DATA_CACHE_DIR, DataConfig

_OHLCV = ["open", "high", "low", "close", "volume"]
_STALE_SECONDS = 3 * 24 * 3600  # "latest" caches expire after 3 days


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

    Returns ``{ticker: df}`` in the canonical schema. Raises ``ValueError`` if a
    ticker yields no usable data.
    """
    if isinstance(tickers, str):
        tickers = [tickers]
    cache_root = Path(cache_dir) if cache_dir is not None else DATA_CACHE_DIR
    cache_root.mkdir(parents=True, exist_ok=True)
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
        raw = yf.download(ticker, start=start, end=yf_end, auto_adjust=True, progress=False)
        if raw is None or len(raw) == 0:
            raise ValueError(
                f"No price data returned for ticker '{ticker}' "
                f"(start={start}, end={end}). Check the symbol and date range."
            )
        df = _normalize(raw, ticker)
        if df.empty:
            raise ValueError(
                f"Ticker '{ticker}': every downloaded row had NaN OHLCV values "
                f"(start={start}, end={end})."
            )
        df.to_parquet(cache_file)
        out[ticker] = df
    return out


def load_universe(cfg: DataConfig) -> dict[str, pd.DataFrame]:
    """Fetch the configured ticker universe (convenience wrapper around fetch_prices)."""
    return fetch_prices(cfg.tickers, cfg.start, cfg.end, cache_dir=cfg.cache_dir)
