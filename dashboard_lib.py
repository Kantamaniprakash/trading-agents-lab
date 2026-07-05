"""Data and portfolio helpers behind the Streamlit dashboard (``app.py``).

Everything in this module is plain pandas / JSON logic with no Streamlit
dependency, so it can be imported and exercised from scripts or tests.
Per the project contracts the dashboard and the ``tradinglab`` CLI never
import each other; both build only on ``tradinglab.*`` modules.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from tradinglab.backtest.baselines import STRATEGIES
from tradinglab.backtest.engine import BacktestResult, backtest_signals
from tradinglab.backtest.metrics import metrics_table
from tradinglab.config import (
    DATA_CACHE_DIR,
    DEFAULT_TICKERS,
    RESULTS_DIR,
    DataConfig,
)

TRANSCRIPTS_DIR = RESULTS_DIR / "transcripts"
PORTFOLIO_PATH = RESULTS_DIR / "portfolio.json"
LIVE_DECISIONS_PATH = RESULTS_DIR / "live_decisions.csv"
STARTING_CASH = 100_000.0

# USD per 1M input/output tokens. Mirrors the CLI's price map by contract
# (the dashboard and the CLI must not import each other).
PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
DEFAULT_PRICE_PER_MTOK: tuple[float, float] = (5.0, 25.0)

STRATEGY_LABELS = {
    "buy_hold": "Buy & hold",
    "macd": "MACD",
    "kdj_rsi": "KDJ + RSI",
    "zmr": "Mean reversion (ZMR)",
    "sma_cross": "SMA cross",
    "ml": "LightGBM ML",
    "agents": "AI agent desk",
}

AGENT_LABEL = "AI agent desk"


# ---------------------------------------------------------------------------
# price data
# ---------------------------------------------------------------------------

def list_cached_tickers() -> list[str]:
    """Tickers that have at least one parquet file in the price cache."""
    if not DATA_CACHE_DIR.exists():
        return []
    tickers = {p.stem.split("_")[0] for p in DATA_CACHE_DIR.glob("*.parquet")}
    return sorted(t for t in tickers if t)


def cached_prices_mtime(ticker: str) -> float | None:
    """Modification time of the newest cached parquet for ``ticker`` (cache key)."""
    if not DATA_CACHE_DIR.exists():
        return None
    files = list(DATA_CACHE_DIR.glob(f"{ticker}_*.parquet"))
    if not files:
        return None
    return max(p.stat().st_mtime for p in files)


def load_cached_prices(ticker: str) -> pd.DataFrame | None:
    """Read the newest cached parquet for ``ticker`` directly (fast, offline).

    Prefers open-ended ("latest") caches, then falls back to any dated cache.
    Returns the canonical OHLCV frame or None when nothing is cached.
    """
    if not DATA_CACHE_DIR.exists():
        return None
    files = list(DATA_CACHE_DIR.glob(f"{ticker}_*.parquet"))
    if not files:
        return None
    files.sort(key=lambda p: ("latest" in p.stem, p.stat().st_mtime), reverse=True)
    for path in files:
        try:
            df = pd.read_parquet(path)
        except Exception:
            continue
        if df.empty or "close" not in df.columns:
            continue
        df.index = pd.DatetimeIndex(df.index)
        return df.sort_index()
    return None


def fetch_latest_prices(ticker: str, max_age_hours: float = 24.0) -> pd.DataFrame:
    """Fetch full daily history up to today via ``tradinglab.data.prices``.

    Uses the standard open-ended cache file and forces a re-download when
    that cache is older than ``max_age_hours`` (default: one day), so the
    AI desk always analyses yesterday's close or newer. Raises ``ValueError``
    for unknown symbols (propagated from ``fetch_prices``).
    """
    from tradinglab.data.prices import fetch_prices

    start = DataConfig().start
    cache_file = DATA_CACHE_DIR / f"{ticker}_{start}_latest.parquet"
    force = (
        cache_file.exists()
        and (time.time() - cache_file.stat().st_mtime) > max_age_hours * 3600.0
    )
    prices = fetch_prices(ticker, start, None, cache_dir=DATA_CACHE_DIR, force=force)
    return prices[ticker]


def fetch_last_price(ticker: str) -> tuple[float | None, str]:
    """Best-effort latest price: live Yahoo quote, else last cached close.

    Returns ``(price, source_description)``; ``(None, reason)`` when neither
    source works. Never raises.
    """
    try:
        import yfinance as yf

        fast = yf.Ticker(ticker).fast_info
        for key in ("last_price", "lastPrice", "regularMarketPrice"):
            value = None
            try:
                value = getattr(fast, key)
            except Exception:
                try:
                    value = fast[key]
                except Exception:
                    value = None
            if isinstance(value, (int, float)) and value > 0:
                return float(value), "live quote (may be ~15 min delayed)"
    except Exception:
        pass
    df = load_cached_prices(ticker)
    if df is not None and len(df):
        date = df.index[-1].strftime("%Y-%m-%d")
        return float(df["close"].iloc[-1]), f"last cached close ({date})"
    return None, "no price available (no live quote and no cached data)"


# ---------------------------------------------------------------------------
# saved metrics CSVs
# ---------------------------------------------------------------------------

def load_metrics_csv(path: Path | str) -> pd.DataFrame | None:
    """Load a metrics CSV written by the CLI (index = strategy names)."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, index_col=0)
    except Exception:
        return None
    return df if len(df) else None


