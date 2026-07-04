"""Vectorized daily backtest engine.

This module is the ONLY place where signals are shifted into positions:
a signal computed at the close of day ``t`` is held during day ``t+1``.
Strategies must never shift their own signals.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tradinglab.backtest.metrics import compute_metrics, equity_curve


@dataclass
class BacktestResult:
    """Container for one backtest run (per-ticker or portfolio)."""

    name: str
    positions: pd.Series
    gross_returns: pd.Series
    net_returns: pd.Series
    equity: pd.Series
    turnover: pd.Series
    metrics: dict


def backtest_signals(
    df: pd.DataFrame,
    signal: pd.Series,
    cost_bps: float = 10.0,
    rf: float = 0.0,
    allow_short: bool = True,
    name: str = "strategy",
) -> BacktestResult:
    """Backtest a close-to-close signal on one ticker.

    The signal is reindexed to ``df.index``, forward-filled (sparse signals
    carry the last decision), NaN-filled with 0, and clipped to [-1, 1]
    (or [0, 1] when ``allow_short=False``). Positions are the signal
    shifted one day; the position held during day ``t`` earns
    ``close[t]/close[t-1] - 1``. Costs are ``cost_bps/1e4`` per unit
    turnover, with the first entry charged in full.
    """
    sig = signal.reindex(df.index).ffill().fillna(0.0).astype(float)
    lo = -1.0 if allow_short else 0.0
    sig = sig.clip(lo, 1.0)

    position = sig.shift(1).fillna(0.0)
    market_ret = df["close"].pct_change().fillna(0.0)
    gross = position * market_ret

    turnover = position.diff().abs().fillna(position.abs())
    cost = (cost_bps / 1e4) * turnover
    net = gross - cost

    return BacktestResult(
        name=name,
        positions=position,
        gross_returns=gross,
        net_returns=net,
        equity=equity_curve(net),
        turnover=turnover,
        metrics=compute_metrics(net, rf=rf),
    )


def backtest_portfolio(
    results: dict[str, BacktestResult],
    rf: float = 0.0,
    name: str = "portfolio",
) -> BacktestResult:
    """Equal-weight portfolio across per-ticker backtest results.

    Net (and gross) returns are averaged over ALL tickers in ``results``
    on the union of their indices; a ticker with no data on a given day
    contributes 0 for that day. Positions and turnover are the
    cross-ticker mean and are approximate (they ignore the daily
    rebalancing implied by equal weighting).
    """
    if not results:
        raise ValueError("backtest_portfolio requires at least one BacktestResult")

    def _mean_over_tickers(attr: str) -> pd.Series:
        frame = pd.DataFrame({t: getattr(r, attr) for t, r in results.items()})
        return frame.sort_index().fillna(0.0).mean(axis=1)

    net = _mean_over_tickers("net_returns")
    gross = _mean_over_tickers("gross_returns")
    positions = _mean_over_tickers("positions")
    turnover = _mean_over_tickers("turnover")

    return BacktestResult(
        name=name,
        positions=positions,
        gross_returns=gross,
        net_returns=net,
        equity=equity_curve(net),
        turnover=turnover,
        metrics=compute_metrics(net, rf=rf),
    )
