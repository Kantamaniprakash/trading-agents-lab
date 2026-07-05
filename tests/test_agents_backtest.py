"""Tests for review fixes: live fundamentals/news injection, max_days vs the
evaluation window, --every/--max-days CLI validation, inclusive fetch end, and
anonymization v2 (price/volume rebasing in the snapshot builder).

No network: yfinance is monkeypatched and the agent pipeline is stubbed.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from tradinglab.agents.graph import (
    TradingAgentsPipeline,
    run_agent_backtest,
    snapshot_from_history,
)
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


def _synthetic_df_at_level(level: float, n: int = 300, seed: int = 11,
                           vol_low: int = 100_000,
                           vol_high: int = 900_000) -> pd.DataFrame:
    """Random-walk OHLCV around a given price level with uniform volumes."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    close = level * np.exp(np.cumsum(rng.normal(0, 0.005, n)))
    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.001, n)),
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(vol_low, vol_high, n).astype(float),
        },
        index=idx,
    )


def _price_table_rows(price_table: str) -> list[list[str]]:
    """Data rows of a rendered price table, split into whitespace fields.

    Skips the optional anonymization label line and the column-header line by
    keeping only lines whose first field is a row label ("T-9".."T-0" or an
    ISO date). Fields: [label, open, high, low, close, volume].
    """
    rows = []
    for line in price_table.splitlines():
        fields = line.split()
        if fields and re.fullmatch(r"T-\d+|\d{4}-\d{2}-\d{2}", fields[0]):
            rows.append(fields)
    return rows


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


def test_anonymized_snapshot_rebases_price_levels():
    """Anonymize v2: prices are index levels (first displayed close = 100),
    and nothing near the real price scale leaks into the rendered block."""
    df = _synthetic_df_at_level(500.0)
    snap = snapshot_from_history(df, df.index[-1], "TEST", anonymize=True)

    assert "indexed (start = 100)" in snap.price_table
    rows = _price_table_rows(snap.price_table)
    assert len(rows) == 10
    assert float(rows[0][4]) == 100.0  # first displayed close, exactly

    # No rendered number anywhere in the prompt block is within 20% of the
    # real price level — the fingerprint the rebase exists to remove.
    real_close = float(df["close"].iloc[-1])
    block = snap.context_block()
    values = [float(v) for v in re.findall(r"\d+(?:\.\d+)?", block)]
    assert real_close > 300  # sanity: synthetic level is actually ~500
    assert not any(0.8 * real_close <= v <= 1.2 * real_close for v in values)

    # Scale-free outputs are unchanged by the rebase: RSI and the whole
    # returns/vol summary match the non-anonymized snapshot number-for-number.
    named = snapshot_from_history(df, df.index[-1], "TEST", anonymize=False)
    rsi = [l for l in snap.indicator_report.splitlines() if l.startswith("RSI")]
    rsi_named = [l for l in named.indicator_report.splitlines()
                 if l.startswith("RSI")]
    assert rsi == rsi_named
    assert snap.returns_summary == named.returns_summary


def test_anonymized_snapshot_masks_volume_magnitudes():
    """Anonymize v2: volume is % of average (100 = average day); raw share
    counts in the millions must not appear anywhere in the block."""
    df = _synthetic_df_at_level(50.0, seed=13,
                                vol_low=1_000_000, vol_high=9_000_000)
    snap = snapshot_from_history(df, df.index[-1], "TEST", anonymize=True)

    block = snap.context_block()
    assert not re.search(r"\d{7,}", block)  # no 7-digit raw volumes anywhere
    assert "% of average" in snap.price_table
    vols = [float(r[5]) for r in _price_table_rows(snap.price_table)]
    assert len(vols) == 10
    assert all(0 < v < 1000 for v in vols)  # relative scale, ~100 = average


def test_non_anonymized_snapshot_keeps_real_levels():
    """The anonymize=False path still renders real dates, real price levels
    and raw volumes (i.e. the rebase must not touch it)."""
    df = _synthetic_df_at_level(500.0)
    snap = snapshot_from_history(df, df.index[-1], "TEST", anonymize=False)

    rows = _price_table_rows(snap.price_table)
    assert len(rows) == 10
    tail = df.tail(10)
    assert [r[0] for r in rows] == [d.strftime("%Y-%m-%d") for d in tail.index]
    for row, (_, rec) in zip(rows, tail.iterrows()):
        assert float(row[4]) == pytest.approx(rec["close"], abs=0.005)
        assert float(row[5]) == pytest.approx(rec["volume"], abs=0.5)
    assert "indexed (start = 100)" not in snap.price_table
