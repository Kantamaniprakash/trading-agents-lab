"""Snapshot construction, pipeline orchestration and the agent backtest.

`snapshot_from_history` turns point-in-time price history into the text
snapshot the agents see; `TradingAgentsPipeline` chains the agent teams for
one decision day; `run_agent_backtest` walks the pipeline over a date range
and scores the resulting signal with the standard backtest engine.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from tradinglab.agents.llm import LLMClient
from tradinglab.agents.roles import (
    run_analysts,
    run_research_debate,
    run_risk_debate,
    run_trader,
)
from tradinglab.agents.state import AgentDayLog, MarketSnapshot
from tradinglab.backtest.engine import BacktestResult, backtest_signals
from tradinglab.config import AgentConfig
from tradinglab.features.indicators import compute_indicator_frame

# compute_indicator_frame's longest window is sma_200 and the returns summary
# needs 252 closes for 52-week stats, so the snapshot window is floored at 260
# rows regardless of `lookback` to keep the long indicators defined.
_MIN_WINDOW_ROWS = 260


def _num(value: object, digits: int = 2) -> str:
    """Format a number, rendering NaN/None as 'n/a'."""
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):.{digits}f}"


def _pct(value: object, signed: bool = True) -> str:
    """Format a decimal fraction as a percentage, NaN-safe."""
    if value is None or pd.isna(value) or not math.isfinite(float(value)):
        return "n/a"
    sign = "+" if signed else ""
    return f"{float(value) * 100:{sign}.2f}%"


def _trailing_return(close: pd.Series, n: int) -> float:
    """Simple return over the last `n` trading days, NaN if not enough history."""
    if len(close) <= n:
        return float("nan")
    return float(close.iloc[-1] / close.iloc[-1 - n] - 1.0)


def _rebase_for_anonymize(window: pd.DataFrame, display_rows: int = 10) -> pd.DataFrame:
    """Rebase a history slice to scale-free levels for anonymized snapshots.

    Masking tickers and dates is not enough: raw price/volume levels are a
    fingerprint that lets a model re-identify a well-known stock and recall its
    memorized path. This scales open/high/low/close by ``k = 100 / close`` at
    the first row the price table will display, so the first displayed close is
    exactly 100.0 and every price is an index level, and replaces volume with
    relative volume (% of the slice's mean volume; 100 = average day).

    Everything downstream (price table, indicator report, returns summary) is
    computed from the rebased frame so the numbers stay internally consistent:
    scale-free indicators (RSI, ADX, %B, stochastics, MFI, returns, vol) are
    unchanged by construction, while price-level ones (SMA, MACD, Bollinger
    bands, ATR) scale by the same k as the displayed prices.
    """
    anchor = float(window["close"].iloc[-min(display_rows, len(window))])
    if not math.isfinite(anchor) or anchor <= 0:
        raise ValueError(
            "Cannot rebase for anonymization: close at the start of the "
            f"displayed window must be positive and finite, got {anchor!r}")
    k = 100.0 / anchor
    rebased = window.copy()
    for col in ("open", "high", "low", "close"):
        rebased[col] = window[col] * k
    mean_vol = float(window["volume"].mean())
    if math.isfinite(mean_vol) and mean_vol > 0:
        rebased["volume"] = window["volume"] / mean_vol * 100.0
    else:  # degenerate slice (all-zero/NaN volume): nothing to normalize
        rebased["volume"] = 0.0
    return rebased


def snapshot_from_history(df: pd.DataFrame, date: str | pd.Timestamp, ticker: str,
                          lookback: int = 60, anonymize: bool = False,
                          fundamentals: str | None = None,
                          news: list[str] | None = None) -> MarketSnapshot:
    """Build a MarketSnapshot from price history up to and including `date`.

    Slices `df.loc[:date]` FIRST and computes everything from that slice only
    (no lookahead by construction). When `anonymize=True` the whole working
    slice is rebased BEFORE anything is rendered (`_rebase_for_anonymize`):
    prices become index levels (first displayed close = 100.0), volume becomes
    % of the slice's average volume, and the price table uses relative row
    labels ("T-9".."T-0") — so no real dates, ticker names, price levels or
    volume magnitudes appear in any rendered block.
    """
    ts = pd.Timestamp(date)
    history = df.loc[:ts]
    if history.empty:
        raise ValueError(f"No price history for {ticker} on or before {ts.date()}")
    assert history.index.max() <= ts, "history slice must not contain future rows"

    window = history.tail(max(int(lookback), _MIN_WINDOW_ROWS))
    if anonymize:
        # Rebase the ENTIRE slice first so the table, indicators and returns
        # summary below are all computed from the same masked frame.
        window = _rebase_for_anonymize(window)

    # --- price table: last 10 rows, rounded ---
    tail = window[["open", "high", "low", "close", "volume"]].tail(10).copy()
    tail[["open", "high", "low", "close"]] = tail[["open", "high", "low", "close"]].round(2)
    if anonymize:
        tail["volume"] = tail["volume"].round(1)
        tail.index = [f"T-{i}" for i in range(len(tail) - 1, -1, -1)]
        price_table = (
            "[prices indexed (start = 100); volume in % of average daily "
            "volume (100 = average day)]\n" + tail.to_string())
    else:
        tail["volume"] = tail["volume"].round(0).astype("int64")
        tail.index = [d.strftime("%Y-%m-%d") for d in tail.index]
        price_table = tail.to_string()

    # --- indicator report: latest row of the standard indicator frame ---
    last = compute_indicator_frame(window).iloc[-1]
    close = float(window["close"].iloc[-1])

    if pd.isna(last["macd"]) or pd.isna(last["macd_signal"]):
        macd_rel = "n/a"
    else:
        macd_rel = "above signal" if last["macd"] > last["macd_signal"] else "below signal"

    def _sma_gap(sma_value: float) -> str:
        if pd.isna(sma_value) or sma_value == 0:
            return "n/a"
        return _pct(close / float(sma_value) - 1.0)

    atr_part = ""
    if not pd.isna(last["atr_14"]) and close > 0:
        atr_part = f" ({float(last['atr_14']) / close * 100:.2f}% of close)"

    indicator_report = "\n".join([
        "KEY INDICATORS (as of latest close)",
        f"RSI(14): {_num(last['rsi_14'])}",
        f"MACD: {_num(last['macd'], 4)} vs signal {_num(last['macd_signal'], 4)} "
        f"(hist {_num(last['macd_hist'], 4)}, {macd_rel})",
        f"Bollinger %B: {_num(last['bb_pctb'])}",
        f"ATR(14): {_num(last['atr_14'])}{atr_part}",
        f"ADX(14): {_num(last['adx_14'])}",
        f"CCI(20): {_num(last['cci_20'])}",
        f"Stochastic: K={_num(last['stoch_k'])} D={_num(last['stoch_d'])} "
        f"(KDJ J={_num(last['kdj_j'])})",
        f"MFI(14): {_num(last['mfi_14'])}",
        f"Close vs SMA10: {_sma_gap(last['sma_10'])} | SMA50: {_sma_gap(last['sma_50'])} "
        f"| SMA200: {_sma_gap(last['sma_200'])}",
    ])

    # --- returns summary ---
    closes = window["close"]
    r1 = _trailing_return(closes, 1)
    r5 = _trailing_return(closes, 5)
    r21 = _trailing_return(closes, 21)
    r63 = _trailing_return(closes, 63)
    daily = closes.pct_change()
    vol21 = float(daily.tail(21).std() * math.sqrt(252)) if len(daily) > 2 else float("nan")
    year = closes.tail(252)
    hi, lo = float(year.max()), float(year.min())
    dist_hi = close / hi - 1.0 if hi > 0 else float("nan")
    dist_lo = close / lo - 1.0 if lo > 0 else float("nan")

    returns_summary = "\n".join([
        "RETURNS & RISK",
        f"Returns: 1d {_pct(r1)} | 5d {_pct(r5)} | 21d {_pct(r21)} | 63d {_pct(r63)}",
        f"21d annualized volatility: {_pct(vol21, signed=False)}",
        f"Distance from 52w high: {_pct(dist_hi)} | from 52w low: {_pct(dist_lo)}",
    ])

    return MarketSnapshot(
        ticker=ticker,
        date=ts.strftime("%Y-%m-%d"),
        price_table=price_table,
        indicator_report=indicator_report,
        returns_summary=returns_summary,
        fundamentals=fundamentals,
        news=news,
        anonymize=anonymize,
    )


class TradingAgentsPipeline:
    """Chains analysts -> research debate -> trader -> risk team for one day."""

    def __init__(self, client: LLMClient, cfg: AgentConfig):
        self.client = client
        self.cfg = cfg

    def decide(self, snapshot: MarketSnapshot) -> AgentDayLog:
        """Run the full agent workflow on one snapshot and return the day log."""
        reports = run_analysts(self.client, snapshot)
        debate = run_research_debate(self.client, snapshot, reports,
                                     self.cfg.debate_rounds)
        decision = run_trader(self.client, snapshot, reports, debate)
        risk = run_risk_debate(self.client, snapshot, decision, self.cfg.risk_rounds)
        return AgentDayLog(
            ticker=snapshot.ticker,
            date=snapshot.date,
            reports=reports,
            debate=debate,
            decision=decision,
            risk=risk,
            final_action=risk.final_action,
            final_size=risk.final_size,
        )


def run_agent_backtest(df: pd.DataFrame, ticker: str, start: str, end: str,
                       client: LLMClient, cfg: AgentConfig,
                       out_dir: Path | str | None = None, every: int = 1,
                       anonymize: bool = False, cost_bps: float = 10.0,
                       rf: float = 0.0, long_only: bool = False,
                       max_days: int | None = None,
                       fundamentals: str | None = None,
                       news: list[str] | None = None,
                       ) -> tuple[BacktestResult, list[AgentDayLog]]:
    """Backtest the agent pipeline on `df` over [start, end].

    Runs a decision on every `every`-th trading date; `max_days` caps the
    number of decision days WITHOUT shrinking the evaluation window, so the
    last decision's position is carried forward and accrues returns through
    `end`. Actions map to signal values: BUY -> +final_size,
    SELL -> -final_size (0 when long_only), HOLD -> NaN so the prior position
    is carried forward. The sparse decision series is reindexed to the full
    trading index over [start, end], ffilled and zero-filled before being
    handed to the backtest engine. `fundamentals`/`news` (live mode: CURRENT
    data, not point-in-time) are passed through to every snapshot so the
    fundamentals/news/sentiment analysts run. Day logs are written to
    `{out_dir}/{ticker}_{date}.md` when `out_dir` is given.
    """
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    period = df.loc[(df.index >= start_ts) & (df.index <= end_ts)]
    if period.empty:
        raise ValueError(f"No trading dates for {ticker} in [{start}, {end}]")

    decision_dates = period.index[::max(int(every), 1)]
    if max_days is not None:
        decision_dates = decision_dates[:max(int(max_days), 1)]
    pipeline = TradingAgentsPipeline(client, cfg)

    out_path: Path | None = None
    if out_dir is not None:
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)

    logs: list[AgentDayLog] = []
    raw = pd.Series(np.nan, index=period.index, dtype=float)

    for ts in decision_dates:
        snapshot = snapshot_from_history(df, ts, ticker,
                                         lookback=cfg.lookback_days,
                                         anonymize=anonymize,
                                         fundamentals=fundamentals,
                                         news=news)
        log = pipeline.decide(snapshot)
        logs.append(log)

        if log.final_action == "BUY":
            value = float(log.final_size)
        elif log.final_action == "SELL":
            value = 0.0 if long_only else -float(log.final_size)
        else:  # HOLD: NaN so ffill carries the previous position
            value = np.nan
        raw.loc[ts] = value

        if out_path is not None:
            (out_path / f"{ticker}_{log.date}.md").write_text(
                log.to_markdown(), encoding="utf-8")
        print(f"[{ticker}] {log.date}  action={log.final_action:<4} "
              f"size={log.final_size:.2f}  verdict={log.debate.verdict}")

    # Explicit and equivalent to the engine's own normalization: dense signal
    # over the full evaluation index, prior position carried through HOLD days.
    signal = raw.reindex(period.index).ffill().fillna(0.0)
    result = backtest_signals(period, signal, cost_bps=cost_bps, rf=rf,
                              allow_short=not long_only,
                              name=f"agents_{ticker}")
    return result, logs
