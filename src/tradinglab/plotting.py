"""Publication-style figures modeled on the TradingAgents paper.

Two figure types:

- :func:`plot_strategy_comparison` — cumulative-return comparison of several
  strategies with one optionally highlighted (paper Fig. 7 style).
- :func:`plot_transaction_history` — three stacked panels: portfolio value and
  exposure, per-trade profit/loss dots, and a candlestick chart with volume and
  buy/sell decision markers (paper Fig. 6 style).

Also provides :func:`parse_transcript_decisions` to recover the structured
decision log from saved agent transcripts (for runs that predate the
decisions CSV).
"""
from __future__ import annotations

import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

_HIGHLIGHT_COLOR = "#6b3f2a"
_UP_COLOR = "#2ca02c"
_DOWN_COLOR = "#d62728"
_BASE_CAPITAL = 100_000.0


def _pretty(name: str) -> str:
    return name.replace("_", " ")


def plot_strategy_comparison(
    equity_curves: dict[str, pd.Series],
    title: str,
    save_path: Path | str,
    highlight: str | None = None,
) -> None:
    """Cumulative-return comparison chart (paper Fig. 7 style).

    ``equity_curves`` maps strategy name -> equity Series; each curve is
    normalized to start at 1.0. ``highlight`` names the curve drawn last,
    thicker and in a distinctive color with a bold legend entry.
    """
    fig, ax = plt.subplots(figsize=(11, 6.5), dpi=150)
    ordered = [n for n in equity_curves if n != highlight]
    if highlight is not None and highlight in equity_curves:
        ordered.append(highlight)

    for name in ordered:
        curve = equity_curves[name].dropna()
        if curve.empty:
            continue
        curve = curve / curve.iloc[0]
        if name == highlight:
            ax.plot(curve.index, curve.values, lw=2.4, color=_HIGHLIGHT_COLOR,
                    label=_pretty(name), zorder=5)
        else:
            ax.plot(curve.index, curve.values, lw=1.4, alpha=0.95,
                    label=_pretty(name))

    ax.set_title(title, fontsize=13)
    ax.set_ylabel("Cumulative Return")
    ax.set_xlabel("Date")
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.set_facecolor("white")
    legend = ax.legend(title="Strategies", loc="upper left", framealpha=0.9)
    if highlight is not None:
        for text in legend.get_texts():
            if text.get_text() == _pretty(highlight):
                text.set_fontweight("bold")
    for lab in ax.get_xticklabels():
        lab.set_rotation(30)
        lab.set_horizontalalignment("right")
    fig.tight_layout()
    fig.savefig(save_path)
    plt.close(fig)


def _net_returns(df: pd.DataFrame, positions: pd.Series,
                 cost_bps: float) -> pd.Series:
    """Engine-consistent net returns for an already-shifted position series."""
    pos = positions.reindex(df.index).fillna(0.0)
    mkt = df["close"].pct_change().fillna(0.0)
    turnover = pos.diff().abs()
    turnover.iloc[0] = abs(pos.iloc[0])
    return pos * mkt - (cost_bps / 1e4) * turnover


def _trade_segments(positions: pd.Series) -> list[tuple[int, int, float]]:
    """Maximal constant-position segments with position != 0.

    Returns (start_iloc, end_iloc, position) tuples, end inclusive.
    """
    segments: list[tuple[int, int, float]] = []
    vals = positions.to_numpy()
    start = None
    for i, v in enumerate(vals):
        if start is None:
            if v != 0:
                start = i
        elif v != vals[start]:
            segments.append((start, i - 1, float(vals[start])))
            start = i if v != 0 else None
    if start is not None:
        segments.append((start, len(vals) - 1, float(vals[start])))
    return segments


