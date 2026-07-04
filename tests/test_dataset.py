"""Tests for tradinglab.features.dataset.

Synthetic data only (seeded RNG, business-day index) -- no network access.
The no-lookahead perturbation tests are the most important in this file.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradinglab.features.dataset import FEATURE_COLUMNS, build_dataset, build_features

# Exact feature contract of build_features (DESIGN.md section 5.5).
EXPECTED_FEATURES = {
    "ret_1", "ret_5", "ret_10", "ret_21",
    "vol_5", "vol_21",
    "close_over_sma10", "close_over_sma50", "close_over_sma200",
    "rsi_14", "bb_pctb", "macd_hist_norm", "atr_norm", "adx_14", "cci_20",
    "stoch_k", "mfi_14", "willr_14", "roc_10",
    "vlm_z21", "dist_52w_high", "dist_52w_low", "dow",
}


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


def test_feature_columns_contract():
    assert set(FEATURE_COLUMNS) == EXPECTED_FEATURES
    df = make_ohlcv()
    feats = build_features(df)
    assert list(feats.columns) == list(FEATURE_COLUMNS)
    assert feats.index.equals(df.index)
    # After the longest warm-up (252d rolling window) every feature is defined.
    assert feats.iloc[260:].notna().all().all()


def test_feature_values_and_scaling():
    df = make_ohlcv()
    feats = build_features(df)
    close = df["close"]
    # ret_1[t] = close[t]/close[t-1] - 1
    np.testing.assert_allclose(
        feats["ret_1"].iloc[1:].to_numpy(),
        close.pct_change().iloc[1:].to_numpy(),
        rtol=1e-12, atol=1e-15,
    )
    # ret_5[t] = close[t]/close[t-5] - 1
    np.testing.assert_allclose(
        feats["ret_5"].iloc[5:].to_numpy(),
        (close / close.shift(5) - 1.0).iloc[5:].to_numpy(),
        rtol=1e-12, atol=1e-15,
    )
    # dow = day of week 0-4
    np.testing.assert_array_equal(
        feats["dow"].to_numpy().astype(int), df.index.dayofweek.to_numpy()
    )
    valid = feats.iloc[260:]
    for col in ("rsi_14", "stoch_k", "mfi_14", "willr_14", "adx_14"):
        assert valid[col].between(0.0, 1.0).all(), col  # scaled to [0, 1]
    assert valid["cci_20"].between(-3.0, 3.0).all()
    assert (valid["atr_norm"] > 0.0).all()
    assert (valid["dist_52w_high"] <= 1e-9).all()   # close <= rolling 252d max
    assert (valid["dist_52w_low"] >= -1e-9).all()   # close >= rolling 252d min
    assert (valid[["vol_5", "vol_21"]] >= 0.0).all().all()


def test_labels_are_forward_returns_horizon_1():
    df = make_ohlcv()
    X, y, y_bin = build_dataset({"AAA": df}, horizon=1)
    assert len(X) > 100
    assert tuple(X.index.names) == ("ticker", "date")
    assert X.index.equals(y.index) and X.index.equals(y_bin.index)
    assert list(X.columns) == list(FEATURE_COLUMNS)
    # y[t] = close[t+1]/close[t] - 1
    fwd = df["close"].shift(-1) / df["close"] - 1.0
    dates = y.index.get_level_values("date")
    np.testing.assert_allclose(y.to_numpy(), fwd.loc[dates].to_numpy(),
                               rtol=1e-12, atol=1e-15)
    # The last df row has no forward label and must be dropped.
    assert df.index[-1] not in set(dates)
    # y_bin = (y > 0) as int
    np.testing.assert_array_equal(np.asarray(y_bin), (y > 0).astype(int).to_numpy())
    assert set(np.unique(np.asarray(y_bin))) <= {0, 1}
    # No NaNs anywhere in the returned dataset.
    assert X.notna().all().all()
    assert y.notna().all()


def test_labels_are_forward_returns_horizon_5():
    df = make_ohlcv()
    _, y, _ = build_dataset({"AAA": df}, horizon=5)
    fwd = df["close"].shift(-5) / df["close"] - 1.0
    dates = y.index.get_level_values("date")
    np.testing.assert_allclose(y.to_numpy(), fwd.loc[dates].to_numpy(),
                               rtol=1e-12, atol=1e-15)
    # The last 5 df rows have no 5-day-forward label.
    assert set(df.index[-5:]).isdisjoint(set(dates))


def test_multi_ticker_pooling():
    df_a = make_ohlcv(seed=1)
    df_b = make_ohlcv(seed=2)
    X, y, _ = build_dataset({"AAA": df_a, "BBB": df_b})
    assert set(X.index.get_level_values("ticker")) == {"AAA", "BBB"}
    # Pooling must not change per-ticker rows.
    X_a, y_a, _ = build_dataset({"AAA": df_a})
    pd.testing.assert_frame_equal(
        X.loc[["AAA"]].sort_index(), X_a.sort_index(), check_exact=True
    )
    pd.testing.assert_series_equal(
        y.loc[["AAA"]].sort_index(), y_a.sort_index(),
        check_exact=True, check_names=False,
    )


def test_features_no_lookahead():
    """THE key property: perturbing the last 30 rows must not change any
    feature value on the untouched prefix."""
    df = make_ohlcv()
    feats_full = build_features(df)
    feats_pert = build_features(perturb_tail(df, 30))
    prefix = df.index[:-30]
    pd.testing.assert_frame_equal(
        feats_full.loc[prefix], feats_pert.loc[prefix], check_exact=True
    )


def test_dataset_no_lookahead():
    """Same property through build_dataset: X rows dated before the perturbed
    region are identical; labels are identical up to `horizon` days before it."""
    df = make_ohlcv()
    df2 = perturb_tail(df, 30)
    X1, y1, _ = build_dataset({"AAA": df}, horizon=1)
    X2, y2, _ = build_dataset({"AAA": df2}, horizon=1)

    cutoff = df.index[-30]  # first perturbed date
    mask1 = np.asarray(X1.index.get_level_values("date") < cutoff)
    mask2 = np.asarray(X2.index.get_level_values("date") < cutoff)
    assert mask1.sum() > 50
    pd.testing.assert_frame_equal(X1[mask1], X2[mask2], check_exact=True)

    # y at date index[-31] already uses close[index[-30]] (perturbed), so labels
    # are only guaranteed unchanged strictly before index[-31].
    label_cutoff = df.index[-31]
    lmask1 = np.asarray(y1.index.get_level_values("date") < label_cutoff)
    lmask2 = np.asarray(y2.index.get_level_values("date") < label_cutoff)
    pd.testing.assert_series_equal(
        y1[lmask1], y2[lmask2], check_exact=True, check_names=False
    )
