"""Technical indicators in pure pandas.

Every function is strictly causal: only rolling / ewm / expanding windows and
non-negative shifts are used, so the value at row ``t`` depends solely on rows
``<= t``. Warm-up rows are NaN. Wilder smoothing is implemented as
``ewm(alpha=1/n, adjust=False)``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(close: pd.Series, n: int) -> pd.Series:
    """Simple moving average over ``n`` periods."""
    return close.rolling(n).mean()


def ema(close: pd.Series, n: int) -> pd.Series:
    """Exponential moving average with span ``n`` (adjust=False)."""
    return close.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Relative Strength Index with Wilder smoothing, in [0, 100]."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False).mean()
    # Equivalent to 100 - 100/(1+RS); yields 100 when avg_loss == 0.
    return 100.0 * avg_gain / (avg_gain + avg_loss)


def macd(close: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    """MACD line, signal line and histogram (cols: macd, macd_signal, macd_hist)."""
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame(
        {"macd": line, "macd_signal": sig, "macd_hist": line - sig}
    )


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    """Bollinger bands (population std) — cols: bb_mid, bb_upper, bb_lower, bb_pctb."""
    mid = sma(close, n)
    std = close.rolling(n).std(ddof=0)
    upper = mid + k * std
    lower = mid - k * std
    pctb = (close - lower) / (upper - lower)
    return pd.DataFrame(
        {"bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bb_pctb": pctb}
    )


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Average True Range with Wilder smoothing."""
    return _true_range(df).ewm(alpha=1.0 / n, adjust=False).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Average Directional Index (Wilder), in [0, 100]."""
    up = df["high"].diff()
    down = -df["low"].diff()
    # Row 0 has no defined directional movement (diff is NaN); keep it NaN so
    # Wilder smoothing seeds on the first real observation instead of a
    # fabricated zero.
    undefined = up.isna() | down.isna()
    plus_dm = pd.Series(
        np.where((up > down) & (up > 0), up, 0.0), index=df.index
    ).mask(undefined)
    minus_dm = pd.Series(
        np.where((down > up) & (down > 0), down, 0.0), index=df.index
    ).mask(undefined)
    atr_n = atr(df, n)
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / n, adjust=False).mean() / atr_n
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / n, adjust=False).mean() / atr_n
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1.0 / n, adjust=False).mean()


def cci(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """Commodity Channel Index with the 0.015 scaling constant."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    tp_sma = tp.rolling(n).mean()
    mad = tp.rolling(n).apply(
        lambda x: np.mean(np.abs(x - x.mean())), raw=True
    )
    return (tp - tp_sma) / (0.015 * mad)


def stochastic(df: pd.DataFrame, n: int = 14, d: int = 3) -> pd.DataFrame:
    """Fast stochastic oscillator — cols: stoch_k, stoch_d, kdj_j (= 3K − 2D)."""
    lowest = df["low"].rolling(n).min()
    highest = df["high"].rolling(n).max()
    k = 100.0 * (df["close"] - lowest) / (highest - lowest)
    d_line = k.rolling(d).mean()
    return pd.DataFrame(
        {"stoch_k": k, "stoch_d": d_line, "kdj_j": 3.0 * k - 2.0 * d_line}
    )


def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume (cumulative signed volume)."""
    direction = np.sign(df["close"].diff()).fillna(0.0)
    return (direction * df["volume"]).cumsum()


def vwma(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """Volume-weighted moving average of close over ``n`` periods."""
    pv = (df["close"] * df["volume"]).rolling(n).sum()
    return pv / df["volume"].rolling(n).sum()


def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Money Flow Index from typical price × volume, in [0, 100]."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    flow = tp * df["volume"]
    prev_tp = tp.shift(1)
    # Row 0 has no defined flow direction (prev_tp is NaN); keep it NaN so the
    # rolling window only fills with real observations (first value at row n).
    pos = flow.where(tp > prev_tp, 0.0).mask(prev_tp.isna())
    neg = flow.where(tp < prev_tp, 0.0).mask(prev_tp.isna())
    pos_sum = pos.rolling(n).sum()
    neg_sum = neg.rolling(n).sum()
    return 100.0 * pos_sum / (pos_sum + neg_sum)


def roc(close: pd.Series, n: int = 10) -> pd.Series:
    """Rate of change over ``n`` periods, in percent."""
    return 100.0 * (close / close.shift(n) - 1.0)


def williams_r(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Williams %R, in [-100, 0]."""
    highest = df["high"].rolling(n).max()
    lowest = df["low"].rolling(n).min()
    return -100.0 * (highest - df["close"]) / (highest - lowest)


def compute_indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the full indicator set for a canonical OHLCV frame.

    Returns a DataFrame aligned to ``df.index`` with columns exactly:
    sma_10, sma_50, sma_200, ema_12, ema_26, rsi_14, macd, macd_signal,
    macd_hist, bb_mid, bb_upper, bb_lower, bb_pctb, atr_14, adx_14, cci_20,
    stoch_k, stoch_d, kdj_j, obv, vwma_20, mfi_14, roc_10, willr_14.
    """
    close = df["close"]
    macd_df = macd(close)
    boll_df = bollinger(close)
    stoch_df = stochastic(df)
    out = pd.DataFrame(index=df.index)
    out["sma_10"] = sma(close, 10)
    out["sma_50"] = sma(close, 50)
    out["sma_200"] = sma(close, 200)
    out["ema_12"] = ema(close, 12)
    out["ema_26"] = ema(close, 26)
    out["rsi_14"] = rsi(close, 14)
    out["macd"] = macd_df["macd"]
    out["macd_signal"] = macd_df["macd_signal"]
    out["macd_hist"] = macd_df["macd_hist"]
    out["bb_mid"] = boll_df["bb_mid"]
    out["bb_upper"] = boll_df["bb_upper"]
    out["bb_lower"] = boll_df["bb_lower"]
    out["bb_pctb"] = boll_df["bb_pctb"]
    out["atr_14"] = atr(df, 14)
    out["adx_14"] = adx(df, 14)
    out["cci_20"] = cci(df, 20)
    out["stoch_k"] = stoch_df["stoch_k"]
    out["stoch_d"] = stoch_df["stoch_d"]
    out["kdj_j"] = stoch_df["kdj_j"]
    out["obv"] = obv(df)
    out["vwma_20"] = vwma(df, 20)
    out["mfi_14"] = mfi(df, 14)
    out["roc_10"] = roc(close, 10)
    out["willr_14"] = williams_r(df, 14)
    return out
