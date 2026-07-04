"""The paper's five rule-based baseline strategies.

Each strategy maps the canonical price DataFrame to a signal Series per the
shared semantics: ``signal[t] in [-1, +1]`` is the desired position computed
with data up to and including the close of ``t``. Signals are NEVER shifted
here — the backtest engine applies the one-day execution lag. Indicator
warm-up rows produce signal 0.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from tradinglab.features.indicators import macd, rsi, sma, stochastic


def _events_to_signal(events: pd.Series) -> pd.Series:
    """Turn a sparse +1/-1/0/NaN event series into a held signal.

    NaN means "no new event": carry the prior signal forward; before any
    event the signal is 0 (flat).
    """
    return events.ffill().fillna(0.0).astype(float)


def buy_and_hold(df: pd.DataFrame) -> pd.Series:
    """Fully long (+1) on every day."""
    return pd.Series(1.0, index=df.index)


# EMA burn-in for the default MACD(12, 26, 9): ewm(adjust=False) is seeded at
# row 0 (macd == macd_signal == 0.0 exactly there, never NaN), so NaN-based
# masking alone cannot detect the warm-up. Per DESIGN 5.8 ("warm-up rows ->
# signal 0") we treat the first `slow` (26) rows as warm-up.
_MACD_SLOW = 26


def macd_strategy(df: pd.DataFrame) -> pd.Series:
    """+1 while the MACD line is above its signal line, else -1; 0 in warm-up.

    Warm-up is defined as rows where macd_hist is NaN or the positional row
    index is < the slow EMA period (26): ewm(adjust=False) is defined from
    row 0 (identically zero there), so without the positional cutoff the
    identical-zero tie at row 0 would produce a short on no information.
    """
    m = macd(df["close"])
    warmup = m["macd_hist"].isna().to_numpy() | (
        np.arange(len(df)) < _MACD_SLOW
    )
    values = np.where(
        warmup, 0.0, np.where(m["macd"] > m["macd_signal"], 1.0, -1.0)
    )
    return pd.Series(values, index=df.index)


def kdj_rsi_strategy(df: pd.DataFrame) -> pd.Series:
    """Oversold/overbought reversal on RSI and the KDJ J-line.

    +1 when rsi_14 < 30 or kdj_j < 20; -1 when rsi_14 > 70 or kdj_j > 80
    (buy condition wins if both fire); otherwise hold the prior signal
    (flat before the first event).

    Warm-up (DESIGN 5.8): rows where rsi_14 or kdj_j is NaN contribute no
    event and are forced to signal 0. With the default periods the kdj_j
    NaN window (rows 0-14: 13 NaN %K rows plus 2 for the 3-day %D) strictly
    covers the RSI ewm burn-in, so no degenerate early-RSI value (exactly
    0/100 from a single diff) can open a position.
    """
    r = rsi(df["close"])
    j = stochastic(df)["kdj_j"]
    warmup = r.isna() | j.isna()
    buy = ((r < 30) | (j < 20)) & ~warmup
    sell = ((r > 70) | (j > 80)) & ~warmup
    events = pd.Series(
        np.select([buy, sell], [1.0, -1.0], default=np.nan), index=df.index
    )
    signal = _events_to_signal(events)
    # No events can fire during warm-up, so ffill cannot leak a position
    # into it; force 0 explicitly to keep the contract robust regardless.
    signal[warmup.to_numpy()] = 0.0
    return signal


def zmr_strategy(
    df: pd.DataFrame,
    window: int = 20,
    entry_z: float = 1.0,
    exit_z: float = 0.3,
) -> pd.Series:
    """Z-score mean reversion.

    z = (close - sma(window)) / rolling std(window). Short (-1) when
    z > entry_z, long (+1) when z < -entry_z, flat (0) when |z| < exit_z;
    otherwise hold the prior signal (flat before the first event).
    """
    close = df["close"]
    z = (close - sma(close, window)) / close.rolling(window).std()
    events = pd.Series(
        np.select(
            [z > entry_z, z < -entry_z, z.abs() < exit_z],
            [-1.0, 1.0, 0.0],
            default=np.nan,
        ),
        index=df.index,
    )
    return _events_to_signal(events)


def sma_cross_strategy(df: pd.DataFrame, fast: int = 10, slow: int = 50) -> pd.Series:
    """+1 while the fast SMA is above the slow SMA, else -1; 0 in warm-up."""
    fast_sma = sma(df["close"], fast)
    slow_sma = sma(df["close"], slow)
    valid = fast_sma.notna() & slow_sma.notna()
    values = np.where(valid, np.where(fast_sma > slow_sma, 1.0, -1.0), 0.0)
    return pd.Series(values, index=df.index)


STRATEGIES: dict[str, Callable] = {
    "buy_hold": buy_and_hold,
    "macd": macd_strategy,
    "kdj_rsi": kdj_rsi_strategy,
    "zmr": zmr_strategy,
    "sma_cross": sma_cross_strategy,
}
