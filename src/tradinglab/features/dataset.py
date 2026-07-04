"""Scale-free feature matrix and pooled supervised dataset construction.

Features at row ``t`` use only rows ``<= t`` (rolling/ewm windows and
non-negative shifts). Labels are strictly forward: ``shift(-horizon)`` is
applied to close per ticker BEFORE pooling.
"""
from __future__ import annotations

import pandas as pd

from tradinglab.features.indicators import (
    adx,
    atr,
    bollinger,
    cci,
    macd,
    mfi,
    roc,
    rsi,
    sma,
    stochastic,
    williams_r,
)

FEATURE_COLUMNS: list[str] = [
    "ret_1",
    "ret_5",
    "ret_10",
    "ret_21",
    "vol_5",
    "vol_21",
    "close_over_sma10",
    "close_over_sma50",
    "close_over_sma200",
    "rsi_14",
    "bb_pctb",
    "macd_hist_norm",
    "atr_norm",
    "adx_14",
    "cci_20",
    "stoch_k",
    "mfi_14",
    "willr_14",
    "roc_10",
    "vlm_z21",
    "dist_52w_high",
    "dist_52w_low",
    "dow",
]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build the scale-free feature frame for one canonical OHLCV frame.

    Returns a DataFrame aligned to ``df.index`` with columns exactly
    ``FEATURE_COLUMNS``. Warm-up rows contain NaN (dropped downstream).
    """
    close = df["close"]
    volume = df["volume"]
    ret_1 = close.pct_change(1)

    out = pd.DataFrame(index=df.index)
    out["ret_1"] = ret_1
    out["ret_5"] = close.pct_change(5)
    out["ret_10"] = close.pct_change(10)
    out["ret_21"] = close.pct_change(21)
    out["vol_5"] = ret_1.rolling(5).std()
    out["vol_21"] = ret_1.rolling(21).std()
    out["close_over_sma10"] = close / sma(close, 10) - 1.0
    out["close_over_sma50"] = close / sma(close, 50) - 1.0
    out["close_over_sma200"] = close / sma(close, 200) - 1.0
    out["rsi_14"] = rsi(close, 14) / 100.0
    out["bb_pctb"] = bollinger(close)["bb_pctb"]
    out["macd_hist_norm"] = macd(close)["macd_hist"] / close
    out["atr_norm"] = atr(df, 14) / close
    out["adx_14"] = adx(df, 14) / 100.0
    out["cci_20"] = (cci(df, 20) / 100.0).clip(-3.0, 3.0)
    out["stoch_k"] = stochastic(df)["stoch_k"] / 100.0
    out["mfi_14"] = mfi(df, 14) / 100.0
    out["willr_14"] = williams_r(df, 14) / -100.0
    out["roc_10"] = roc(close, 10) / 100.0
    vlm_mean = volume.rolling(21).mean()
    vlm_std = volume.rolling(21).std()
    out["vlm_z21"] = (volume - vlm_mean) / vlm_std
    out["dist_52w_high"] = close / close.rolling(252).max() - 1.0
    out["dist_52w_low"] = close / close.rolling(252).min() - 1.0
    out["dow"] = df.index.dayofweek.astype("int64")
    return out[FEATURE_COLUMNS]


def build_dataset(prices: dict[str, pd.DataFrame], horizon: int = 1
                  ) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Pool per-ticker features and forward-return labels.

    Returns ``(X, y, y_bin)`` indexed by MultiIndex ``(ticker, date)``.
    ``y[t] = close[t+horizon] / close[t] - 1`` computed per ticker before
    pooling; ``y_bin = (y > 0)`` as int. Rows with any NaN feature or label
    are dropped.
    """
    if not prices:
        raise ValueError("prices dict is empty — nothing to build a dataset from")

    frames: list[pd.DataFrame] = []
    for ticker, df in prices.items():
        feats = build_features(df)
        # Forward label per ticker, before pooling.
        feats = feats.copy()
        feats["__y__"] = df["close"].shift(-horizon) / df["close"] - 1.0
        frames.append(feats)

    pooled = pd.concat(frames, keys=list(prices.keys()), names=["ticker", "date"])
    pooled = pooled.dropna()

    X = pooled[FEATURE_COLUMNS]
    y = pooled["__y__"].rename("y")
    y_bin = (y > 0).astype(int).rename("y_bin")
    return X, y, y_bin