def agent_metric_files() -> list[Path]:
    """All saved ``agents_{ticker}_metrics.csv`` files, sorted by name."""
    if not RESULTS_DIR.exists():
        return []
    return sorted(RESULTS_DIR.glob("agents_*_metrics.csv"))


def ticker_from_metrics_path(path: Path) -> str:
    """``results/agents_AAPL_metrics.csv`` -> ``AAPL``."""
    stem = path.stem
    if stem.startswith("agents_") and stem.endswith("_metrics"):
        return stem[len("agents_"):-len("_metrics")]
    return stem


# ---------------------------------------------------------------------------
# agent decisions: CSV (contract 2) with transcript fallback (contract 3)
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(
    r"#\s*Trading agents transcript\s*[—–-]+\s*(.+?)\s*[—–-]+\s*(\d{4}-\d{2}-\d{2})"
)
_DECISION_RE = re.compile(
    r"\*\*Final decision:\s*(BUY|SELL|HOLD)\*\*"
    r"(?:\s*\(\s*size\s*([0-9]*\.?[0-9]+)\s*\))?",
    re.IGNORECASE,
)
_VERDICT_RE = re.compile(
    r"Facilitator verdict[:*\s]*(BULLISH|BEARISH|NEUTRAL)", re.IGNORECASE
)
_FILENAME_RE = re.compile(r"(?P<ticker>.+)_(?P<date>\d{4}-\d{2}-\d{2})\.md$", re.IGNORECASE)


