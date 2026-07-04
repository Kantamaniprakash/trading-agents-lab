"""Performance metrics for daily return series.

Conventions: returns are simple daily returns; division-by-zero cases
(zero volatility, zero drawdown, no non-zero-return days, empty input)
yield ``np.nan`` rather than raising.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

METRIC_KEYS = [
    "n_days",
    "cumulative_return",
    "annualized_return",
    "annualized_vol",
    "sharpe",
    "sortino",
    "max_drawdown",
    "calmar",
    "hit_rate",
]


def equity_curve(returns: pd.Series) -> pd.Series:
    """Cumulative equity curve from simple returns: (1 + r).cumprod()."""
    return (1.0 + returns).cumprod()


def max_drawdown(equity: pd.Series) -> float:
    """Maximum peak-to-trough drawdown of an equity curve.

    Returns a positive fraction (e.g. 0.23 for a 23% drawdown), 0.0 for a
    non-decreasing curve, and np.nan for an empty series.
    """
    if len(equity) == 0:
        return float("nan")
    drawdown = 1.0 - equity / equity.cummax()
    return float(drawdown.max())


def compute_metrics(
    returns: pd.Series, rf: float = 0.0, periods_per_year: int = 252
) -> dict:
    """Compute standard performance metrics for a daily return series.

    `rf` is the annualized risk-free rate; it is de-annualized as
    ``rf / periods_per_year`` for excess-return calculations. Undefined
    ratios (zero vol, zero drawdown, no non-zero days) are np.nan.
    """
    n = int(len(returns))
    if n == 0:
        out = {key: float("nan") for key in METRIC_KEYS}
        out["n_days"] = 0
        return out

    rf_daily = rf / periods_per_year
    equity = equity_curve(returns)
    cum = float(equity.iloc[-1] - 1.0)
    ann_return = float((1.0 + cum) ** (periods_per_year / n) - 1.0)

    std = float(returns.std())
    ann_vol = std * np.sqrt(periods_per_year) if std > 0 else float("nan")

    excess = returns - rf_daily
    sharpe = (
        float(excess.mean() / std * np.sqrt(periods_per_year))
        if std > 0
        else float("nan")
    )

    # Textbook downside deviation about zero over ALL observations:
    # sqrt(mean(min(excess, 0)^2)). NaN when there is no downside.
    downside_dev = float(np.sqrt(np.mean(np.minimum(excess.to_numpy(), 0.0) ** 2)))
    sortino = (
        float(excess.mean() / downside_dev * np.sqrt(periods_per_year))
        if downside_dev > 0
        else float("nan")
    )

    # Measure drawdown from initial capital: prepend the inception point 1.0
    # so losses taken before equity first exceeds starting capital count.
    mdd = max_drawdown(pd.Series(np.concatenate(([1.0], equity.to_numpy()))))
    calmar = ann_return / mdd if mdd and mdd > 0 else float("nan")

    nonzero = returns[returns != 0.0]
    hit_rate = (
        float((nonzero > 0).sum() / len(nonzero)) if len(nonzero) > 0 else float("nan")
    )

    return {
        "n_days": n,
        "cumulative_return": cum,
        "annualized_return": ann_return,
        "annualized_vol": ann_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": mdd,
        "calmar": calmar,
        "hit_rate": hit_rate,
    }


def metrics_table(named_returns: dict[str, pd.Series], rf: float = 0.0) -> pd.DataFrame:
    """Metrics for several return series as a DataFrame (one row per name)."""
    rows = {name: compute_metrics(r, rf=rf) for name, r in named_returns.items()}
    table = pd.DataFrame.from_dict(rows, orient="index")
    table = table.reindex(columns=METRIC_KEYS)
    table.index.name = "strategy"
    return table
