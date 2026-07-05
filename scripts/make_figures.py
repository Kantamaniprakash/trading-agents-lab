"""Generate the README figures from committed results + cached price data.

Reproduces the agent-run equity curve exactly the way the CLI does (same
signal construction, same backtest engine) so the figures can never drift
from the reported numbers.

Usage:  python scripts/make_figures.py
Writes: results/figures/agents_aapl_2026.png
        results/figures/baselines_sharpe.png
        results/figures/agents_aapl_2026_comparison.png   (paper Fig-7 style)
        results/figures/agents_aapl_2026_txn.png          (paper Fig-6 style)
        results/figures/baselines_comparison_{AAPL,NVDA,TSLA}.png
"""
from __future__ import annotations

import re
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from tradinglab.backtest.baselines import STRATEGIES
from tradinglab.backtest.engine import backtest_signals
from tradinglab.plotting import (
    parse_transcript_decisions,
    plot_strategy_comparison,
    plot_transaction_history,
)

ROOT = Path(__file__).resolve().parents[1]
FIG_DIR = ROOT / "results" / "figures"

TICKER = "AAPL"
START, END = "2026-03-01", "2026-06-01"
COST_BPS, RF = 10.0, 0.04

# palette: categorical slots for series, status colors for actions, muted chrome
C_SERIES1 = "#2a78d6"   # agents / price line
C_SERIES2 = "#1baf7a"   # buy & hold
C_BUY = "#0ca30c"
C_SELL = "#d03b3b"
C_HOLD = "#898781"
C_INK = "#0b0b0b"
C_INK2 = "#52514e"
C_MUTED = "#898781"
C_GRID = "#e1e0d9"
C_AXIS = "#c3c2b7"
C_SURFACE = "#fcfcfb"

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
    "figure.facecolor": C_SURFACE,
    "axes.facecolor": C_SURFACE,
    "savefig.facecolor": C_SURFACE,
    "axes.edgecolor": C_AXIS,
    "axes.labelcolor": C_INK2,
    "xtick.color": C_MUTED,
    "ytick.color": C_MUTED,
    "text.color": C_INK,
})

DECISION_RE = re.compile(r"\*\*Final decision: (BUY|SELL|HOLD)\*\*\s*\(size ([0-9.]+)\)")


def style_axis(ax, ygrid: bool = True):
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(C_AXIS)
    if ygrid:
        ax.grid(axis="y", color=C_GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0, labelsize=9)


def load_decisions() -> pd.DataFrame:
    rows = []
    for path in sorted((ROOT / "results" / "transcripts").glob(f"{TICKER}_*.md")):
        date = path.stem.split("_", 1)[1]
        m = DECISION_RE.search(path.read_text(encoding="utf-8"))
        if m:
            rows.append({"date": pd.Timestamp(date), "action": m.group(1),
                         "size": float(m.group(2))})
    return pd.DataFrame(rows).set_index("date")


def load_prices() -> pd.DataFrame:
    cached = sorted((ROOT / "data" / "cache").glob(f"{TICKER}_*.parquet"))
    if not cached:
        raise SystemExit(f"no cached prices for {TICKER}; run `cli download` first")
    df = pd.read_parquet(cached[0])
    return df.loc[START:END]


def agents_signal(decisions: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    # identical to the CLI: BUY -> +size, SELL -> -size, HOLD -> carry forward
    raw = pd.Series(
        [d.size if d.action == "BUY" else -d.size if d.action == "SELL" else None
         for d in decisions.itertuples()],
        index=decisions.index, dtype=float)
    return raw.reindex(index).ffill().fillna(0.0)


def fig_agents(prices: pd.DataFrame, decisions: pd.DataFrame) -> None:
    signal = agents_signal(decisions, prices.index)
    agents = backtest_signals(prices, signal, cost_bps=COST_BPS, rf=RF, name="agents")
    hold = backtest_signals(prices, pd.Series(1.0, index=prices.index),
                            cost_bps=COST_BPS, rf=RF, name="buy_hold")

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11.5, 7.6), dpi=160, sharex=True,
        gridspec_kw={"height_ratios": [1.5, 1], "hspace": 0.12})

    close = prices["close"]
    ax1.plot(close.index, close, color=C_SERIES1, linewidth=2)
    marker_for = {"BUY": ("^", C_BUY), "SELL": ("v", C_SELL), "HOLD": ("o", C_HOLD)}
    for action, (marker, color) in marker_for.items():
        days = decisions[decisions["action"] == action]
        pts = close.reindex(days.index, method="ffill")
        face = C_SURFACE if action == "HOLD" else color
        ax1.scatter(pts.index, pts, marker=marker, s=64, color=face,
                    edgecolors=color, linewidths=1.4, zorder=3,
                    label=f"{action.title()} ({len(days)})")
        if action != "HOLD":  # label position size next to each trade
            for date, px in pts.items():
                size = decisions.loc[date, "size"]
                sign = "+" if action == "BUY" else "−"
                ax1.annotate(f"{sign}{size:.2f}", (date, px), fontsize=7.5,
                             color=C_INK2, xytext=(0, 10 if action == "BUY" else -14),
                             textcoords="offset points", ha="center")
    style_axis(ax1)
    ax1.set_ylabel("AAPL close ($)", fontsize=9)
    ax1.legend(loc="upper left", frameon=False, fontsize=8.5)
    ax1.set_title(
        f"Anonymized agent run — {TICKER}, {START} → {END} "
        f"(weekly decisions, net of {COST_BPS:.0f} bps)",
        fontsize=12, loc="left", color=C_INK, pad=12)

    for res, color, label in ((agents, C_SERIES1, "Agents"),
                              (hold, C_SERIES2, "Buy & hold")):
        ax2.plot(res.equity.index, res.equity, color=color, linewidth=2, label=label)
        ax2.annotate(f" {label} {res.equity.iloc[-1] - 1:+.1%}",
                     (res.equity.index[-1], res.equity.iloc[-1]),
                     fontsize=8.5, color=color, va="center")
    ax2.axhline(1.0, color=C_AXIS, linewidth=0.8)
    style_axis(ax2)
    ax2.set_ylabel("Equity (growth of $1)", fontsize=9)
    ax2.legend(loc="upper left", frameon=False, fontsize=8.5)
    ax2.margins(x=0.09)

    fig.savefig(FIG_DIR / "agents_aapl_2026.png", bbox_inches="tight")
    plt.close(fig)