def parse_transcript(path: Path | str) -> dict | None:
    """Parse one transcript markdown file into a decision record.

    Returns ``{ticker, date, action, size, verdict, path}`` or None when the
    file cannot be attributed to a ticker + date. Tolerates em-dash / hyphen
    separators in the header and HOLD decisions with or without a size.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    ticker: str | None = None
    date: str | None = None
    header = _HEADER_RE.search(text)
    if header:
        ticker, date = header.group(1).strip(), header.group(2)
    if not ticker or not date:
        from_name = _FILENAME_RE.match(path.name)
        if from_name:
            ticker = ticker or from_name["ticker"]
            date = date or from_name["date"]
    if not ticker or not date:
        return None

    decision = _DECISION_RE.search(text)
    action = decision.group(1).upper() if decision else "HOLD"
    size = float(decision.group(2)) if decision and decision.group(2) else 0.0
    verdict_match = _VERDICT_RE.search(text)
    verdict = verdict_match.group(1).upper() if verdict_match else ""
    return {
        "ticker": ticker,
        "date": date,
        "action": action,
        "size": size,
        "verdict": verdict,
        "path": str(path),
    }


def _positions_after(actions: pd.Series, sizes: pd.Series) -> list[float]:
    """Replay decision semantics: BUY -> +size, SELL -> -size, HOLD -> carry.

    A size printed on a HOLD is advisory and ignored (matches the backtest).
    """
    position = 0.0
    out: list[float] = []
    for action, size in zip(actions, sizes):
        if action == "BUY":
            position = abs(float(size))
        elif action == "SELL":
            position = -abs(float(size))
        out.append(position)
    return out


def _decisions_from_transcripts(ticker: str) -> pd.DataFrame | None:
    """Rebuild the decisions table by parsing ``results/transcripts/{ticker}_*.md``."""
    if not TRANSCRIPTS_DIR.exists():
        return None
    rows = []
    for path in sorted(TRANSCRIPTS_DIR.glob(f"{ticker}_*.md")):
        parsed = parse_transcript(path)
        if parsed and parsed["ticker"].upper() == ticker.upper():
            rows.append(parsed)
    if not rows:
        return None
    df = pd.DataFrame(rows)[["date", "action", "size", "verdict"]]
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["position_after"] = _positions_after(df["action"], df["size"])
    return df


def load_agent_decisions(ticker: str) -> tuple[pd.DataFrame | None, str]:
    """Load agent decisions for ``ticker``: the decisions CSV, else transcripts.

    Returns ``(df, source)`` where df has columns
    ``date (Timestamp), action, size, verdict, position_after`` sorted by
    date, and source describes where the data came from. ``(None, "")`` when
    neither source exists.
    """
    csv_path = RESULTS_DIR / f"agents_{ticker}_decisions.csv"
    if csv_path.exists():
        try:
            df = pd.read_csv(csv_path)
            if {"date", "action"}.issubset(df.columns):
                df["date"] = pd.to_datetime(df["date"])
                df["action"] = df["action"].astype(str).str.upper().str.strip()
                df["size"] = pd.to_numeric(df.get("size"), errors="coerce").fillna(0.0)
                if "verdict" not in df.columns:
                    df["verdict"] = ""
                df = df.sort_values("date").reset_index(drop=True)
                pos = pd.to_numeric(df.get("position_after"), errors="coerce")
                if pos is None or pos.isna().any():
                    df["position_after"] = _positions_after(df["action"], df["size"])
                else:
                    df["position_after"] = pos
                cols = ["date", "action", "size", "verdict", "position_after"]
                return df[cols], "decisions file"
        except Exception:
            pass  # malformed CSV: fall back to transcripts
    df = _decisions_from_transcripts(ticker)
    if df is not None:
        return df, "transcripts"
    return None, ""


def decisions_to_signal(decisions: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    """Dense desired-position series from sparse decisions on ``index``.

    BUY -> +size, SELL -> -size on the first trading day >= the decision
    date; HOLD contributes nothing (the prior position is carried by ffill).
    """
    raw = pd.Series(np.nan, index=index, dtype=float)
    for row in decisions.sort_values("date").itertuples(index=False):
        loc = index.searchsorted(pd.Timestamp(row.date))
        if loc >= len(index):
            continue
        if row.action == "BUY":
            raw.iloc[loc] = abs(float(row.size))
        elif row.action == "SELL":
            raw.iloc[loc] = -abs(float(row.size))
    return raw.ffill().fillna(0.0)


def default_agent_end(df: pd.DataFrame, decisions: pd.DataFrame,
                      extra_days: int = 3) -> pd.Timestamp:
    """End of the agent evaluation window: last decision + a few trading days.

    The original CLI runs let the final position accrue returns for a few
    days past the last decision; +3 trading days reproduces the committed
    AAPL run (last decision 2026-05-27, evaluation end 2026-06-01).
    """
    last = pd.Timestamp(decisions["date"].max())
    loc = int(df.index.searchsorted(last))
    end_loc = min(len(df.index) - 1, loc + int(extra_days))
    return df.index[end_loc]


def agent_window_backtest(df: pd.DataFrame, decisions: pd.DataFrame,
                          end: pd.Timestamp | None = None,
                          cost_bps: float = 10.0, rf: float = 0.0,
                          ) -> BacktestResult | None:
    """Score the recorded agent decisions with the standard backtest engine.

    The window runs from the first decision date to ``end`` (default: the
    last decision plus three trading days). Returns None when the window
    holds no trading days.
    """
    if decisions is None or decisions.empty:
        return None
    dec = decisions.sort_values("date")
    start_ts = pd.Timestamp(dec["date"].iloc[0])
    end_ts = pd.Timestamp(end) if end is not None else default_agent_end(df, dec)
    period = df.loc[(df.index >= start_ts) & (df.index <= end_ts)]
    if period.empty:
        return None
    signal = decisions_to_signal(dec, period.index)
    return backtest_signals(period, signal, cost_bps=cost_bps, rf=rf, name="agents")


# ---------------------------------------------------------------------------
# baseline comparison
# ---------------------------------------------------------------------------

def baseline_results(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                     cost_bps: float = 10.0, rf: float = 0.0,
                     ) -> dict[str, BacktestResult]:
    """Run the five rule baselines over ``[start, end]`` on one ticker.

    Signals are computed on the FULL history so indicator warm-up (for
    example the 200-day average) does not eat into short windows; only the
    backtest itself is restricted to the window.
    """
    period = df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]
    out: dict[str, BacktestResult] = {}
    if period.empty:
        return out
    for name, strategy in STRATEGIES.items():
        signal = strategy(df)
        label = STRATEGY_LABELS.get(name, name)
        out[label] = backtest_signals(period, signal, cost_bps=cost_bps, rf=rf, name=name)
    return out


def window_metrics(results: dict[str, BacktestResult], rf: float = 0.0) -> pd.DataFrame:
    """Standard metrics table for a dict of backtest results (net returns)."""
    return metrics_table({name: r.net_returns for name, r in results.items()}, rf=rf)


# ---------------------------------------------------------------------------
# transcripts listing
# ---------------------------------------------------------------------------

def list_transcripts() -> list[dict]:
    """All parsed transcripts in ``results/transcripts``, newest first."""
    if not TRANSCRIPTS_DIR.exists():
        return []
    items = []
    for path in TRANSCRIPTS_DIR.glob("*.md"):
        parsed = parse_transcript(path)
        if parsed:
            items.append(parsed)
    items.sort(key=lambda d: (d["date"], d["ticker"]), reverse=True)
    return items


# ---------------------------------------------------------------------------
# live decisions log (AI Trading Desk)
# ---------------------------------------------------------------------------

def append_live_decision(ticker: str, action: str, size: float, rationale: str) -> None:
    """Append one AI-desk decision to ``results/live_decisions.csv``."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    row = pd.DataFrame(
        [
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "ticker": ticker,
                "action": action,
                "size": float(size),
                "rationale": " ".join(str(rationale).split()),
            }
        ]
    )
    row.to_csv(
        LIVE_DECISIONS_PATH,
        mode="a",
        header=not LIVE_DECISIONS_PATH.exists(),
        index=False,
        encoding="utf-8",
    )


