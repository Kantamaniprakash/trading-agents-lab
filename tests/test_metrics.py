"""Tests for tradinglab.backtest.metrics.

Synthetic data only -- no network access.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from tradinglab.backtest.metrics import (
    compute_metrics,
    equity_curve,
    max_drawdown,
    metrics_table,
)

METRIC_KEYS = {
    "n_days", "cumulative_return", "annualized_return", "annualized_vol",
    "sharpe", "sortino", "max_drawdown", "calmar", "hit_rate",
}


def bday_series(values) -> pd.Series:
    """Float return series on a business-day index."""
    idx = pd.bdate_range("2022-01-03", periods=len(values))
    return pd.Series(list(values), index=idx, dtype=float)


def test_equity_curve_exact():
    r = bday_series([0.1, -0.05])
    eq = equity_curve(r)
    # 1 * 1.10 = 1.1 ; 1.1 * 0.95 = 1.045
    np.testing.assert_allclose(eq.to_numpy(), [1.1, 1.045], rtol=1e-12)
    assert eq.index.equals(r.index)


def test_max_drawdown_exact():
    eq = bday_series([1.0, 1.2, 0.9, 1.05, 1.32, 1.1])
    # Worst peak-to-trough: 1.2 -> 0.9 gives (1.2 - 0.9)/1.2 = 0.25
    # (the later 1.32 -> 1.1 dip is only ~0.1667). Positive fraction.
    assert max_drawdown(eq) == pytest.approx(0.25, rel=1e-12)


def test_max_drawdown_monotone_equity_is_zero():
    eq = bday_series([1.0, 1.1, 1.25, 1.3])
    assert max_drawdown(eq) == pytest.approx(0.0, abs=1e-12)


def test_compute_metrics_known_series():
    r = bday_series([0.02, 0.01, -0.01, 0.03, 0.0, -0.02])
    m = compute_metrics(r, rf=0.0)
    assert METRIC_KEYS <= set(m.keys())
    assert m["n_days"] == 6

    # cum = 1.02*1.01*0.99*1.03*1.00*0.98 - 1
    #     = 1.0302 * 0.99 = 1.019898 ; * 1.03 = 1.05049494 ; * 0.98 = 1.0294850412
    assert m["cumulative_return"] == pytest.approx(0.0294850412, rel=1e-9)

    # annualized = (1 + cum)^(252/6) - 1 = 1.0294850412^42 - 1
    expected_ann = 1.0294850412 ** 42.0 - 1.0
    assert m["annualized_return"] == pytest.approx(expected_ann, rel=1e-8)

    # mean = (0.02+0.01-0.01+0.03+0.00-0.02)/6 = 0.03/6 = 0.005
    # sharpe = mean/std * sqrt(252). The std ddof convention (0 or 1) is not
    # pinned down by the design, so accept either.
    mean = 0.005
    sharpe1 = mean / r.std(ddof=1) * math.sqrt(252.0)
    sharpe0 = mean / r.std(ddof=0) * math.sqrt(252.0)
    assert m["sharpe"] > 0.0
    assert (
        m["sharpe"] == pytest.approx(sharpe1, rel=1e-9)
        or m["sharpe"] == pytest.approx(sharpe0, rel=1e-9)
    )
    vol1 = r.std(ddof=1) * math.sqrt(252.0)
    vol0 = r.std(ddof=0) * math.sqrt(252.0)
    assert (
        m["annualized_vol"] == pytest.approx(vol1, rel=1e-9)
        or m["annualized_vol"] == pytest.approx(vol0, rel=1e-9)
    )

    # Equity: [1.02, 1.0302, 1.019898, 1.05049494, 1.05049494, 1.0294850412].
    # Dip 1: 1.0302 -> 1.019898 = exactly -1%. Dip 2: 1.05049494 -> *0.98 = -2%.
    assert m["max_drawdown"] == pytest.approx(0.02, rel=1e-9)
    assert m["calmar"] == pytest.approx(expected_ann / 0.02, rel=1e-6)

    # hit_rate: 5 non-zero-return days, 3 positive -> 3/5 = 0.6
    assert m["hit_rate"] == pytest.approx(0.6, rel=1e-12)

    assert np.isfinite(m["sortino"]) and m["sortino"] > 0.0


def test_drawdown_from_initial_capital():
    # A loss taken before equity ever exceeds starting capital must count:
    # equity [0.90, 0.918, 0.9455...] never regains 1.0, so the max drawdown
    # is 10% from inception and calmar is finite (not NaN).
    r = bday_series([-0.10, 0.02, 0.03])
    m = compute_metrics(r, rf=0.0)
    assert m["max_drawdown"] == pytest.approx(0.10, rel=1e-9)
    assert np.isfinite(m["calmar"])


def test_sortino_downside_deviation_about_zero():
    # Downside deviation = sqrt(mean(min(excess, 0)^2)) over ALL observations.
    r = bday_series([0.02, 0.01, -0.01, 0.03, 0.0, -0.02])
    dd = math.sqrt((0.01 ** 2 + 0.02 ** 2) / 6.0)
    expected = (0.005 / dd) * math.sqrt(252.0)
    m = compute_metrics(r, rf=0.0)
    assert m["sortino"] == pytest.approx(expected, rel=1e-9)


def test_positive_rf_lowers_sharpe():
    r = bday_series([0.02, 0.01, -0.01, 0.03, 0.0, -0.02])
    assert compute_metrics(r, rf=0.04)["sharpe"] < compute_metrics(r, rf=0.0)["sharpe"]


def test_constant_returns_zero_vol_guarded():
    # Constant positive returns: std = 0, no drawdown, no negative excess returns.
    # Division-by-zero cases must yield NaN (per design), never raise.
    # 0.015625 = 2^-6 is exact in binary, so mean/deviations/std are exactly 0
    # (a value like 0.01 leaves an epsilon-sized std through float rounding).
    r = bday_series([0.015625] * 10)
    m = compute_metrics(r, rf=0.0)
    assert m["cumulative_return"] == pytest.approx(1.015625 ** 10 - 1.0, rel=1e-12)
    assert m["max_drawdown"] == pytest.approx(0.0, abs=1e-12)
    assert m["hit_rate"] == pytest.approx(1.0, rel=1e-12)
    assert np.isnan(m["sharpe"])
    assert np.isnan(m["sortino"])
    assert np.isnan(m["calmar"])


def test_all_zero_returns_guarded():
    r = bday_series([0.0] * 5)
    m = compute_metrics(r)
    assert m["cumulative_return"] == pytest.approx(0.0, abs=1e-15)
    assert np.isnan(m["sharpe"])
    assert np.isnan(m["hit_rate"])  # no non-zero-return days


def test_empty_returns_guarded():
    m = compute_metrics(bday_series([]))
    assert METRIC_KEYS <= set(m.keys())
    assert m["n_days"] == 0
    assert np.isnan(m["sharpe"])


def test_metrics_table():
    rng = np.random.default_rng(0)
    named = {
        "alpha": bday_series(rng.normal(0.001, 0.01, 100)),
        "beta": bday_series(rng.normal(0.0, 0.02, 100)),
    }
    tbl = metrics_table(named)
    assert isinstance(tbl, pd.DataFrame)
    # Orientation (strategies as rows vs columns) is not pinned by the design.
    if {"alpha", "beta"} <= set(map(str, tbl.index)):
        assert "sharpe" in tbl.columns
    else:
        assert {"alpha", "beta"} <= set(map(str, tbl.columns))
        assert "sharpe" in tbl.index
