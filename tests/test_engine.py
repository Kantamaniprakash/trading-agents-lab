"""Tests for tradinglab.backtest.engine.

Synthetic data only -- no network access. Verifies the shift discipline
(signal at t earns returns only from t+1), transaction costs, clipping,
and the no-lookahead property of the engine itself.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradinglab.backtest.engine import (
    BacktestResult,
    backtest_portfolio,
    backtest_signals,
)

METRIC_KEYS = {
    "n_days", "cumulative_return", "annualized_return", "annualized_vol",
    "sharpe", "sortino", "max_drawdown", "calmar", "hit_rate",
}

# Default cost: 10 bps per unit turnover = 10/1e4 = 0.001.
COST = 0.001


def five_day_df() -> pd.DataFrame:
    """Five business days with hand-picked closes [100, 101, 102, 100, 103]."""
    idx = pd.bdate_range("2024-01-01", periods=5)
    close = np.array([100.0, 101.0, 102.0, 100.0, 103.0])
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(5, 1e6),
        },
        index=idx,
    )


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


def test_always_long_exact_net_returns():
    df = five_day_df()
    res = backtest_signals(df, pd.Series(1.0, index=df.index), cost_bps=10.0,
                           name="long")
    assert isinstance(res, BacktestResult)
    assert res.name == "long"
    assert res.net_returns.index.equals(df.index)
    # position = signal.shift(1).fillna(0) = [0, 1, 1, 1, 1]
    np.testing.assert_allclose(res.positions.to_numpy(), [0, 1, 1, 1, 1])
    # turnover = diff().abs() with first = |position[0]| = [0, 1, 0, 0, 0]
    np.testing.assert_allclose(res.turnover.to_numpy(), [0, 1, 0, 0, 0])
    # day0: first row return = 0
    # day1: 101/100 - 1 = 0.01, minus entry cost 0.001 -> 0.009
    # day2: 102/101 - 1 ; day3: 100/102 - 1 ; day4: 103/100 - 1 (no more turnover)
    expected = [
        0.0,
        (101.0 / 100.0 - 1.0) - COST,
        102.0 / 101.0 - 1.0,
        100.0 / 102.0 - 1.0,
        103.0 / 100.0 - 1.0,
    ]
    np.testing.assert_allclose(res.net_returns.to_numpy(), expected,
                               rtol=1e-12, atol=1e-15)


def test_signal_at_t_earns_from_t_plus_1():
    df = five_day_df()
    # One-day pulse at index 2 -> position held only during day 3.
    sig = pd.Series([0.0, 0.0, 1.0, 0.0, 0.0], index=df.index)
    res = backtest_signals(df, sig)
    np.testing.assert_allclose(res.positions.to_numpy(), [0, 0, 0, 1, 0])
    # day3: gross = 100/102 - 1, entry cost 0.001
    # day4: flat but pays exit cost 0.001 (turnover |0 - 1| = 1)
    expected = [0.0, 0.0, 0.0, (100.0 / 102.0 - 1.0) - COST, -COST]
    np.testing.assert_allclose(res.net_returns.to_numpy(), expected,
                               rtol=1e-12, atol=1e-15)


def test_sparse_signal_is_ffilled():
    df = five_day_df()
    # Signal defined only on the first date; engine reindexes + ffills -> always long.
    sparse = pd.Series([1.0], index=[df.index[0]])
    res_sparse = backtest_signals(df, sparse)
    res_full = backtest_signals(df, pd.Series(1.0, index=df.index))
    np.testing.assert_allclose(res_sparse.positions.to_numpy(),
                               res_full.positions.to_numpy())
    np.testing.assert_allclose(res_sparse.net_returns.to_numpy(),
                               res_full.net_returns.to_numpy(), rtol=1e-12)


def test_flip_long_to_short_charges_double_cost():
    df = five_day_df()
    sig = pd.Series([1.0, 1.0, -1.0, -1.0, -1.0], index=df.index)
    res = backtest_signals(df, sig)
    # position = [0, 1, 1, -1, -1]; turnover = [0, 1, 0, 2, 0]
    np.testing.assert_allclose(res.positions.to_numpy(), [0, 1, 1, -1, -1])
    np.testing.assert_allclose(res.turnover.to_numpy(), [0, 1, 0, 2, 0])
    # day3 gross = -1 * (100/102 - 1); flip cost = 2 * 0.001
    assert res.gross_returns.iloc[3] == pytest.approx(-(100.0 / 102.0 - 1.0),
                                                      rel=1e-12)
    expected = [
        0.0,
        0.01 - COST,
        102.0 / 101.0 - 1.0,
        -(100.0 / 102.0 - 1.0) - 2.0 * COST,
        -(103.0 / 100.0 - 1.0),
    ]
    np.testing.assert_allclose(res.net_returns.to_numpy(), expected,
                               rtol=1e-12, atol=1e-15)


def test_buy_and_hold_equity_matches_price_relative_net_of_entry_cost():
    df = five_day_df()
    res = backtest_signals(df, pd.Series(1.0, index=df.index))
    # equity_final = (1 + 0.01 - 0.001) * (102/101)*(100/102)*(103/100)
    #              = 1.009 * 103/101   (telescoping product)
    assert res.equity.iloc[-1] == pytest.approx(1.009 * 103.0 / 101.0, rel=1e-12)
    # ~ price relative net of one entry cost: (103/100) * (1 - 0.001)
    assert res.equity.iloc[-1] == pytest.approx((103.0 / 100.0) * (1.0 - COST),
                                                rel=2e-3)
    # Equity is compounded from net returns.
    np.testing.assert_allclose(
        res.equity.to_numpy(),
        (1.0 + res.net_returns).cumprod().to_numpy(),
        rtol=1e-12,
    )


def test_long_only_clips_short_signals_to_flat():
    df = five_day_df()
    res = backtest_signals(df, pd.Series(-1.0, index=df.index), allow_short=False)
    np.testing.assert_allclose(res.positions.to_numpy(), 0.0)
    np.testing.assert_allclose(res.net_returns.to_numpy(), 0.0, atol=1e-15)
    np.testing.assert_allclose(res.turnover.to_numpy(), 0.0)


def test_signals_clipped_to_unit_interval():
    df = five_day_df()
    res_big = backtest_signals(df, pd.Series(5.0, index=df.index))
    np.testing.assert_allclose(res_big.positions.to_numpy(), [0, 1, 1, 1, 1])
    res_neg = backtest_signals(df, pd.Series(-5.0, index=df.index), allow_short=True)
    np.testing.assert_allclose(res_neg.positions.to_numpy(), [0, -1, -1, -1, -1])


def test_short_position_earns_negative_market_return():
    df = five_day_df()
    res = backtest_signals(df, pd.Series(-1.0, index=df.index), allow_short=True)
    # day1: gross = -1 * (101/100 - 1) = -0.01 ; net = -0.01 - entry cost
    assert res.gross_returns.iloc[1] == pytest.approx(-0.01, rel=1e-12)
    assert res.net_returns.iloc[1] == pytest.approx(-0.01 - COST, rel=1e-12)


def test_zero_cost_net_equals_gross():
    df = five_day_df()
    res = backtest_signals(df, pd.Series(1.0, index=df.index), cost_bps=0.0)
    np.testing.assert_allclose(res.net_returns.to_numpy(),
                               res.gross_returns.to_numpy(), rtol=1e-12, atol=1e-15)


def test_result_metrics_populated():
    df = make_ohlcv(300)
    res = backtest_signals(df, pd.Series(1.0, index=df.index))
    assert METRIC_KEYS <= set(res.metrics.keys())
    assert res.metrics["cumulative_return"] == pytest.approx(
        res.equity.iloc[-1] - 1.0, rel=1e-9
    )


def test_engine_no_lookahead():
    """Perturbing future prices must not change any prefix output of the engine."""
    df = make_ohlcv()
    # Deterministic signal, independent of prices.
    sig = pd.Series(np.where(np.arange(len(df)) % 40 < 20, 1.0, -1.0),
                    index=df.index)
    res_full = backtest_signals(df, sig)

    rng = np.random.default_rng(99)
    df2 = df.copy()
    tail = df2.index[-30:]
    factors = 1.0 + rng.uniform(0.05, 0.40, size=30)
    for col in ("open", "high", "low", "close"):
        df2.loc[tail, col] = df2.loc[tail, col].to_numpy() * factors
    res_pert = backtest_signals(df2, sig)

    prefix = df.index[:-30]
    for attr in ("positions", "gross_returns", "net_returns", "turnover", "equity"):
        pd.testing.assert_series_equal(
            getattr(res_full, attr).loc[prefix],
            getattr(res_pert, attr).loc[prefix],
            check_exact=True,
            check_names=False,
        )


def test_portfolio_equal_weight_average_of_net_returns():
    df_a = make_ohlcv(300, seed=1)
    df_b = make_ohlcv(300, seed=2)  # same index, different prices
    res_a = backtest_signals(df_a, pd.Series(1.0, index=df_a.index), name="A")
    res_b = backtest_signals(df_b, pd.Series(-0.5, index=df_b.index), name="B")
    port = backtest_portfolio({"A": res_a, "B": res_b})
    assert port.name == "portfolio"
    np.testing.assert_allclose(
        port.net_returns.to_numpy(),
        ((res_a.net_returns + res_b.net_returns) / 2.0).to_numpy(),
        rtol=1e-12, atol=1e-15,
    )
    assert METRIC_KEYS <= set(port.metrics.keys())
