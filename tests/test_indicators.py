"""Tests for tradinglab.features.indicators.

Synthetic data only (seeded RNG, business-day index) -- no network access.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradinglab.features.indicators import (
    adx,
    atr,
    bollinger,
    cci,
    compute_indicator_frame,
    ema,
    macd,
    mfi,
    obv,
    roc,
    rsi,
    sma,
    stochastic,
    vwma,
    williams_r,
)

# Exact column contract of compute_indicator_frame (DESIGN.md section 5.4).
INDICATOR_COLUMNS = [
    "sma_10", "sma_50", "sma_200", "ema_12", "ema_26", "rsi_14",
    "macd", "macd_signal", "macd_hist",
    "bb_mid", "bb_upper", "bb_lower", "bb_pctb",
    "atr_14", "adx_14", "cci_20",
    "stoch_k", "stoch_d", "kdj_j",
    "obv", "vwma_20", "mfi_14", "roc_10", "willr_14",
]


def make_ohlcv(n: int = 400, seed: int = 0) -> pd.DataFrame:
    """Synthetic canonical OHLCV frame: random walk on a business-day index."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, n)))
    open_ = close * (1.0 + rng.normal(0.0, 0.002, n))
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.003, n)))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.003, n)))
    volume = rng.integers(100_000, 1_000_000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def perturb_tail(df: pd.DataFrame, n_tail: int, seed: int = 99) -> pd.DataFrame:
    """Copy of df with the last n_tail rows materially altered (prices and volume)."""
    rng = np.random.default_rng(seed)
    out = df.copy()
    tail = out.index[-n_tail:]
    factors = 1.0 + rng.uniform(0.05, 0.40, size=n_tail)
    for col in ("open", "high", "low", "close"):
        out.loc[tail, col] = out.loc[tail, col].to_numpy() * factors
    out.loc[tail, "volume"] = out.loc[tail, "volume"].to_numpy() * 3.0 + 12_345.0
    return out


def test_sma_exact_values():
    close = pd.Series(
        [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        index=pd.bdate_range("2021-01-01", periods=6),
    )
    s = sma(close, 3)
    assert s.index.equals(close.index)
    assert s.iloc[:2].isna().all()  # warm-up rows are NaN
    # (1+2+3)/3 = 2, (2+3+4)/3 = 3, (3+4+5)/3 = 4, (4+5+6)/3 = 5
    np.testing.assert_allclose(s.iloc[2:].to_numpy(), [2.0, 3.0, 4.0, 5.0], rtol=1e-12)


def test_ema_exact_values():
    # alpha = 2/(n+1) = 0.5 for n=3. The first three values are constant 4, so the
    # recursion is identical whether the EMA is seeded with close[0] or with SMA(3):
    #   ema[0..2] = 4
    #   ema[3] = 0.5*8 + 0.5*4 = 6
    #   ema[4] = 0.5*6 + 0.5*6 = 6
    close = pd.Series(
        [4.0, 4.0, 4.0, 8.0, 6.0],
        index=pd.bdate_range("2021-01-01", periods=5),
    )
    e = ema(close, 3)
    assert e.index.equals(close.index)
    assert e.iloc[3] == pytest.approx(6.0, rel=1e-12)
    assert e.iloc[4] == pytest.approx(6.0, rel=1e-12)


def test_ema_converges_to_span_definition():
    # Far from the warm-up region, any reasonable seeding converges to the
    # recursive span EMA (alpha = 2/(n+1)); this pins down the alpha definition.
    close = make_ohlcv()["close"]
    e = ema(close, 12)
    expected = close.ewm(span=12, adjust=False).mean().iloc[-1]
    assert e.iloc[-1] == pytest.approx(expected, rel=1e-8)


def test_rsi_bounds_random_walk():
    close = make_ohlcv()["close"]
    r = rsi(close, 14).dropna()
    assert len(r) > 300
    assert r.between(0.0, 100.0).all()


def test_rsi_equals_100_on_strictly_rising_series():
    # Constant daily gains, zero losses -> avg_loss = 0 -> RSI = 100 after warm-up.
    close = pd.Series(
        np.linspace(10.0, 70.0, 60),
        index=pd.bdate_range("2021-01-01", periods=60),
    )
    r = rsi(close, 14)
    tail = r.iloc[20:]
    assert tail.notna().all()
    np.testing.assert_allclose(tail.to_numpy(), 100.0, rtol=1e-9)


def test_macd_columns_and_histogram_identity():
    close = make_ohlcv()["close"]
    m = macd(close)
    assert list(m.columns) == ["macd", "macd_signal", "macd_hist"]
    valid = m.dropna()
    assert len(valid) > 300
    np.testing.assert_allclose(
        valid["macd_hist"].to_numpy(),
        (valid["macd"] - valid["macd_signal"]).to_numpy(),
        rtol=1e-9, atol=1e-12,
    )


def test_bollinger_mid_equals_sma20():
    close = make_ohlcv()["close"]
    b = bollinger(close, 20, 2.0)
    assert list(b.columns) == ["bb_mid", "bb_upper", "bb_lower", "bb_pctb"]
    pd.testing.assert_series_equal(b["bb_mid"], sma(close, 20), check_names=False)
    # Bands are symmetric around the mid: (upper + lower) / 2 == mid.
    valid = b.dropna()
    np.testing.assert_allclose(
        ((valid["bb_upper"] + valid["bb_lower"]) / 2.0).to_numpy(),
        valid["bb_mid"].to_numpy(),
        rtol=1e-9,
    )


def test_stochastic_columns_and_kdj_identity():
    df = make_ohlcv()
    st = stochastic(df)
    assert list(st.columns) == ["stoch_k", "stoch_d", "kdj_j"]
    valid = st.dropna()
    assert len(valid) > 300
    assert valid["stoch_k"].between(0.0, 100.0).all()
    # kdj_j = 3K - 2D by definition.
    np.testing.assert_allclose(
        valid["kdj_j"].to_numpy(),
        (3.0 * valid["stoch_k"] - 2.0 * valid["stoch_d"]).to_numpy(),
        rtol=1e-9, atol=1e-9,
    )


def test_roc_exact_values():
    close = pd.Series(
        np.arange(100.0, 130.0),
        index=pd.bdate_range("2021-01-01", periods=30),
    )
    r = roc(close, 10)
    assert r.iloc[:10].isna().all()  # warm-up rows are NaN
    # close[10]=110, close[0]=100 -> 100*(110/100 - 1) = 10.0
    assert r.iloc[10] == pytest.approx(10.0, rel=1e-12)
    # close[20]=120, close[10]=110 -> 100*(120/110 - 1) = 9.0909...
    assert r.iloc[20] == pytest.approx(100.0 * (120.0 / 110.0 - 1.0), rel=1e-12)


def test_individual_indicators_are_series_aligned_to_index():
    df = make_ohlcv(120)
    close = df["close"]
    outputs = [
        sma(close, 10), ema(close, 10), rsi(close), atr(df), adx(df),
        cci(df), obv(df), vwma(df), mfi(df), roc(close), williams_r(df),
    ]
    for s in outputs:
        assert isinstance(s, pd.Series)
        assert s.index.equals(df.index)


def test_indicator_frame_columns_and_bounds():
    df = make_ohlcv()
    frame = compute_indicator_frame(df)
    assert list(frame.columns) == INDICATOR_COLUMNS
    assert frame.index.equals(df.index)
    assert frame["rsi_14"].dropna().between(0.0, 100.0).all()
    assert frame["adx_14"].dropna().between(0.0, 100.0).all()
    assert frame["mfi_14"].dropna().between(0.0, 100.0).all()
    assert frame["stoch_k"].dropna().between(0.0, 100.0).all()
    assert frame["willr_14"].dropna().between(-100.0, 0.0).all()
    assert (frame["atr_14"].dropna() > 0.0).all()


def test_adx_and_mfi_row0_warmup_is_nan():
    """Row 0 has no defined directional movement / flow direction, so it must
    not be counted as a zero observation: ADX is NaN at row 0 and MFI's first
    valid value appears at index n (not n-1)."""
    df = make_ohlcv(120)
    a = adx(df, 14)
    assert np.isnan(a.iloc[0])
    m = mfi(df, 14)
    assert m.iloc[:14].isna().all()
    assert m.iloc[14:].notna().all()


def test_indicator_frame_no_lookahead():
    """THE key property: perturbing the last 30 rows must not change any
    indicator value on the untouched prefix."""
    df = make_ohlcv()
    frame_full = compute_indicator_frame(df)
    frame_perturbed = compute_indicator_frame(perturb_tail(df, 30))
    prefix = df.index[:-30]
    pd.testing.assert_frame_equal(
        frame_full.loc[prefix],
        frame_perturbed.loc[prefix],
        check_exact=True,
    )
