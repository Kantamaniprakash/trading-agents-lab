"""Command-line interface for trading-agents-lab.

Subcommands:
    download   fetch and cache the daily price universe
    baselines  run the paper's five rule baselines + equal-weight portfolio
    train-ml   walk-forward LightGBM alpha model vs buy-and-hold
    agents     LLM multi-agent backtest for one ticker (needs ANTHROPIC_API_KEY)
    report     print a combined summary of previously saved result CSVs

Run as ``python -m tradinglab.cli <cmd> [options]``. Tables are printed to
stdout and CSV/PNG artifacts are written to ``results/``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # must be set before pyplot is imported (headless-safe)

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from tradinglab.backtest.baselines import STRATEGIES  # noqa: E402
from tradinglab.backtest.engine import backtest_portfolio, backtest_signals  # noqa: E402
from tradinglab.backtest.metrics import metrics_table  # noqa: E402
from tradinglab.config import (  # noqa: E402
    DATA_CACHE_DIR,
    DEFAULT_TICKERS,
    LLM_CACHE_DIR,
    RESULTS_DIR,
    AgentConfig,
    BacktestConfig,
    DataConfig,
    MLConfig,
)
from tradinglab.data.prices import fetch_prices  # noqa: E402

# USD per 1M input/output tokens; unknown models fall back to the opus price.
PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
DEFAULT_PRICE_PER_MTOK: tuple[float, float] = (5.0, 25.0)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _positive_int(value: str) -> int:
    """argparse type: integer >= 1, with a clean error message otherwise."""
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: {value!r}") from None
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"must be an integer >= 1 (got {value})")
    return ivalue


def _print_table(table: pd.DataFrame, title: str) -> None:
    """Print a DataFrame as a fixed-width table with 4-decimal floats."""
    out = table.copy()
    if "n_days" in out.columns:
        out["n_days"] = out["n_days"].fillna(0).astype(int)
    print(f"\n=== {title} ===")
    print(out.to_string(float_format=lambda v: f"{v:.4f}"))


def _plot_equity(curves: dict[str, pd.Series], title: str, out_path: Path) -> None:
    """Plot several equity curves on one axes and save a PNG."""
    fig, ax = plt.subplots(figsize=(10, 6))
    for name, eq in curves.items():
        ax.plot(eq.index, eq.values, label=name, linewidth=1.2)
    ax.set_title(title)
    ax.set_ylabel("Equity (growth of $1)")
    ax.legend(loc="best", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def _usage_table(usage: dict) -> pd.DataFrame:
    """Build a per-model token usage/cost table (USD) with a TOTAL row."""
    rows: dict[str, dict] = {}
    for model, u in usage.items():
        in_price, out_price = PRICE_PER_MTOK.get(model, DEFAULT_PRICE_PER_MTOK)
        cost = (u.get("input_tokens", 0) / 1e6) * in_price + (
            u.get("output_tokens", 0) / 1e6
        ) * out_price
        rows[model] = {
            "calls": u.get("calls", 0),
            "cache_hits": u.get("cache_hits", 0),
            "input_tokens": u.get("input_tokens", 0),
            "output_tokens": u.get("output_tokens", 0),
            "cost_usd": cost,
        }
    table = pd.DataFrame.from_dict(rows, orient="index")
    if not table.empty:
        total = table.sum(numeric_only=True)
        total.name = "TOTAL"
        table = pd.concat([table, total.to_frame().T])
        for col in ("calls", "cache_hits", "input_tokens", "output_tokens"):
            table[col] = table[col].astype(int)
    return table


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------

def cmd_download(args: argparse.Namespace) -> None:
    """Fetch/cache the universe and print row counts per ticker."""
    cfg = DataConfig(tickers=list(args.tickers), start=args.start, end=args.end)
    prices = fetch_prices(
        cfg.tickers, cfg.start, cfg.end, cache_dir=cfg.cache_dir, force=args.force
    )
    rows = {
        t: {
            "rows": len(df),
            "first": df.index.min().date(),
            "last": df.index.max().date(),
        }
        for t, df in prices.items()
    }
    table = pd.DataFrame.from_dict(rows, orient="index")
    _print_table(table, "Downloaded universe")
    print(f"\ncached under: {cfg.cache_dir}")


def cmd_baselines(args: argparse.Namespace) -> None:
    """Run all five rule baselines per ticker plus an equal-weight portfolio."""
    bt_cfg = BacktestConfig(cost_bps=args.cost_bps)
    rf = bt_cfg.risk_free_rate
    prices = fetch_prices(list(args.tickers), args.start, args.end, cache_dir=DATA_CACHE_DIR)

    results: dict[str, dict] = {}
    for ticker, df in prices.items():
        per_strat = {}
        for strat_name, strat_fn in STRATEGIES.items():
            signal = strat_fn(df)
            per_strat[strat_name] = backtest_signals(
                df, signal, cost_bps=args.cost_bps, rf=rf, name=f"{ticker}/{strat_name}"
            )
        results[ticker] = per_strat
        png = RESULTS_DIR / f"baselines_equity_{ticker}.png"
        _plot_equity(
            {n: r.equity for n, r in per_strat.items()},
            f"{ticker} baselines (net of {args.cost_bps:g} bps)",
            png,
        )
        print(f"saved: {png}")

    named_returns: dict[str, pd.Series] = {}
    for ticker in prices:
        for strat_name, res in results[ticker].items():
            named_returns[f"{ticker}/{strat_name}"] = res.net_returns
    for strat_name in STRATEGIES:
        port = backtest_portfolio(
            {t: results[t][strat_name] for t in prices},
            rf=rf,
            name=f"PORTFOLIO/{strat_name}",
        )
        named_returns[f"PORTFOLIO/{strat_name}"] = port.net_returns

    table = metrics_table(named_returns, rf=rf)
    csv_path = RESULTS_DIR / "baselines_metrics.csv"
    table.to_csv(csv_path)
    _print_table(table, "Baseline metrics (net returns)")
    print(f"\nsaved: {csv_path}")


def cmd_train_ml(args: argparse.Namespace) -> None:
    """Train the walk-forward LightGBM model and backtest its signals."""
    # Heavy imports (lightgbm) deferred so other subcommands stay light.
    from tradinglab.features.dataset import build_dataset
    from tradinglab.models.ml import predictions_to_signals, walk_forward_predict

    ml_cfg = MLConfig(
        horizon=args.horizon, test_start=args.test_start, signal_threshold=args.threshold
    )
    bt_cfg = BacktestConfig(cost_bps=args.cost_bps, long_only=args.long_only)
    rf = bt_cfg.risk_free_rate

    prices = fetch_prices(list(args.tickers), args.start, args.end, cache_dir=DATA_CACHE_DIR)
    X, y, _y_bin = build_dataset(prices, horizon=ml_cfg.horizon)
    print(f"dataset: {len(X)} rows, {X.shape[1]} features, horizon={ml_cfg.horizon}")

    wf = walk_forward_predict(X, y, ml_cfg)
    print(f"walk-forward: {wf.n_refits} refits, {len(wf.predictions)} test predictions")

    pred_path = RESULTS_DIR / "ml_predictions.csv"
    wf.predictions.rename("prediction").to_csv(pred_path)
    print(f"saved: {pred_path}")

    signals = predictions_to_signals(
        wf.predictions, threshold=args.threshold, long_only=args.long_only
    )

    test_start = pd.Timestamp(ml_cfg.test_start)
    ml_results: dict[str, object] = {}
    bh_results: dict[str, object] = {}
    named_returns: dict[str, pd.Series] = {}
    for ticker, signal in signals.items():
        df = prices.get(ticker)
        if df is None:
            continue
        df_test = df.loc[df.index >= test_start]
        if df_test.empty:
            continue
        ml_res = backtest_signals(
            df_test,
            signal,
            cost_bps=args.cost_bps,
            rf=rf,
            allow_short=not args.long_only,
            name=f"{ticker}/ml",
        )
        bh_res = backtest_signals(
            df_test,
            pd.Series(1.0, index=df_test.index),
            cost_bps=args.cost_bps,
            rf=rf,
            name=f"{ticker}/buy_hold",
        )
        ml_results[ticker] = ml_res
        bh_results[ticker] = bh_res
        named_returns[f"{ticker}/ml"] = ml_res.net_returns
        named_returns[f"{ticker}/buy_hold"] = bh_res.net_returns
        png = RESULTS_DIR / f"ml_equity_{ticker}.png"
        _plot_equity(
            {"ml": ml_res.equity, "buy_hold": bh_res.equity},
            f"{ticker} ML vs buy & hold (test from {ml_cfg.test_start})",
            png,
        )
        print(f"saved: {png}")

    if ml_results:
        port_ml = backtest_portfolio(ml_results, rf=rf, name="PORTFOLIO/ml")
        port_bh = backtest_portfolio(bh_results, rf=rf, name="PORTFOLIO/buy_hold")
        named_returns["PORTFOLIO/ml"] = port_ml.net_returns
        named_returns["PORTFOLIO/buy_hold"] = port_bh.net_returns

    table = metrics_table(named_returns, rf=rf)
    csv_path = RESULTS_DIR / "ml_metrics.csv"
    table.to_csv(csv_path)
    _print_table(table, "ML walk-forward metrics (net returns, test window)")

    importances = wf.feature_importance.sort_values("gain", ascending=False).head(15)
    _print_table(importances, "Top-15 feature importances (avg gain)")
    print(f"\nsaved: {csv_path}")


def cmd_agents(args: argparse.Namespace) -> None:
    """Run the LLM multi-agent backtest for one ticker."""
    # Heavy imports (anthropic) deferred so key-less environments can still
    # run download/baselines/train-ml/report.
    from tradinglab.agents.graph import run_agent_backtest
    from tradinglab.agents.llm import LLMClient

    agent_cfg = AgentConfig()
    bt_cfg = BacktestConfig(cost_bps=args.cost_bps, long_only=args.long_only)
    rf = bt_cfg.risk_free_rate

    # Fetch history well before the decision window for indicator warm-up.
    # Compare as timestamps, not strings: non-ISO inputs like "6/1/2010" sort
    # wrong lexicographically.
    fetch_start = min(DataConfig().start, args.start, key=pd.Timestamp)
    prices = fetch_prices(args.ticker, fetch_start, args.end, cache_dir=DATA_CACHE_DIR)
    df = prices[args.ticker]

    window = df.loc[pd.Timestamp(args.start): pd.Timestamp(args.end)]
    if window.empty:
        print(f"error: no trading days for {args.ticker} in [{args.start}, {args.end}]")
        sys.exit(1)
    # Decision-date subsetting (every/max-days) is informational here; the
    # evaluation window stays the full user-requested [start, end] so the
    # final decision still accrues returns.
    decision_dates = window.index[:: args.every]
    if args.max_days is not None:
        decision_dates = decision_dates[: args.max_days]
    print(
        f"{args.ticker}: {len(decision_dates)} decision days "
        f"(last {decision_dates[-1].strftime('%Y-%m-%d')}), evaluation window "
        f"[{args.start} .. {args.end}] every={args.every} anonymize={args.anonymize}"
    )

    fundamentals: str | None = None
    news: list[str] | None = None
    if args.live:
        from tradinglab.data.fundamentals import fetch_fundamentals_snapshot, format_fundamentals
        from tradinglab.data.news import fetch_current_news

        snapshot = fetch_fundamentals_snapshot(args.ticker)
        fundamentals = format_fundamentals(snapshot)
        items = fetch_current_news(args.ticker)
        news = [
            f"{item.get('date', '')} [{item.get('publisher', '')}] {item.get('title', '')}"
            for item in items
        ] or None
        print("\n--- live fundamentals ---")
        print(fundamentals)
        print("--- live news ---")
        print("\n".join(news) if news else "(none)")

    out_dir = RESULTS_DIR / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)

    client = LLMClient(agent_cfg, cache_dir=LLM_CACHE_DIR)
    kwargs = dict(
        out_dir=out_dir,
        every=args.every,
        max_days=args.max_days,
        anonymize=args.anonymize,
        cost_bps=args.cost_bps,
        rf=rf,
        long_only=args.long_only,
    )
    if args.live:
        kwargs["fundamentals"] = fundamentals
        kwargs["news"] = news
        print(
            "note: live fundamentals/news (printed above) are injected into "
            "agent prompts — CURRENT data, not point-in-time"
        )

    try:
        result, logs = run_agent_backtest(
            df, args.ticker, args.start, args.end, client, agent_cfg, **kwargs
        )
    except RuntimeError as exc:  # missing API key surfaces here
        print(f"error: {exc}")
        sys.exit(1)

    bench_df = df.loc[pd.Timestamp(args.start): pd.Timestamp(args.end)]
    bench = backtest_signals(
        bench_df,
        pd.Series(1.0, index=bench_df.index),
        cost_bps=args.cost_bps,
        rf=rf,
        name="buy_hold",
    )

    table = metrics_table(
        {f"{args.ticker}/agents": result.net_returns, f"{args.ticker}/buy_hold": bench.net_returns},
        rf=rf,
    )
    csv_path = RESULTS_DIR / f"agents_{args.ticker}_metrics.csv"
    table.to_csv(csv_path)
    _print_table(table, f"Agent backtest metrics — {args.ticker}")

    png = RESULTS_DIR / f"agents_equity_{args.ticker}.png"
    _plot_equity(
        {"agents": result.equity, "buy_hold": bench.equity},
        f"{args.ticker} agents vs buy & hold",
        png,
    )

    _print_table(_usage_table(client.usage), "Token usage / cost (USD)")
    print(f"\ndecisions logged: {len(logs)}")
    print(f"saved: {csv_path}")
    print(f"saved: {png}")
    print(f"transcripts: {out_dir}")


def cmd_report(args: argparse.Namespace) -> None:
    """Load saved result CSVs and print a combined summary."""
    found = False
    sections = [
        (RESULTS_DIR / "baselines_metrics.csv", "Baselines"),
        (RESULTS_DIR / "ml_metrics.csv", "ML walk-forward"),
    ]
    sections += [
        (path, f"Agents — {path.stem.replace('agents_', '').replace('_metrics', '')}")
        for path in sorted(RESULTS_DIR.glob("agents_*_metrics.csv"))
    ]
    for path, title in sections:
        if not path.exists():
            continue
        found = True
        table = pd.read_csv(path, index_col=0)
        _print_table(table, title)
    if not found:
        print(
            "no saved results found in "
            f"{RESULTS_DIR} — run baselines/train-ml/agents first"
        )


# ---------------------------------------------------------------------------
# parser / entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="tradinglab",
        description="TradingAgents research lab: baselines, ML alpha, LLM agents.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    data_defaults = DataConfig()
    ml_defaults = MLConfig()
    bt_defaults = BacktestConfig()

    p_dl = sub.add_parser("download", help="fetch and cache the price universe")
    p_dl.add_argument("--tickers", nargs="+", default=list(DEFAULT_TICKERS))
    p_dl.add_argument("--start", default=data_defaults.start)
    p_dl.add_argument("--end", default=None)
    p_dl.add_argument("--force", action="store_true", help="re-download, ignore cache")
    p_dl.set_defaults(func=cmd_download)

    p_bl = sub.add_parser("baselines", help="run the five rule baselines")
    p_bl.add_argument("--tickers", nargs="+", default=list(DEFAULT_TICKERS))
    p_bl.add_argument("--start", default=data_defaults.start)
    p_bl.add_argument("--end", default=None)
    p_bl.add_argument("--cost-bps", type=float, default=bt_defaults.cost_bps)
    p_bl.set_defaults(func=cmd_baselines)

    p_ml = sub.add_parser("train-ml", help="walk-forward LightGBM alpha model")
    p_ml.add_argument("--tickers", nargs="+", default=list(DEFAULT_TICKERS))
    p_ml.add_argument("--start", default=data_defaults.start)
    p_ml.add_argument("--end", default=None)
    p_ml.add_argument("--horizon", type=int, default=ml_defaults.horizon)
    p_ml.add_argument("--threshold", type=float, default=ml_defaults.signal_threshold)
    p_ml.add_argument("--test-start", default=ml_defaults.test_start)
    p_ml.add_argument("--long-only", action="store_true")
    p_ml.add_argument("--cost-bps", type=float, default=bt_defaults.cost_bps)
    p_ml.set_defaults(func=cmd_train_ml)

    p_ag = sub.add_parser("agents", help="LLM multi-agent backtest (needs API key)")
    p_ag.add_argument("--ticker", required=True)
    p_ag.add_argument("--start", required=True)
    p_ag.add_argument("--end", required=True)
    p_ag.add_argument(
        "--every", type=_positive_int, default=1, help="decide every N-th trading day (>= 1)"
    )
    p_ag.add_argument("--anonymize", action="store_true", help="mask ticker/dates from the LLM")
    p_ag.add_argument("--live", action="store_true", help="inject current fundamentals + news")
    p_ag.add_argument("--long-only", action="store_true")
    p_ag.add_argument(
        "--max-days", type=_positive_int, default=None,
        help="cap number of decision days (>= 1)",
    )
    p_ag.add_argument("--cost-bps", type=float, default=bt_defaults.cost_bps)
    p_ag.set_defaults(func=cmd_agents)

    p_rp = sub.add_parser("report", help="combined summary of saved results")
    p_rp.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: parse args, ensure results dir, dispatch."""
    args = build_parser().parse_args(argv)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    args.func(args)


if __name__ == "__main__":
    main()