def fig_baselines_sharpe() -> None:
    metrics = pd.read_csv(ROOT / "results" / "baselines_metrics.csv")
    port = metrics[metrics["strategy"].str.startswith("PORTFOLIO/")].copy()
    port["name"] = (port["strategy"].str.split("/").str[1]
                    .map({"buy_hold": "Buy & hold", "macd": "MACD",
                          "kdj_rsi": "KDJ + RSI", "zmr": "ZMR",
                          "sma_cross": "SMA cross"}))
    port = port.sort_values("sharpe")

    fig, ax = plt.subplots(figsize=(9.5, 4.2), dpi=160)
    colors = [C_SERIES1 if s >= 0 else C_SELL for s in port["sharpe"]]
    bars = ax.barh(port["name"], port["sharpe"], color=colors, height=0.62)
    for bar, sharpe in zip(bars, port["sharpe"]):
        inside = sharpe < 0
        ax.annotate(f"{sharpe:.2f}",
                    (sharpe, bar.get_y() + bar.get_height() / 2),
                    xytext=(6 if not inside else -6, 0), textcoords="offset points",
                    ha="left" if not inside else "right", va="center",
                    fontsize=9, color=C_INK2)
    ax.axvline(0, color=C_AXIS, linewidth=1)
    style_axis(ax, ygrid=False)
    ax.grid(axis="x", color=C_GRID, linewidth=0.8)
    ax.tick_params(axis="y", labelsize=10, labelcolor=C_INK)
    ax.set_xlabel("Sharpe ratio (net of 10 bps, vs 4% risk-free)", fontsize=9)
    ax.margins(x=0.12)
    ax.set_title(
        "The paper's rule baselines over an honest horizon — "
        "equal-weight 10-ticker portfolio, 2015 → mid-2026",
        fontsize=11.5, loc="left", color=C_INK, pad=12)

    fig.savefig(FIG_DIR / "baselines_sharpe.png", bbox_inches="tight")
    plt.close(fig)


def fig_paper_style(prices: pd.DataFrame) -> None:
    """Paper Fig-7 comparison + Fig-6 transaction history for the agent run."""
    decisions = parse_transcript_decisions(ROOT / "results" / "transcripts", TICKER)
    signal = agents_signal(
        decisions.assign(date=pd.to_datetime(decisions["date"]))
        .set_index("date"),
        prices.index,
    )
    agents = backtest_signals(prices, signal, cost_bps=COST_BPS, rf=RF,
                              name="agent_desk")
    curves = {"agent_desk": agents.equity}
    for name, strat in STRATEGIES.items():
        res = backtest_signals(prices, strat(prices), cost_bps=COST_BPS, rf=RF,
                               name=name)
        curves[name] = res.equity
    plot_strategy_comparison(
        curves,
        f"Strategy Comparison — Cumulative Returns for {TICKER} "
        f"({START} → {END}, net of {COST_BPS:.0f} bps)",
        FIG_DIR / "agents_aapl_2026_comparison.png",
        highlight="agent_desk",
    )
    plot_transaction_history(
        prices, agents.positions,
        FIG_DIR / "agents_aapl_2026_txn.png",
        title=f"Agent desk — transaction history for {TICKER} "
              f"[{START} … {END}]",
        decisions=decisions, cost_bps=COST_BPS,
    )


def fig_baseline_comparisons() -> None:
    """Full-history Fig-7 style baseline comparison for three tickers."""
    for ticker in ("AAPL", "NVDA", "TSLA"):
        cached = sorted((ROOT / "data" / "cache").glob(f"{ticker}_*.parquet"))
        if not cached:
            continue
        df = pd.read_parquet(cached[0])
        curves = {}
        for name, strat in STRATEGIES.items():
            res = backtest_signals(df, strat(df), cost_bps=COST_BPS, rf=RF,
                                   name=name)
            curves[name] = res.equity
        plot_strategy_comparison(
            curves,
            f"Strategy Comparison — Cumulative Returns for {ticker} "
            f"(2015 → mid-2026, net of {COST_BPS:.0f} bps)",
            FIG_DIR / f"baselines_comparison_{ticker}.png",
        )


def main() -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    decisions = load_decisions()
    prices = load_prices()
    fig_agents(prices, decisions)
    fig_baselines_sharpe()
    fig_paper_style(prices)
    fig_baseline_comparisons()
    for name in ("agents_aapl_2026.png", "baselines_sharpe.png",
                 "agents_aapl_2026_comparison.png", "agents_aapl_2026_txn.png",
                 "baselines_comparison_AAPL.png"):
        print(f"wrote {FIG_DIR / name}")


if __name__ == "__main__":
    main()
