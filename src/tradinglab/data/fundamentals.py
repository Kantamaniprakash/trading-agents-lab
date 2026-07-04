"""Best-effort company fundamentals snapshot via yfinance.

LOOKAHEAD WARNING: yfinance fundamentals are CURRENT values, not point-in-time
history. Injecting them into a historical backtest leaks future information,
so the agent backtest only uses this module when running in live mode
(analysis date ~ today).
"""
from __future__ import annotations

import pandas as pd
import yfinance as yf

# snapshot key -> yfinance .info key
_INFO_KEYS = {
    "trailing_pe": "trailingPE",
    "forward_pe": "forwardPE",
    "market_cap": "marketCap",
    "profit_margins": "profitMargins",
    "return_on_equity": "returnOnEquity",
    "debt_to_equity": "debtToEquity",
    "revenue_growth": "revenueGrowth",
    "earnings_growth": "earningsGrowth",
}


def _quarterly_trend(stmt: object, row: str, n: int = 4) -> dict[str, float]:
    """Extract ``{quarter-end: value}`` (newest first) for one income-statement line."""
    if not isinstance(stmt, pd.DataFrame) or stmt.empty or row not in stmt.index:
        return {}
    trend: dict[str, float] = {}
    for col, val in stmt.loc[row].dropna().items():
        try:
            trend[pd.Timestamp(col).strftime("%Y-%m-%d")] = float(val)
        except (TypeError, ValueError):
            continue
    return dict(sorted(trend.items(), reverse=True)[:n])


def fetch_fundamentals_snapshot(ticker: str) -> dict:
    """Fetch a best-effort snapshot of current fundamentals for ``ticker``.

    Returns a dict with ``available: True``, the ratio/size fields listed in
    ``_INFO_KEYS`` (None when Yahoo omits them), and quarterly revenue /
    net-income trends when the income statement is available. On any failure
    (network, schema, parsing) returns ``{"available": False, "error": str(e)}``
    — this function must never raise.

    Live-mode only: values are current, not point-in-time (see module warning).
    """
    try:
        tkr = yf.Ticker(ticker)
        info = tkr.info
        if not isinstance(info, dict):
            info = {}
        snapshot: dict = {"available": True, "ticker": ticker}
        for name, key in _INFO_KEYS.items():
            snapshot[name] = info.get(key)
        try:
            stmt = tkr.quarterly_income_stmt
        except Exception:
            stmt = None
        snapshot["quarterly_revenue"] = _quarterly_trend(stmt, "Total Revenue")
        snapshot["quarterly_net_income"] = _quarterly_trend(stmt, "Net Income")
        return snapshot
    except Exception as e:
        return {"available": False, "error": str(e)}


def _fmt_num(value: object, pct: bool = False) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "n/a"
    return f"{value * 100:.1f}%" if pct else f"{value:,.2f}"


def _fmt_size(value: object) -> str:
    """Format a large dollar figure with T/B/M suffix."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "n/a"
    for suffix, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(value) >= div:
            return f"{value / div:.2f}{suffix}"
    return f"{value:,.0f}"


def format_fundamentals(snapshot: dict) -> str:
    """Render a fundamentals snapshot as a readable text block for agent prompts."""
    if not snapshot.get("available"):
        return f"Fundamentals unavailable ({snapshot.get('error', 'unknown error')})."
    lines = [
        f"Fundamentals snapshot for {snapshot.get('ticker', '?')} "
        "(CURRENT values, not point-in-time):",
        f"  Trailing P/E:      {_fmt_num(snapshot.get('trailing_pe'))}",
        f"  Forward P/E:       {_fmt_num(snapshot.get('forward_pe'))}",
        f"  Market cap:        {_fmt_size(snapshot.get('market_cap'))}",
        f"  Profit margin:     {_fmt_num(snapshot.get('profit_margins'), pct=True)}",
        f"  Return on equity:  {_fmt_num(snapshot.get('return_on_equity'), pct=True)}",
        f"  Debt/equity:       {_fmt_num(snapshot.get('debt_to_equity'))}",
        f"  Revenue growth:    {_fmt_num(snapshot.get('revenue_growth'), pct=True)}",
        f"  Earnings growth:   {_fmt_num(snapshot.get('earnings_growth'), pct=True)}",
    ]
    for label, key in (
        ("Quarterly revenue", "quarterly_revenue"),
        ("Quarterly net income", "quarterly_net_income"),
    ):
        trend = snapshot.get(key) or {}
        if trend:
            parts = ", ".join(f"{d}: {_fmt_size(v)}" for d, v in trend.items())
            lines.append(f"  {label} (newest first): {parts}")
    return "\n".join(lines)