def load_live_decisions() -> pd.DataFrame | None:
    """The full live-decision history, or None when nothing has been logged."""
    if not LIVE_DECISIONS_PATH.exists():
        return None
    try:
        df = pd.read_csv(LIVE_DECISIONS_PATH)
    except Exception:
        return None
    return df if len(df) else None


def latest_live_decision() -> dict | None:
    """The most recent AI-desk decision as a plain dict, or None."""
    df = load_live_decisions()
    if df is None:
        return None
    last = df.iloc[-1]
    return {
        "timestamp": str(last.get("timestamp", "")),
        "ticker": str(last.get("ticker", "")),
        "action": str(last.get("action", "HOLD")).upper(),
        "size": float(pd.to_numeric(last.get("size"), errors="coerce") or 0.0),
        "rationale": str(last.get("rationale", "")),
    }


# ---------------------------------------------------------------------------
# LLM usage / cost
# ---------------------------------------------------------------------------

def usage_cost_table(usage: dict) -> tuple[pd.DataFrame, float]:
    """Per-model token usage with USD cost, plus the total cost.

    ``usage`` is ``LLMClient.usage``. Cache hits cost nothing (the response
    came from disk).
    """
    rows = []
    for model, stats in usage.items():
        in_price, out_price = PRICE_PER_MTOK.get(model, DEFAULT_PRICE_PER_MTOK)
        cost = (
            stats.get("input_tokens", 0) / 1e6 * in_price
            + stats.get("output_tokens", 0) / 1e6 * out_price
        )
        rows.append(
            {
                "model": model,
                "API calls": int(stats.get("calls", 0)),
                "cache hits (free)": int(stats.get("cache_hits", 0)),
                "input tokens": int(stats.get("input_tokens", 0)),
                "output tokens": int(stats.get("output_tokens", 0)),
                "cost (USD)": cost,
            }
        )
    table = pd.DataFrame(rows)
    total = float(table["cost (USD)"].sum()) if len(table) else 0.0
    return table, total


# ---------------------------------------------------------------------------
# paper portfolio (contract 4: results/portfolio.json)
# ---------------------------------------------------------------------------

def load_portfolio() -> dict:
    """Load ``results/portfolio.json``; create it with $100,000 cash if absent."""
    if PORTFOLIO_PATH.exists():
        try:
            with open(PORTFOLIO_PATH, encoding="utf-8") as fh:
                portfolio = json.load(fh)
        except (json.JSONDecodeError, OSError):
            portfolio = None
        if isinstance(portfolio, dict) and "cash" in portfolio:
            portfolio.setdefault("positions", [])
            portfolio.setdefault("log", [])
            return portfolio
    portfolio = {"cash": STARTING_CASH, "positions": [], "log": []}
    save_portfolio(portfolio)
    return portfolio