def plot_transaction_history(
    df: pd.DataFrame,
    positions: pd.Series,
    save_path: Path | str,
    title: str = "",
    decisions: pd.DataFrame | None = None,
    cost_bps: float = 10.0,
) -> None:
    """Three-panel transaction history (paper Fig. 6 style).

    Panels: portfolio value + exposure; per-trade net P/L dots; candlesticks
    with volume and BUY/SELL decision markers. ``positions`` is the engine's
    already-shifted daily position series; ``decisions`` optionally carries
    columns ``date, action, size`` for the markers.
    """
    pos = positions.reindex(df.index).fillna(0.0)
    net = _net_returns(df, pos, cost_bps)
    equity = (1.0 + net).cumprod()
    value = _BASE_CAPITAL * equity

    x = np.arange(len(df))
    fig, (ax_val, ax_pl, ax_px) = plt.subplots(
        3, 1, figsize=(13, 9), dpi=150, sharex=True,
        gridspec_kw={"height_ratios": [1, 1, 3]},
    )

    # --- top: portfolio value + exposure -------------------------------
    ax_val.plot(x, value.values, color="#1f77b4", lw=1.6,
                label=f"portfolio value  {value.iloc[-1]:,.2f}")
    ax_val.axhline(_BASE_CAPITAL, color="grey", lw=0.8, ls=":")
    ax_exp = ax_val.twinx()
    ax_exp.step(x, pos.values, where="post", color="#d62728", lw=1.0,
                alpha=0.8, label="exposure")
    ax_exp.set_ylim(-1.1, 1.1)
    ax_exp.set_ylabel("exposure", fontsize=8, color="#d62728")
    ax_exp.tick_params(axis="y", labelsize=7, colors="#d62728")
    ax_val.set_ylabel("value ($)", fontsize=8)
    ax_val.tick_params(labelsize=7)
    handles1, labels1 = ax_val.get_legend_handles_labels()
    handles2, labels2 = ax_exp.get_legend_handles_labels()
    ax_val.legend(handles1 + handles2, labels1 + labels2, loc="upper left",
                  fontsize=7, framealpha=0.9)
    ax_val.grid(True, linestyle="--", alpha=0.3)

    # --- middle: per-trade P/L dots -------------------------------------
    ax_pl.axhline(0.0, color="grey", lw=0.8, ls="--")
    for start, end, _p in _trade_segments(pos):
        seg_net = net.iloc[start:end + 1]
        pl = _BASE_CAPITAL * ((1.0 + seg_net).prod() - 1.0)
        color = "#1f4fd6" if pl >= 0 else "#d62728"
        ax_pl.scatter(end, pl, s=28, color=color, zorder=3)
    ax_pl.set_ylabel("trade P/L ($)", fontsize=8)
    ax_pl.tick_params(labelsize=7)
    ax_pl.grid(True, linestyle="--", alpha=0.3)
    handles = [
        plt.Line2D([], [], marker="o", ls="", color="#1f4fd6", label="positive"),
        plt.Line2D([], [], marker="o", ls="", color="#d62728", label="negative"),
    ]
    ax_pl.legend(handles=handles, title="Trades — net P/L", loc="upper left",
                 fontsize=7, title_fontsize=7, framealpha=0.9)

    # --- bottom: candlesticks + volume + decision markers ---------------
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    up = c >= o
    body_w = 0.6

    ax_px.vlines(x, l, h, color=np.where(up, _UP_COLOR, _DOWN_COLOR),
                 lw=0.8, zorder=2)
    for i in range(len(df)):
        lo, hi = sorted((o[i], c[i]))
        height = max(hi - lo, 1e-9)
        ax_px.add_patch(Rectangle(
            (x[i] - body_w / 2, lo), body_w, height,
            facecolor="white" if up[i] else _DOWN_COLOR,
            edgecolor=_UP_COLOR if up[i] else _DOWN_COLOR,
            lw=0.8, zorder=3,
        ))
    pad = (h.max() - l.min()) * 0.04
    ax_px.set_ylim(l.min() - 3.5 * pad, h.max() + pad)
    ax_px.set_ylabel("price", fontsize=8)
    ax_px.tick_params(labelsize=7)
    ax_px.grid(True, linestyle="--", alpha=0.3)

    ax_vol = ax_px.twinx()
    vol = df["volume"].to_numpy(dtype=float)
    ax_vol.bar(x, vol, width=0.8, color="grey", alpha=0.45, zorder=1)
    ax_vol.set_ylim(0, vol.max() * 5.0)   # confine bars to bottom ~20%
    ax_vol.set_yticks([])
    ax_vol.set_ylabel("volume", fontsize=8, color="grey")

    if decisions is not None and len(decisions):
        dts = pd.to_datetime(decisions["date"])
        idx = df.index.get_indexer(dts, method="nearest")
        for (_, row), i in zip(decisions.iterrows(), idx):
            if i < 0:
                continue
            action = str(row["action"]).upper()
            if action == "BUY":
                ax_px.scatter(x[i], l[i] - 1.5 * pad, marker="^", s=90,
                              color=_UP_COLOR, zorder=5)
            elif action == "SELL":
                ax_px.scatter(x[i], h[i] + 0.6 * pad, marker="v", s=90,
                              color=_DOWN_COLOR, zorder=5)
        marker_handles = [
            plt.Line2D([], [], marker="^", ls="", color=_UP_COLOR, label="buy"),
            plt.Line2D([], [], marker="v", ls="", color=_DOWN_COLOR, label="sell"),
        ]
        ax_px.legend(handles=marker_handles, loc="upper right", fontsize=7,
                     framealpha=0.9)

    # date ticks on integer positions (no weekend gaps)
    n_ticks = min(9, len(df))
    tick_pos = np.linspace(0, len(df) - 1, n_ticks).astype(int)
    ax_px.set_xticks(tick_pos)
    ax_px.set_xticklabels(
        [df.index[i].strftime("%Y-%m-%d") for i in tick_pos],
        rotation=30, ha="right", fontsize=7,
    )
    ax_px.set_xlim(-1, len(df))

    if title:
        fig.suptitle(title, fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.97) if title else None)
    fig.savefig(save_path)
    plt.close(fig)


_DECISION_RE = re.compile(
    r"\*\*Final decision:\s*(BUY|SELL|HOLD)\*\*(?:\s*\(size\s*([0-9.]+)\))?",
    re.IGNORECASE,
)
_VERDICT_RE = re.compile(r"\*\*Facilitator verdict:\*\*\s*([A-Z]+)")


def parse_transcript_decisions(transcripts_dir: Path | str,
                               ticker: str) -> pd.DataFrame:
    """Recover the decision log from saved transcripts.

    Returns a DataFrame with columns ``date, action, size, verdict,
    position_after`` (chronological), replaying HOLD-carry semantics for
    ``position_after``. Files are ``{ticker}_{YYYY-MM-DD}.md``.
    """
    transcripts_dir = Path(transcripts_dir)
    rows: list[dict] = []
    for path in sorted(transcripts_dir.glob(f"{ticker}_*.md")):
        date = path.stem[len(ticker) + 1:]
        text = path.read_text(encoding="utf-8", errors="replace")
        m = _DECISION_RE.search(text)
        if not m:
            continue
        action = m.group(1).upper()
        size = float(m.group(2)) if m.group(2) else 0.0
        v = _VERDICT_RE.search(text)
        rows.append({
            "date": date,
            "action": action,
            "size": size,
            "verdict": v.group(1) if v else "",
        })

    position = 0.0
    for row in rows:
        if row["action"] == "BUY":
            position = row["size"]
        elif row["action"] == "SELL":
            position = -row["size"]
        row["position_after"] = position
    return pd.DataFrame(
        rows, columns=["date", "action", "size", "verdict", "position_after"],
    )
