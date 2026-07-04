"""Tests for review fixes: live fundamentals/news injection, max_days vs the
evaluation window, --every/--max-days CLI validation, and inclusive fetch end.

No network: yfinance is monkeypatched and the agent pipeline is stubbed.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from tradinglab.agents.graph import TradingAgentsPipeline, run_agent_backtest
from tradinglab.cli import build_parser
from tradinglab.config import AgentConfig
from tradinglab.data import prices as prices_mod
from tradinglab.data.prices import fetch_prices


def _synthetic_df(n: int = 300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.001, n)),
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1e5, 1e6, n).astype(float),
        },
        index=idx,
    )


def _stub_decide(captured: list):
    def decide(self, snapshot):
        captured.append(snapshot)
        return SimpleNamespace(
            ticker=snapshot.ticker,
            date=snapshot.date,
            final_action="BUY",
            final_size=1.0,
            debate=SimpleNamespace(verdict="BULLISH"),
        )

    return decide


def test_run_agent_backtest_injects_fundamentals_and_news(monkeypatch):
    df = _synthetic_df()
    captured: list = []
    monkeypatch.setattr(TradingAgentsPipeline, "decide", _stub_decide(captured))

    start = df.index[270].strftime("%Y-%m-%d")
    end = df.index[-1].strftime("%Y-%m-%d")
    run_agent_backtest(
        df, "TEST", start, end, client=None, cfg=AgentConfig(),
        every=10, fundamentals="PE: 10", news=["headline one"],
    )
    assert captured, "pipeline never ran"
    assert all(s.fundamentals == "PE: 10" for s in captured)
    assert all(s.news == ["headline one"] for s in captured)


def test_max_days_caps_decisions_without_shrinking_window(monkeypatch):
    df = _synthetic_df()
    captured: list = []
    monkeypatch.setattr(TradingAgentsPipeline, "decide", _stub_decide(captured))

    start_ts, end_ts = df.index[270], df.index[-1]
    period = df.loc[start_ts:end_ts]
    result, logs = run_agent_backtest(
        df, "TEST", start_ts.strftime("%Y-%m-%d"), end_ts.strftime("%Y-%m-%d"),
        client=None, cfg=AgentConfig(), every=1, max_days=1,
    )
    # Only one (paid) decision...
    assert len(logs) == 1
    # ...but the evaluation window still spans the full requested range,
    # so the single BUY accrues returns after the decision date.
    assert result.net_returns.index.equals(period.index)
    assert (result.net_returns.iloc[1:] != 0).any()


def test_cli_rejects_nonpositive_every_and_max_days(capsys):
    parser = build_parser()
    base = ["agents", "--ticker", "T", "--start", "2024-01-01", "--end", "2024-02-01"]
    for extra in (["--every", "0"], ["--every", "-1"], ["--max-days", "0"]):
        with pytest.raises(SystemExit):
            parser.parse_args(base + extra)
    # valid values still parse
    ns = parser.parse_args(base + ["--every", "5", "--max-days", "3"])
    assert ns.every == 5 and ns.max_days == 3


def test_fetch_prices_end_is_inclusive(monkeypatch, tmp_path):
    calls = {}

    def fake_download(ticker, start=None, end=None, **kwargs):
        calls["start"], calls["end"] = start, end
        idx = pd.bdate_range("2024-01-02", "2024-01-10")
        n = len(idx)
        return pd.DataFrame(
            {
                "Open": np.full(n, 10.0),
                "High": np.full(n, 11.0),
                "Low": np.full(n, 9.0),
                "Close": np.full(n, 10.5),
                "Volume": np.full(n, 1000.0),
            },
            index=idx,
        )

    monkeypatch.setattr(prices_mod.yf, "download", fake_download)
    out = fetch_prices("TEST", "2024-01-02", "2024-01-10", cache_dir=tmp_path)

    # yfinance's exclusive end is compensated with +1 calendar day...
    assert calls["end"] == "2024-01-11"
    # ...while the cache filename keeps the user-facing end.
    assert (tmp_path / "TEST_2024-01-02_2024-01-10.parquet").exists()
    assert pd.Timestamp("2024-01-10") in out["TEST"].index
