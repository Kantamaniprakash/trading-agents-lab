"""TradingAgents Lab — trader dashboard.

A point-and-click interface over the trading-agents-lab research framework:
backtest results, a live AI trading desk, a paper-trading portfolio, and the
full decision transcripts. Launch with ``run_dashboard.bat`` or::

    python -m streamlit run app.py

Nothing in this app ever places a real order with a brokerage.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import streamlit as st  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
from plotly.subplots import make_subplots  # noqa: E402

import dashboard_lib as lib  # noqa: E402
from tradinglab.config import DEFAULT_TICKERS, RESULTS_DIR, AgentConfig  # noqa: E402

st.set_page_config(page_title="TradingAgents Lab", page_icon="📈", layout="wide")

_HIGHLIGHT = "#6b3f2a"
_GREEN = "#0ca30c"
_RED = "#d03b3b"
_GREY = "#898781"


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def banner() -> None:
    st.warning(
        "**Research tool — not investment advice.** Prices come from Yahoo Finance "
        "and can be ~15 minutes delayed. Backtested performance does not predict "
        "future results. The Paper Portfolio uses pretend money; this app never "
        "places real orders with any brokerage.",
        icon="⚠️",
    )


def last_price_cached(ticker: str, max_age_s: float = 300.0) -> tuple[float | None, str]:
    """Session-cached wrapper around lib.fetch_last_price (5-minute TTL)."""
    cache = st.session_state.setdefault("_px_cache", {})
    hit = cache.get(ticker)
    if hit and (time.time() - hit[2]) < max_age_s:
        return hit[0], hit[1]
    price, source = lib.fetch_last_price(ticker)
    cache[ticker] = (price, source, time.time())
    return price, source


def equity_comparison_figure(curves: dict[str, pd.Series],
                             highlight: str | None = None) -> go.Figure:
    """Interactive cumulative-return comparison (paper Fig-7 style)."""
    fig = go.Figure()
    ordered = [n for n in curves if n != highlight]
    if highlight in curves:
        ordered.append(highlight)
    for name in ordered:
        curve = curves[name].dropna()
        if curve.empty:
            continue
        curve = curve / curve.iloc[0]
        is_hl = name == highlight
        fig.add_trace(go.Scatter(
            x=curve.index, y=curve.values, name=name, mode="lines",
            line=dict(width=4 if is_hl else 1.6,
                      color=_HIGHLIGHT if is_hl else None),
        ))
    fig.update_layout(
        template="plotly_white", height=480,
        yaxis_title="Cumulative return (growth of $1)",
        legend_title_text="Strategies", hovermode="x unified",
        margin=dict(l=40, r=20, t=30, b=40),
    )
    return fig


def candlestick_figure(df: pd.DataFrame, title: str = "",
                       decisions: pd.DataFrame | None = None) -> go.Figure:
    """Interactive candlestick + volume chart with optional BUY/SELL markers."""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.75, 0.25], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"], low=df["low"],
        close=df["close"], name="price",
        increasing_line_color=_GREEN, decreasing_line_color=_RED,
    ), row=1, col=1)
    vol_colors = [_GREEN if c >= o else _RED
                  for o, c in zip(df["open"], df["close"])]
    fig.add_trace(go.Bar(x=df.index, y=df["volume"], name="volume",
                         marker_color=vol_colors, opacity=0.5), row=2, col=1)

    if decisions is not None and len(decisions):
        dec = decisions.copy()
        dec["date"] = pd.to_datetime(dec["date"])
        dec = dec[(dec["date"] >= df.index.min()) & (dec["date"] <= df.index.max())]
        for action, symbol, color, dy in (("BUY", "triangle-up", _GREEN, 0.99),
                                          ("SELL", "triangle-down", _RED, 1.01)):
            rows = dec[dec["action"].str.upper() == action]
            if rows.empty:
                continue
            idx = df.index.get_indexer(rows["date"], method="nearest")
            ys = [df["low"].iloc[i] * dy if action == "BUY"
                  else df["high"].iloc[i] * dy for i in idx]
            fig.add_trace(go.Scatter(
                x=[df.index[i] for i in idx], y=ys, mode="markers",
                name=action.lower(),
                marker=dict(symbol=symbol, size=13, color=color),
            ), row=1, col=1)

    fig.update_layout(
        template="plotly_white", height=560, title=title,
        xaxis_rangeslider_visible=False, showlegend=True,
        margin=dict(l=40, r=20, t=50, b=30),
    )
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig


def fmt_metrics(df: pd.DataFrame) -> pd.DataFrame:
    pretty = df.copy()
    pct = ["cumulative_return", "annualized_return", "annualized_vol",
           "max_drawdown", "hit_rate"]
    for col in pct:
        if col in pretty.columns:
            pretty[col] = (pd.to_numeric(pretty[col], errors="coerce") * 100
                           ).map(lambda v: f"{v:,.1f}%")
    for col in ("sharpe", "sortino", "calmar"):
        if col in pretty.columns:
            pretty[col] = pd.to_numeric(pretty[col], errors="coerce").map(
                lambda v: f"{v:.2f}")
    return pretty


# ---------------------------------------------------------------------------
# page: results
# ---------------------------------------------------------------------------

def page_results() -> None:
    st.title("📊 Backtest results")
    st.caption(
        "Everything below is measured **after** trading costs (10 bps per trade) "
        "and compares against simply buying and holding."
    )

    baselines = lib.load_metrics_csv(RESULTS_DIR / "baselines_metrics.csv")
    ml = lib.load_metrics_csv(RESULTS_DIR / "ml_metrics.csv")

    if baselines is not None:
        port = baselines[baselines.index.str.startswith("PORTFOLIO/")].copy()
        port.index = [i.split("/")[1] for i in port.index]
        port.index = [lib.STRATEGY_LABELS.get(i, i) for i in port.index]
        st.subheader("Whole-portfolio Sharpe ratio, 2015 → mid-2026")
        st.caption(
            "Sharpe ratio = reward per unit of risk. Above 1 is good; below 0 "
            "means you lost money versus a safe 4% deposit."
        )
        srt = port.sort_values("sharpe")
        colors = [_GREEN if s >= 0 else _RED for s in srt["sharpe"]]
        fig = go.Figure(go.Bar(x=srt["sharpe"], y=srt.index, orientation="h",
                               marker_color=colors,
                               text=[f"{s:.2f}" for s in srt["sharpe"]],
                               textposition="outside"))
        fig.update_layout(template="plotly_white", height=320,
                          xaxis_title="Sharpe ratio",
                          margin=dict(l=40, r=40, t=10, b=40))
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Compare strategies on one stock")
    tickers = lib.list_cached_tickers() or list(DEFAULT_TICKERS)
    col1, col2 = st.columns([1, 2])
    with col1:
        ticker = st.selectbox("Stock", tickers,
                              index=tickers.index("AAPL") if "AAPL" in tickers else 0)
    df = lib.load_cached_prices(ticker)
    if df is None or df.empty:
        st.info("No cached prices for this stock yet — run the AI Trading Desk "
                "once, or `python -m tradinglab.cli download` from a terminal.")
        return

    decisions, source = lib.load_agent_decisions(ticker)
    with col2:
        window = st.radio(
            "Window", ["Full history (2015 →)", "AI agent run window"],
            horizontal=True,
            help="The agent window is only available for stocks with a recorded "
                 "AI backtest (decision transcripts).",
            disabled=decisions is None,
        )

    if decisions is not None and window == "AI agent run window":
        start = pd.Timestamp(decisions["date"].min())
        end = lib.default_agent_end(df, decisions)
    else:
        start, end = df.index.min(), df.index.max()
        decisions = None if window.startswith("Full") else decisions

    results = lib.baseline_results(df, start, end, cost_bps=10.0, rf=0.04)
    curves = {name: r.equity for name, r in results.items()}
    highlight = None
    if decisions is not None:
        agent_res = lib.agent_window_backtest(df, decisions, end=end,
                                              cost_bps=10.0, rf=0.04)
        if agent_res is not None:
            curves[lib.AGENT_LABEL] = agent_res.equity
            results[lib.AGENT_LABEL] = agent_res
            highlight = lib.AGENT_LABEL

    st.plotly_chart(equity_comparison_figure(curves, highlight=highlight),
                    use_container_width=True)
    if decisions is not None and highlight:
        st.caption(f"AI agent decisions loaded from: {source}.")

    st.subheader("The numbers")
    table = lib.window_metrics(results, rf=0.04)
    st.dataframe(fmt_metrics(table), use_container_width=True)

    if ml is not None:
        with st.expander("Machine-learning model results (LightGBM, 2021 →)"):
            st.caption(
                "A classical ML model predicting next-day direction. Long-only "
                "it makes money but still trails buy & hold once costs are paid — "
                "the honest benchmark any 'AI trading' claim must beat."
            )
            st.dataframe(fmt_metrics(ml), use_container_width=True)


# ---------------------------------------------------------------------------
# page: AI trading desk
# ---------------------------------------------------------------------------

def _resolve_api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY") or st.session_state.get("api_key")
    with st.sidebar:
        if not key:
            st.info("Paste your Anthropic API key to enable the AI desk. "
                    "It stays on this computer only.")
        entered = st.text_input("Anthropic API key", type="password",
                                value=st.session_state.get("api_key", ""),
                                help="Get one at console.anthropic.com. "
                                     "Each analysis costs roughly $0.20–0.50.")
        if entered:
            st.session_state["api_key"] = entered
            key = entered
    if key:
        os.environ["ANTHROPIC_API_KEY"] = key
    return key


def _run_analysis(ticker: str, debate_rounds: int) -> None:
    from tradinglab.agents.graph import TradingAgentsPipeline, snapshot_from_history
    from tradinglab.agents.llm import LLMClient
    from tradinglab.data.fundamentals import fetch_fundamentals_snapshot, format_fundamentals
    from tradinglab.data.news import fetch_current_news

    progress = st.status(f"Running the AI trading desk on {ticker}…", expanded=True)
    with progress:
        st.write("1/4 Downloading the latest prices…")
        df = lib.fetch_latest_prices(ticker)
        latest = df.index[-1]
        st.write(f"   data through **{latest:%Y-%m-%d}** ({len(df):,} days)")

        st.write("2/4 Fetching fundamentals and news…")
        fundamentals = format_fundamentals(fetch_fundamentals_snapshot(ticker))
        news_items = fetch_current_news(ticker)
        news = [n.get("title", "") for n in news_items if n.get("title")] or None

        st.write("3/4 Building the market snapshot…")
        snapshot = snapshot_from_history(
            df, latest.strftime("%Y-%m-%d"), ticker,
            fundamentals=fundamentals, news=news,
        )

        st.write("4/4 Analysts → debate → trader → risk team → fund manager…")
        cfg = AgentConfig(debate_rounds=debate_rounds)
        client = LLMClient(cfg)
        log = TradingAgentsPipeline(client, cfg).decide(snapshot)
        progress.update(label="Analysis complete", state="complete", expanded=False)

    lib.append_live_decision(ticker, log.final_action, log.final_size,
                             getattr(log.risk, "rationale", "") or
                             getattr(log.decision, "rationale", ""))
    st.session_state["last_run"] = {
        "ticker": ticker, "log": log, "usage": client.usage,
        "df": df.tail(140), "when": latest.strftime("%Y-%m-%d"),
    }


def _decision_card(ticker: str, log) -> None:
    action = str(log.final_action).upper()
    size = float(log.final_size)
    color, icon = {"BUY": (_GREEN, "🟢"), "SELL": (_RED, "🔴")}.get(action, (_GREY, "⚪"))
    st.markdown(
        f"""<div style="border:2px solid {color}; border-radius:12px; padding:18px 24px;
        background:{color}11;">
        <span style="font-size:2.2em; font-weight:700; color:{color};">{icon} {action}</span>
        <span style="font-size:1.3em; margin-left:14px;">{ticker}</span>
        <span style="font-size:1.1em; margin-left:14px; color:#555;">
        suggested exposure: <b>{size:.0%}</b> of capital</span>
        </div>""",
        unsafe_allow_html=True,
    )
    verdict = getattr(log.debate, "verdict", "") if log.debate else ""
    rationale = (getattr(log.risk, "rationale", "") or
                 getattr(log.decision, "rationale", ""))
    if verdict:
        st.caption(f"Research team verdict: **{verdict}**")
    if rationale:
        st.markdown(f"> {rationale}")


def page_desk() -> None:
    st.title("🤖 AI Trading Desk — live analysis")
    st.caption(
        "One click runs the full agent pipeline from the research paper on the "
        "latest market data: four analysts report, a bull and a bear debate, a "
        "trader decides, a risk team challenges it, and a fund manager signs off."
    )
    key = _resolve_api_key()

    universe = sorted(set(lib.list_cached_tickers()) | set(DEFAULT_TICKERS))
    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        picked = st.selectbox("Choose a stock", universe,
                              index=universe.index("AAPL") if "AAPL" in universe else 0)
    with col2:
        custom = st.text_input("…or type any ticker symbol", "",
                               placeholder="e.g. DIS, KO, SPY")
    with col3:
        rounds = st.selectbox("Debate depth", [1, 2, 3], index=1,
                              help="More debate rounds = deeper analysis, "
                                   "slightly higher cost.")
    ticker = (custom.strip().upper() or picked).upper()

    run = st.button(f"▶  Run AI analysis on {ticker}", type="primary",
                    disabled=not key,
                    help=None if key else "Enter your API key in the sidebar first.")
    if run:
        try:
            _run_analysis(ticker, rounds)
        except Exception as exc:  # surfaced in plain language, never a traceback
            st.error(
                f"The analysis could not be completed: {exc}\n\n"
                "Check the ticker symbol, your internet connection, and your "
                "API key, then try again."
            )

    last = st.session_state.get("last_run")
    if not last:
        st.info("No analysis yet this session. Pick a stock and press Run. "
                "A full run takes about a minute and costs roughly $0.20–0.50 "
                "in API usage.")
        return

    log, ticker = last["log"], last["ticker"]
    st.divider()
    st.subheader(f"Latest recommendation — {ticker} (data through {last['when']})")
    _decision_card(ticker, log)

    st.plotly_chart(
        candlestick_figure(last["df"], title=f"{ticker} — last 6 months"),
        use_container_width=True,
    )

    st.subheader("Why? — the full reasoning")
    for report in (log.reports or []):
        with st.expander(f"📋 {report.role}"):
            st.markdown(report.summary)
    if log.debate:
        with st.expander("⚖️ Bull vs Bear research debate"):
            for turn in log.debate.transcript:
                st.markdown(f"**{turn.speaker}:** {turn.text}")
            st.markdown(f"**Verdict:** {log.debate.verdict} — "
                        f"{log.debate.rationale}")
    if log.decision:
        with st.expander("💼 Trader decision"):
            st.markdown(f"**{log.decision.action}** (size {log.decision.size:.2f}) — "
                        f"{log.decision.rationale}")
    if log.risk:
        with st.expander("🛡️ Risk team & fund manager"):
            st.markdown(
                f"Approved: **{log.risk.approved}** → final "
                f"**{log.risk.final_action}** (size {log.risk.final_size:.2f})\n\n"
                f"{log.risk.rationale}"
            )

    usage_table, total = lib.usage_cost_table(last["usage"])
    with st.expander(f"🧾 What this analysis cost: ${total:.2f}"):
        st.dataframe(usage_table, use_container_width=True)
        st.caption("Cache hits are free — rerunning the same analysis on the "
                   "same data costs nothing.")

    st.success(
        "To act on this with pretend money, open the **Paper Portfolio** page "
        "and press *Apply latest AI decision*."
    )

    history = lib.load_live_decisions()
    if history is not None:
        with st.expander("📒 All live decisions this desk has made"):
            st.dataframe(history.iloc[::-1], use_container_width=True)


# ---------------------------------------------------------------------------
# page: paper portfolio
# ---------------------------------------------------------------------------

def page_portfolio() -> None:
    st.title("💼 Paper portfolio — practice with pretend money")
    st.caption(
        "You start with $100,000 of practice money. Trades use live prices but "
        "no real money ever moves. Prove the strategy works here before risking "
        "a single real dollar."
    )
    portfolio = lib.load_portfolio()

    rows, positions_value = [], 0.0
    for pos in portfolio.get("positions", []):
        tk = pos["ticker"]
        price, src = last_price_cached(tk)
        shares = float(pos["shares"])
        entry = float(pos["entry_price"])
        if price:
            value = shares * price
            pl = value - shares * entry
            pl_pct = price / entry - 1.0
        else:
            value, pl, pl_pct = shares * entry, 0.0, 0.0
        positions_value += value
        rows.append({
            "stock": tk, "shares": round(shares, 4),
            "bought at": f"${entry:,.2f}", "now": f"${price:,.2f}" if price else "?",
            "value": f"${value:,.2f}",
            "profit/loss": f"${pl:+,.2f} ({pl_pct:+.1%})",
            "price source": src,
        })

    cash = float(portfolio.get("cash", 0.0))
    total = cash + positions_value
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total account", f"${total:,.2f}",
              f"{total / lib.STARTING_CASH - 1:+.2%} all-time")
    c2.metric("Cash", f"${cash:,.2f}")
    c3.metric("Invested", f"${positions_value:,.2f}")
    c4.metric("Open positions", str(len(rows)))

    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.info("No open positions yet.")

    st.divider()
    left, mid, right = st.columns(3)

    with left:
        st.subheader("Apply the AI's advice")
        latest = lib.latest_live_decision()
        if latest is None:
            st.caption("Run the AI Trading Desk first — its latest decision "
                       "will show up here.")
        else:
            st.markdown(
                f"Latest AI decision: **{latest['action']} {latest['ticker']}** "
                f"(size {latest['size']:.2f}, {latest['timestamp'][:16]})"
            )
            st.caption(
                "BUY invests (AI size × 10%) of your account in the stock; "
                "SELL closes your position. The paper portfolio never shorts — "
                "short selling with practice money teaches expensive habits."
            )
            if st.button("Apply latest AI decision", type="primary"):
                price, _ = last_price_cached(latest["ticker"], max_age_s=60)
                ok, msg = lib.apply_ai_decision(
                    portfolio, latest["ticker"], latest["action"],
                    latest["size"], price, total,
                )
                (st.success if ok else st.error)(msg)
                if ok:
                    lib.save_portfolio(portfolio)
                    st.rerun()

    with mid:
        st.subheader("Buy manually")
        with st.form("buy_form"):
            tk = st.text_input("Ticker", "AAPL").strip().upper()
            dollars = st.number_input("Amount to invest ($)", min_value=0.0,
                                      value=1000.0, step=100.0)
            if st.form_submit_button("Buy"):
                price, src = last_price_cached(tk, max_age_s=60)
                ok, msg = lib.portfolio_buy(portfolio, tk, dollars, price,
                                            note=f"manual buy ({src})")
                (st.success if ok else st.error)(msg)
                if ok:
                    lib.save_portfolio(portfolio)
                    st.rerun()

    with right:
        st.subheader("Sell / close")
        open_tickers = [p["ticker"] for p in portfolio.get("positions", [])]
        if not open_tickers:
            st.caption("Nothing to sell.")
        else:
            with st.form("sell_form"):
                tk = st.selectbox("Position", open_tickers)
                if st.form_submit_button("Sell everything in this stock"):
                    price, src = last_price_cached(tk, max_age_s=60)
                    ok, msg = lib.portfolio_sell_all(portfolio, tk, price,
                                                     note=f"manual sell ({src})")
                    (st.success if ok else st.error)(msg)
                    if ok:
                        lib.save_portfolio(portfolio)
                        st.rerun()

    log = portfolio.get("log", [])
    if log:
        st.divider()
        st.subheader("Trade history")
        st.dataframe(pd.DataFrame(log).iloc[::-1], use_container_width=True)


# ---------------------------------------------------------------------------
# page: transcripts
# ---------------------------------------------------------------------------

def page_transcripts() -> None:
    st.title("📜 Decision transcripts")
    st.caption(
        "Every decision the agents ever made, with the full debate — the "
        "'explainability' the research paper promises. Newest first."
    )
    items = lib.list_transcripts()
    if not items:
        st.info("No transcripts yet. They appear after agent backtests "
                "(`cli agents`) or can be browsed from the committed AAPL run.")
        return
    labels = [f"{d['date']} — {d['ticker']} — {d['action']}"
              + (f" ({d['verdict']})" if d["verdict"] else "")
              for d in items]
    chosen = st.selectbox("Pick a decision day", labels)
    item = items[labels.index(chosen)]
    st.markdown(Path(item["path"]).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    with st.sidebar:
        st.markdown("## 📈 TradingAgents Lab")
        page = st.radio("Go to", ["Results", "AI Trading Desk",
                                  "Paper Portfolio", "Transcripts"])
        st.divider()
    banner()
    if page == "Results":
        page_results()
    elif page == "AI Trading Desk":
        page_desk()
    elif page == "Paper Portfolio":
        page_portfolio()
    else:
        page_transcripts()
    st.divider()
    st.caption(
        "trading-agents-lab · research implementation of arXiv:2412.20138 · "
        "all performance shown net of 10 bps costs · this is not financial advice"
    )


main()