def save_portfolio(portfolio: dict) -> None:
    """Write the paper portfolio back to ``results/portfolio.json`` (UTF-8)."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(PORTFOLIO_PATH, "w", encoding="utf-8") as fh:
        json.dump(portfolio, fh, indent=2, ensure_ascii=False)


def find_position(portfolio: dict, ticker: str) -> dict | None:
    """The open position dict for ``ticker`` (case-insensitive), or None."""
    for position in portfolio.get("positions", []):
        if str(position.get("ticker", "")).upper() == ticker.upper():
            return position
    return None


def _log_trade(portfolio: dict, ticker: str, action: str, shares: float,
               price: float, note: str) -> None:
    portfolio.setdefault("log", []).append(
        {
            "date": datetime.now().isoformat(timespec="seconds"),
            "ticker": ticker,
            "action": action,
            "shares": round(float(shares), 4),
            "price": round(float(price), 4),
            "note": note,
        }
    )


def portfolio_buy(portfolio: dict, ticker: str, dollars: float, price: float,
                  note: str = "") -> tuple[bool, str]:
    """Buy ``dollars`` worth of ``ticker`` at ``price`` (fractional shares).

    Averages into an existing position. Returns ``(ok, message)``.
    """
    ticker = ticker.upper().strip()
    if not ticker:
        return False, "Enter a ticker symbol first."
    if price is None or price <= 0:
        return False, f"No valid price available for {ticker}."
    if dollars <= 0:
        return False, "The amount to invest must be greater than zero."
    cash = float(portfolio.get("cash", 0.0))
    if dollars > cash + 1e-9:
        return False, (
            f"Not enough cash: you have ${cash:,.2f} but tried to invest "
            f"${dollars:,.2f}."
        )
    shares = round(dollars / price, 4)
    if shares <= 0:
        return False, "The amount is too small to buy any shares."
    cost = shares * price
    portfolio["cash"] = cash - cost

    existing = find_position(portfolio, ticker)
    if existing is None:
        portfolio.setdefault("positions", []).append(
            {
                "ticker": ticker,
                "shares": shares,
                "entry_price": round(price, 4),
                "entry_date": datetime.now().strftime("%Y-%m-%d"),
            }
        )
    else:
        old_shares = float(existing.get("shares", 0.0))
        old_price = float(existing.get("entry_price", price))
        new_shares = old_shares + shares
        existing["shares"] = round(new_shares, 4)
        existing["entry_price"] = round(
            (old_shares * old_price + shares * price) / new_shares, 4
        )
    _log_trade(portfolio, ticker, "BUY", shares, price, note)
    return True, f"Bought {shares:,.4f} shares of {ticker} at ${price:,.2f} (${cost:,.2f})."


def portfolio_sell_all(portfolio: dict, ticker: str, price: float,
                       note: str = "") -> tuple[bool, str]:
    """Close the entire position in ``ticker`` at ``price``."""
    ticker = ticker.upper().strip()
    position = find_position(portfolio, ticker)
    if position is None:
        return False, f"You have no open position in {ticker} to sell."
    if price is None or price <= 0:
        return False, f"No valid price available for {ticker}."
    shares = float(position.get("shares", 0.0))
    proceeds = shares * price
    portfolio["cash"] = float(portfolio.get("cash", 0.0)) + proceeds
    portfolio["positions"] = [
        p for p in portfolio.get("positions", [])
        if str(p.get("ticker", "")).upper() != ticker
    ]
    _log_trade(portfolio, ticker, "SELL", shares, price, note)
    return True, f"Sold {shares:,.4f} shares of {ticker} at ${price:,.2f} (${proceeds:,.2f})."


def apply_ai_decision(portfolio: dict, ticker: str, action: str, size: float,
                      price: float | None, account_value: float,
                      ) -> tuple[bool, str]:
    """Translate the latest AI decision into a paper trade.

    Mapping (documented in the dashboard): BUY invests ``size x 10%`` of the
    total account value; SELL closes the position (the paper portfolio never
    shorts); HOLD does nothing.
    """
    action = (action or "HOLD").upper()
    note = f"AI desk decision (size {float(size):.2f})"
    if action == "BUY":
        if price is None or price <= 0:
            return False, f"No valid price available for {ticker}."
        dollars = max(0.0, float(size)) * 0.10 * float(account_value)
        dollars = min(dollars, float(portfolio.get("cash", 0.0)))
        if dollars < 1.0:
            return False, (
                "Nothing to buy: the AI position size times 10% of your account "
                "is smaller than $1 (or you have no cash left)."
            )
        return portfolio_buy(portfolio, ticker, dollars, price, note=note)
    if action == "SELL":
        if find_position(portfolio, ticker) is None:
            return False, (
                f"The AI said SELL {ticker}, but you hold no shares of it. "
                "The paper portfolio never sells shares it does not own (no shorting)."
            )
        if price is None or price <= 0:
            return False, f"No valid price available for {ticker}."
        return portfolio_sell_all(portfolio, ticker, price, note=note)
    return False, "The AI decision was HOLD, so there is nothing to trade."
