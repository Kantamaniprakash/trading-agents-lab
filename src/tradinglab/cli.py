"""Command-line interface for trading-agents-lab.

Subcommands:
    download   fetch and cache the daily price universe
    baselines  run the paper's five rule baselines + equal-weight portfolio
    train-ml   walk-forward LightGBM alpha model vs buy-and-hold
    agents     LLM multi-agent backtest for one ticker (needs ANTHROPIC_API_KEY)
    snapshot   print the market snapshot the agents see for one ticker (no API key)
    desk       one-shot agent decision on the latest close (needs ANTHROPIC_API_KEY)
    probe      memorization probe: can the LLM recall a window? (needs ANTHROPIC_API_KEY)
    report     print a combined summary of previously saved result CSVs

Run as ``python -m tradinglab.cli <cmd> [options]``. Tables are printed to
stdout and CSV/PNG artifacts are written to ``results/``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # must be set before pyplot is imported (headless-safe)

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from tradinglab import __version__  # noqa: E402
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


def _figures_dir() -> Path:
    """Return results/figures, creating it if needed."""
    fig_dir = RESULTS_DIR / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    return fig_dir


def _decisions_frame(logs: list, long_only: bool = False) -> pd.DataFrame:
    """Flatten AgentDayLog records into the decisions-CSV schema.

    Columns (exactly): ``date, action, size, verdict, position_after``.
    ``position_after`` replays the engine's action-to-signal mapping
    (BUY -> +size, SELL -> -size or 0 when long_only, HOLD -> carry the
    prior position), so it is the signal value actually held after each
    decision — including HOLD carry.
    """
    rows: list[dict] = []
    position = 0.0
    for log in logs:
        action = str(log.final_action)
        size = float(log.final_size)
        if action == "BUY":
            position = size
        elif action == "SELL":
            position = 0.0 if long_only else -size
        # anything else (HOLD or unrecognized) carries the prior position
        debate = getattr(log, "debate", None)
        verdict = str(getattr(debate, "verdict", "")) if debate is not None else ""
        rows.append(
            {
                "date": log.date,
                "action": action,
                "size": size,
                "verdict": verdict,
                "position_after": position,
            }
        )
    return pd.DataFrame(
        rows, columns=["date", "action", "size", "verdict", "position_after"]
    )


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
    # Lazy import: plotting pulls in the paper-figure styling stack and is
    # only needed by subcommands that render figures.
    from tradinglab.plotting import plot_strategy_comparison

    bt_cfg = BacktestConfig(cost_bps=args.cost_bps)
    rf = bt_cfg.risk_free_rate
    prices = fetch_prices(list(args.tickers), args.start, args.end, cache_dir=DATA_CACHE_DIR)
    fig_dir = _figures_dir()

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
        comparison_png = fig_dir / f"baselines_comparison_{ticker}.png"
        plot_strategy_comparison(
            {n: r.equity for n, r in per_strat.items()},
            f"{ticker} — baseline strategies (net of {args.cost_bps:g} bps)",
            comparison_png,
        )
        print(f"saved: {comparison_png}")

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

    # --- decisions CSV (columns: date,action,size,verdict,position_after) ---
    decisions = _decisions_frame(logs, long_only=args.long_only)
    dec_path = RESULTS_DIR / f"agents_{args.ticker}_decisions.csv"
    decisions.to_csv(dec_path, index=False)

    # --- paper-style figures (Fig-7 comparison + Fig-6 transaction history) ---
    # Lazy import: keeps the plotting stack out of key-less/light subcommands.
    from tradinglab.plotting import plot_strategy_comparison, plot_transaction_history

    fig_dir = _figures_dir()
    start_tag = pd.Timestamp(args.start).strftime("%Y-%m-%d")
    end_tag = pd.Timestamp(args.end).strftime("%Y-%m-%d")
    cmp_png = fig_dir / f"agents_{args.ticker}_{start_tag}_{end_tag}.png"
    plot_strategy_comparison(
        {"agent_desk": result.equity, "buy_hold": bench.equity},
        f"{args.ticker} — agent desk vs buy & hold "
        f"(net of {args.cost_bps:g} bps)",
        cmp_png,
        highlight="agent_desk",
    )
    txn_png = fig_dir / f"agents_{args.ticker}_{start_tag}_{end_tag}_txn.png"
    plot_transaction_history(
        bench_df,
        result.positions,
        txn_png,
        title=f"{args.ticker} — agent transaction history [{start_tag} .. {end_tag}]",
        decisions=decisions,
        cost_bps=args.cost_bps,
    )

    _print_table(_usage_table(client.usage), "Token usage / cost (USD)")
    print(f"\ndecisions logged: {len(logs)}")
    print(f"saved: {csv_path}")
    print(f"saved: {dec_path}")
    print(f"saved: {png}")
    print(f"saved: {cmp_png}")
    print(f"saved: {txn_png}")
    print(f"transcripts: {out_dir}")


_WRITES_RESULTS = {"baselines", "train-ml", "agents", "desk", "probe"}
_USES_LLM_CACHE = {"agents", "desk", "probe"}


def _exit_unwritable(directory: Path, exc: OSError) -> None:
    print(
        f"error: cannot write to {directory} ({exc.strerror or exc}). "
        "Set TRADINGLAB_HOME to a writable directory and rerun.",
        file=sys.stderr,
    )
    sys.exit(1)


def _require_writable(directory: Path) -> None:
    """Exit 1 with one clear line unless a file can really be created in ``directory``.

    ``mkdir(exist_ok=True)`` succeeds on a directory that exists but is
    read-only, so the only honest test is to write something.
    """
    probe = directory / ".write-test"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        _exit_unwritable(directory, exc)


def _build_desk_snapshot_or_exit(args: argparse.Namespace):
    """Shared snapshot construction for ``snapshot`` and ``desk`` (exit 1 on bad input)."""
    from tradinglab.agents.desk import build_desk_snapshot

    try:
        return build_desk_snapshot(
            args.ticker, date=args.date, live=not args.no_live,
            anonymize=args.anonymize, lookback=AgentConfig().lookback_days,
        )
    except ValueError as exc:  # unknown symbol, empty ticker, date before history
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_snapshot(args: argparse.Namespace) -> None:
    """Print the market snapshot the agents would see for one ticker.

    No LLM call and no API key: this is the data half of the pipeline, for
    inspecting prompts or for an external agent that runs the roles itself.
    """
    from tradinglab.agents.desk import DISCLAIMER, price_phrase, snapshot_to_dict

    desk = _build_desk_snapshot_or_exit(args)
    payload = snapshot_to_dict(desk)
    if args.json:
        # ASCII-only on stdout: headlines carry arbitrary characters, and a
        # Windows pipe (cp1252) would otherwise abort the print mid-document.
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return

    print(payload["context"])
    print("\n--- Snapshot metadata ---")
    ticker_line = f"ticker: {payload['ticker']}"
    about = [payload[key] for key in ("company", "instrument_type", "currency") if payload[key]]
    if about:
        ticker_line += f" ({', '.join(about)})"
    print(ticker_line)
    print(
        f"as of: {payload['as_of']} ({price_phrase(desk)}, "
        f"{payload['history_rows']} daily bars), fetched {payload['fetched_at']}"
    )
    fundamentals = "yes" if payload["has_fundamentals"] else "no"
    if payload["fundamentals_missing"]:
        fundamentals += f" (missing: {', '.join(payload['fundamentals_missing'])})"
    print(f"fundamentals: {fundamentals} | news headlines: {payload['news_headlines']}")
    for note in payload["notes"]:
        print(f"note: {note}")
    print(f"\n{DISCLAIMER}")


def cmd_desk(args: argparse.Namespace) -> None:
    """Run the full agent pipeline once on a single snapshot and report the decision."""
    # Heavy imports (anthropic) deferred so key-less environments can still
    # run download/baselines/train-ml/snapshot/report.
    from tradinglab.agents.desk import decision_to_dict, render_decision_card
    from tradinglab.agents.graph import TradingAgentsPipeline
    from tradinglab.agents.llm import LLMClient

    # With --json, stdout carries the JSON document only; progress goes to stderr.
    info = sys.stderr if args.json else sys.stdout

    desk = _build_desk_snapshot_or_exit(args)

    # Refuse to spend API credit on data that is known to be bad. Whether live
    # inputs were wanted is what the snapshot builder actually did, not a guess
    # from the flags: --date <the newest bar> still asks Yahoo for them.
    problems = []
    if desk.stale:
        problems.append("the newest bar is stale")
    if desk.prices_behind:
        problems.append("the prices are behind the market")
    if desk.live_attempted and not desk.live:
        problems.append("Yahoo returned no fundamentals and no headlines")
    if problems and not args.allow_degraded:
        print(f"error: not running the desk: {'; '.join(problems)}.", file=sys.stderr)
        for note in desk.notes:
            print(f"note: {note}", file=sys.stderr)
        print(
            "Nothing was spent. Retry in a minute, or pass --allow-degraded to "
            "run on this data anyway.",
            file=sys.stderr,
        )
        sys.exit(1)

    # The transcript is the product of a paid run. Prove it can be saved
    # before paying for it (main() has already checked the LLM cache).
    out_dir = RESULTS_DIR / "desk"
    _require_writable(out_dir)

    agent_cfg = AgentConfig(debate_rounds=args.debate_rounds)
    print(
        f"{desk.snapshot.ticker}: running the agent desk as of {desk.as_of} "
        f"(debate rounds={agent_cfg.debate_rounds}, live data={desk.live}, "
        f"anonymize={args.anonymize})",
        file=info,
    )

    client = LLMClient(agent_cfg, cache_dir=LLM_CACHE_DIR)
    try:
        log = TradingAgentsPipeline(client, agent_cfg).decide(desk.snapshot)
    except RuntimeError as exc:  # missing API key surfaces here
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    usage_table = _usage_table(client.usage)
    cost = float(usage_table.loc["TOTAL", "cost_usd"]) if not usage_table.empty else 0.0
    payload = decision_to_dict(log, desk, usage=client.usage, cost_usd=cost)

    # results/desk/ is kept apart from results/transcripts/, which holds
    # backtest decision days that the dashboard replays into equity curves.
    stem = f"{desk.snapshot.ticker}_{desk.as_of}"
    md_path = out_dir / f"{stem}.md"
    json_path = out_dir / f"{stem}.json"
    md_path.write_text(log.to_markdown(), encoding="utf-8")
    payload["transcript_path"] = str(md_path)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    if args.json:
        # ASCII-only on stdout (see cmd_snapshot); the saved file keeps UTF-8.
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    else:
        print()
        print(render_decision_card(log, desk))
        if not usage_table.empty:
            _print_table(usage_table, "Token usage / cost (USD)")
    print(f"\nsaved: {md_path}", file=info)
    print(f"saved: {json_path}", file=info)


def cmd_probe(args: argparse.Namespace) -> None:
    """Probe the backbone LLM for memorized knowledge of a price window."""
    # Heavy imports (anthropic) deferred so key-less environments can still
    # run download/baselines/train-ml/report.
    from tradinglab.agents.llm import LLMClient
    from tradinglab.agents.probe import run_probe

    agent_cfg = AgentConfig()

    # Fetch history from well before the window so the naive carry-forward
    # anchor (last close BEFORE start) exists. Compare as timestamps, not
    # strings: non-ISO inputs like "6/1/2010" sort wrong lexicographically.
    fetch_start = min(DataConfig().start, args.start, key=pd.Timestamp)
    prices = fetch_prices(args.ticker, fetch_start, args.end, cache_dir=DATA_CACHE_DIR)
    df = prices[args.ticker]

    window = df.loc[pd.Timestamp(args.start): pd.Timestamp(args.end)]
    if window.empty:
        print(f"error: no trading days for {args.ticker} in [{args.start}, {args.end}]")
        sys.exit(1)

    model_label = args.model or agent_cfg.deep_model
    print(
        f"probing {model_label} for memorized knowledge of {args.ticker} "
        f"[{args.start} .. {args.end}]: {min(args.n, len(window))} price dates "
        f"+ monthly directions (prompts contain ticker + dates ONLY, no prices)"
    )

    client = LLMClient(agent_cfg, cache_dir=LLM_CACHE_DIR)
    try:
        result = run_probe(
            client, df, args.ticker, args.start, args.end,
            n_dates=args.n, model=args.model,
        )
    except RuntimeError as exc:  # missing API key surfaces here
        print(f"error: {exc}")
        sys.exit(1)

    # --- per-question tables -------------------------------------------------
    if result["price_questions"]:
        price_table = pd.DataFrame(result["price_questions"]).set_index("date")
        price_table["abs_err_pct"] = price_table.pop("ape") * 100.0
        price_table["naive_err_pct"] = price_table.pop("naive_ape") * 100.0
        _print_table(price_table, "Price recall (model answer vs actual close)")
    else:
        print("\n=== Price recall === (no parseable answers)")

    if result["direction_questions"]:
        dir_table = pd.DataFrame(result["direction_questions"]).set_index("month")
        _print_table(dir_table, "Direction recall (monthly rise/fall)")
    else:
        print("\n=== Direction recall === (no parseable answers)")

    # --- summary + verdict ---------------------------------------------------
    s = result["summary"]

    def _pct_or_na(value: float | None) -> str:
        return "n/a" if value is None else f"{value * 100:.2f}%"

    print(f"\n=== Probe summary — {args.ticker} [{result['start']} .. {result['end']}] ===")
    print(f"model:                     {s['model']}")
    print(f"price questions:           {s['n_price']} answered, {s['n_price_skipped']} skipped")
    print(f"price MAPE (model):        {_pct_or_na(s['price_mape'])}")
    print(
        f"price MAPE (naive anchor): {_pct_or_na(s['naive_mape'])}   "
        f"(carry-forward close {s['naive_anchor']:.2f} from {s['naive_anchor_date']})"
    )
    print(f"direction questions:       {s['n_direction']} answered, {s['n_direction_skipped']} skipped")
    print(f"direction hit rate:        {_pct_or_na(s['direction_hit_rate'])} (chance = 50%)")
    print(f"high-confidence answers:   {s['n_high_confidence']}")
    print(f"\nVERDICT: {s['verdict']}")
    explanations = {
        "CONTAMINATED": (
            "The model can recall this window from its pretraining data. "
            "Backtests on this window measure memory, not skill — use "
            "--anonymize and heavily discount any results on this period."
        ),
        "LIKELY CLEAN": (
            "The window appears to be outside what the model can recall: "
            "price guesses are no better than the naive carry-forward anchor "
            "and direction calls are near coin-flip."
        ),
        "INCONCLUSIVE": (
            "Mixed or insufficient signal — the probe could neither confirm "
            "nor rule out memorization. Prefer --anonymize and post-cutoff "
            "windows, and treat backtests on this window with caution."
        ),
    }
    print(explanations.get(s["verdict"], ""))

    # --- artifacts -------------------------------------------------------------
    out_path = (
        RESULTS_DIR / f"probe_{args.ticker}_{result['start']}_{result['end']}.json"
    )
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    _print_table(_usage_table(client.usage), "Token usage / cost (USD)")
    print(f"\nsaved: {out_path}")


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
    parser.add_argument("--version", action="version", version=f"tradinglab {__version__}")
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

    def _add_snapshot_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--ticker", required=True)
        p.add_argument(
            "--date", default=None,
            help="analysis date YYYY-MM-DD (default: latest available close)",
        )
        p.add_argument(
            "--no-live", action="store_true",
            help="skip current fundamentals + news (prices and indicators only)",
        )
        p.add_argument(
            "--anonymize", action="store_true",
            help="mask ticker/dates and rebase price levels (drops fundamentals + news)",
        )
        p.add_argument("--json", action="store_true", help="print one JSON document")

    p_sn = sub.add_parser(
        "snapshot",
        help="print the market snapshot the agents see for one ticker (no API key)",
    )
    _add_snapshot_args(p_sn)
    p_sn.set_defaults(func=cmd_snapshot)

    p_dk = sub.add_parser(
        "desk",
        help="one-shot agent decision on the latest close (needs API key)",
    )
    _add_snapshot_args(p_dk)
    p_dk.add_argument(
        "--debate-rounds", type=_positive_int, default=AgentConfig().debate_rounds,
        help="bull/bear exchanges before the verdict (>= 1)",
    )
    p_dk.add_argument(
        "--allow-degraded", action="store_true",
        help="run even when the snapshot is stale, could not be refreshed, or "
             "came back with no fundamentals and no headlines",
    )
    p_dk.set_defaults(func=cmd_desk)

    p_pr = sub.add_parser(
        "probe",
        help="memorization probe: can the LLM recall this window? (needs API key)",
    )
    p_pr.add_argument("--ticker", required=True)
    p_pr.add_argument("--start", required=True)
    p_pr.add_argument("--end", required=True)
    p_pr.add_argument(
        "--n", type=_positive_int, default=8,
        help="number of price-recall dates, evenly spaced in the window (>= 1)",
    )
    p_pr.add_argument(
        "--model", default=None,
        help="override the probed model (default: the agent deep model)",
    )
    p_pr.set_defaults(func=cmd_probe)

    p_rp = sub.add_parser("report", help="combined summary of saved results")
    p_rp.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: parse args, ensure the data directories, dispatch."""
    # Text that came from Yahoo or from an LLM can hold any character. On a
    # console or pipe that cannot encode one, print an escape instead of dying.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")

    args = build_parser().parse_args(argv)
    # Commands whose product is a file must be able to write it, and say so in
    # one line when they cannot, before doing any work or spending anything.
    # The price cache is best-effort everywhere (fetch_prices warns), and
    # "snapshot", "download" and "report" produce nothing else on disk.
    if args.command in _WRITES_RESULTS:
        _require_writable(RESULTS_DIR)
    if args.command in _USES_LLM_CACHE:
        _require_writable(LLM_CACHE_DIR)
    args.func(args)


if __name__ == "__main__":
    main()
