"""Tests for the one-shot desk path: workspace resolution, the desk snapshot
builder (lookahead guard, live-data guards, data-quality notes), the latest
price cache policy, and the ``snapshot`` / ``desk`` CLI subcommands.

No network: prices are synthetic and the Yahoo fetchers / LLM pipeline are
monkeypatched.
"""
from __future__ import annotations

import json
import os
import time

import numpy as np
import pandas as pd
import pytest

from tradinglab import cli
from tradinglab import config as config_mod
from tradinglab.agents import desk as desk_mod
from tradinglab.agents.desk import (
    build_desk_snapshot,
    decision_to_dict,
    fetch_latest_prices,
    render_decision_card,
    snapshot_to_dict,
)
from tradinglab.agents.graph import TradingAgentsPipeline
from tradinglab.agents.state import (
    AgentDayLog,
    AnalystReport,
    DebateResult,
    DebateTurn,
    RiskVerdict,
    TraderDecision,
)
from tradinglab.data import fundamentals as fundamentals_mod
from tradinglab.data import news as news_mod

NY = "America/New_York"


def _synthetic_df(n: int = 320, seed: int = 3, end: str = "2026-03-13") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=n)
    close = 180.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
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


def _after_close(df: pd.DataFrame) -> pd.Timestamp:
    """A New York timestamp on the last bar's date, after the closing print."""
    return pd.Timestamp(f"{df.index[-1]:%Y-%m-%d} 17:00", tz=NY)


def _day_log(ticker: str = "ABC", date: str = "2026-03-13") -> AgentDayLog:
    return AgentDayLog(
        ticker=ticker,
        date=date,
        reports=[AnalystReport(role="Market Analyst", summary="Trend is up.")],
        debate=DebateResult(
            transcript=[DebateTurn("Bull", "Momentum."), DebateTurn("Bear", "Stretched.")],
            verdict="BULLISH",
            rationale="Trend evidence dominates.",
        ),
        decision=TraderDecision(action="BUY", size=0.4, rationale="Aligned signals."),
        risk=RiskVerdict(approved=True, final_action="BUY", final_size=0.25,
                         rationale="Size cut for volatility."),
        final_action="BUY",
        final_size=0.25,
    )


# ---------------------------------------------------------------------------
# workspace resolution
# ---------------------------------------------------------------------------

def test_project_root_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGLAB_HOME", str(tmp_path / "home"))
    assert config_mod._resolve_project_root() == (tmp_path / "home").resolve()


def test_project_root_is_checkout_when_running_from_source(monkeypatch):
    monkeypatch.delenv("TRADINGLAB_HOME", raising=False)
    root = config_mod._resolve_project_root()
    assert (root / "pyproject.toml").is_file()


def test_project_root_falls_back_to_user_dir_for_installed_package(monkeypatch, tmp_path):
    """A pip-installed copy has no pyproject.toml two levels up: its data must
    not land inside the interpreter's lib directory."""
    monkeypatch.delenv("TRADINGLAB_HOME", raising=False)
    monkeypatch.setattr(config_mod.Path, "home", classmethod(lambda cls: tmp_path))
    installed = tmp_path / "lib" / "site-packages" / "tradinglab" / "config.py"
    installed.parent.mkdir(parents=True)
    installed.touch()
    assert config_mod._resolve_project_root(installed) == tmp_path / ".tradinglab"


# ---------------------------------------------------------------------------
# snapshot builder
# ---------------------------------------------------------------------------

def test_latest_snapshot_uses_last_bar_and_normalizes_ticker():
    df = _synthetic_df()
    desk = build_desk_snapshot(" abc ", live=False, df=df, now=_after_close(df))
    assert desk.snapshot.ticker == "ABC"
    assert desk.as_of == "2026-03-13"
    assert desk.snapshot.date == desk.as_of
    assert desk.last_close == pytest.approx(float(df["close"].iloc[-1]))
    assert desk.history_rows == len(df)
    assert desk.live is False
    assert desk.notes == []


def test_historical_snapshot_ignores_future_rows():
    """No lookahead: mutating every row after the analysis date must not
    change a single character of what the agents see."""
    df = _synthetic_df()
    date = "2026-02-13"
    perturbed = df.copy()
    perturbed.loc[perturbed.index > pd.Timestamp(date), ["open", "high", "low", "close"]] *= 3.0
    a = build_desk_snapshot("ABC", date=date, df=df)
    b = build_desk_snapshot("ABC", date=date, df=perturbed)
    assert a.as_of == b.as_of == date
    assert a.snapshot.context_block() == b.snapshot.context_block()
    assert a.history_rows == int((df.index <= pd.Timestamp(date)).sum())


def test_historical_snapshot_never_gets_current_fundamentals_or_news(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("live data must not be fetched for a past date")

    monkeypatch.setattr(fundamentals_mod, "fetch_fundamentals_snapshot", boom)
    monkeypatch.setattr(news_mod, "fetch_current_news", boom)
    desk = build_desk_snapshot("ABC", date="2026-02-13", live=True, df=_synthetic_df())
    assert desk.live is False
    assert desk.snapshot.fundamentals is None and desk.snapshot.news is None
    assert any("leak the future" in note for note in desk.notes)


def test_named_historical_snapshot_warns_about_memorization():
    df = _synthetic_df()
    named = build_desk_snapshot("ABC", date="2026-02-13", live=False, df=df)
    assert any("remember what happened next" in note for note in named.notes)
    masked = build_desk_snapshot("ABC", date="2026-02-13", live=False, anonymize=True, df=df)
    assert not any("remember what happened next" in note for note in masked.notes)


def test_non_trading_date_resolves_to_previous_bar():
    df = _synthetic_df()
    desk = build_desk_snapshot("ABC", date="2026-03-08", live=False, df=df)  # a Sunday
    assert desk.as_of == "2026-03-06"


def test_date_before_history_raises():
    with pytest.raises(ValueError, match="No price history"):
        build_desk_snapshot("ABC", date="1999-01-01", df=_synthetic_df())


def test_empty_ticker_raises():
    with pytest.raises(ValueError, match="ticker"):
        build_desk_snapshot("  ", df=_synthetic_df())


def test_live_snapshot_injects_fundamentals_and_news(monkeypatch):
    monkeypatch.setattr(
        fundamentals_mod, "fetch_fundamentals_snapshot",
        lambda t: {"available": True, "ticker": t, "trailing_pe": 21.5},
    )
    monkeypatch.setattr(
        news_mod, "fetch_current_news",
        lambda t: [{"title": "ABC beats estimates", "publisher": "Wire", "date": "2026-03-12"}],
    )
    df = _synthetic_df()
    desk = build_desk_snapshot("ABC", df=df, now=_after_close(df))
    assert desk.live is True
    assert "Trailing P/E:      21.50" in desk.snapshot.fundamentals
    assert desk.snapshot.news == ["2026-03-12 [Wire] ABC beats estimates"]
    block = desk.snapshot.context_block()
    assert "--- Fundamentals ---" in block and "ABC beats estimates" in block
    assert desk.notes == []


def test_live_failures_are_reported_not_passed_to_agents(monkeypatch):
    """An unavailable fundamentals fetch must not reach the prompt as a fake
    fundamentals block; both gaps are surfaced as notes."""
    monkeypatch.setattr(
        fundamentals_mod, "fetch_fundamentals_snapshot",
        lambda t: {"available": False, "error": "timeout"},
    )
    monkeypatch.setattr(news_mod, "fetch_current_news", lambda t: [])
    df = _synthetic_df()
    desk = build_desk_snapshot("ABC", df=df, now=_after_close(df))
    assert desk.live is False
    assert desk.snapshot.fundamentals is None and desk.snapshot.news is None
    assert "--- Fundamentals ---" not in desk.snapshot.context_block()
    assert any("timeout" in note for note in desk.notes)
    assert any("No news headlines" in note for note in desk.notes)


def test_anonymized_snapshot_drops_live_data_and_hides_levels(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("live data must not be fetched when anonymizing")

    monkeypatch.setattr(fundamentals_mod, "fetch_fundamentals_snapshot", boom)
    monkeypatch.setattr(news_mod, "fetch_current_news", boom)
    df = _synthetic_df()
    desk = build_desk_snapshot("ABC", anonymize=True, df=df, now=_after_close(df))
    payload = snapshot_to_dict(desk)
    assert payload["anonymized"] is True
    assert payload["last_close"] is None
    assert "TICKER-X" in payload["context"] and "ABC" not in payload["context"]
    assert "2026-" not in payload["context"]
    assert any("anonymization" in note for note in desk.notes)


def test_partial_session_bar_is_flagged():
    df = _synthetic_df()
    midday = pd.Timestamp(f"{df.index[-1]:%Y-%m-%d} 11:30", tz=NY)
    desk = build_desk_snapshot("ABC", live=False, df=df, now=midday)
    assert any("partial session" in note for note in desk.notes)


def test_stale_latest_bar_is_flagged_but_weekend_is_not():
    df = _synthetic_df()  # last bar Friday 2026-03-13
    sunday = pd.Timestamp("2026-03-15 12:00", tz=NY)
    assert build_desk_snapshot("ABC", live=False, df=df, now=sunday).notes == []
    later = pd.Timestamp("2026-03-27 12:00", tz=NY)
    notes = build_desk_snapshot("ABC", live=False, df=df, now=later).notes
    assert any("14 days old" in note for note in notes)


# ---------------------------------------------------------------------------
# latest-price cache policy
# ---------------------------------------------------------------------------

@pytest.fixture
def capture_force(monkeypatch):
    seen: dict = {}

    def fake_fetch(ticker, start, end=None, cache_dir=None, force=False):
        seen["force"] = force
        return {ticker: _synthetic_df()}

    monkeypatch.setattr(desk_mod, "fetch_prices", fake_fetch)
    return seen


def _write_cache(tmp_path, last_bar: str, written: pd.Timestamp):
    start = config_mod.DataConfig().start
    path = tmp_path / f"ABC_{start}_latest.parquet"
    _synthetic_df(end=last_bar).to_parquet(path)
    stamp = written.tz_convert("UTC").timestamp()
    os.utime(path, (stamp, stamp))
    return path


def test_latest_prices_no_cache_does_not_force(capture_force, tmp_path):
    fetch_latest_prices("ABC", cache_dir=tmp_path)
    assert capture_force["force"] is False


def test_latest_prices_reuses_fresh_cache_with_complete_bar(capture_force, tmp_path):
    recent = pd.Timestamp.now(tz=NY) - pd.Timedelta(hours=1)
    last_bar = (recent - pd.Timedelta(days=4)).strftime("%Y-%m-%d")
    _write_cache(tmp_path, last_bar, recent)
    fetch_latest_prices("ABC", cache_dir=tmp_path)
    assert capture_force["force"] is False


def test_latest_prices_refetches_cache_older_than_a_day(capture_force, tmp_path):
    old = pd.Timestamp(time.time() - 30 * 3600, unit="s", tz="UTC").tz_convert(NY)
    _write_cache(tmp_path, "2026-03-13", old)
    fetch_latest_prices("ABC", cache_dir=tmp_path)
    assert capture_force["force"] is True


def test_latest_prices_refetches_cache_captured_mid_session(capture_force, tmp_path, monkeypatch):
    """A bar downloaded at 11:00 on its own date is an intraday reading; it
    must never be served later as that day's close."""
    written = pd.Timestamp("2026-03-13 11:00", tz=NY)
    _write_cache(tmp_path, "2026-03-13", written)
    # Same evening: well inside the 24h freshness window.
    monkeypatch.setattr(desk_mod.time, "time", lambda: written.timestamp() + 7 * 3600)
    fetch_latest_prices("ABC", cache_dir=tmp_path)
    assert capture_force["force"] is True


def test_latest_prices_keeps_cache_captured_after_the_close(capture_force, tmp_path, monkeypatch):
    written = pd.Timestamp("2026-03-13 17:30", tz=NY)
    _write_cache(tmp_path, "2026-03-13", written)
    monkeypatch.setattr(desk_mod.time, "time", lambda: written.timestamp() + 3600)
    fetch_latest_prices("ABC", cache_dir=tmp_path)
    assert capture_force["force"] is False


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def test_decision_payload_and_card():
    df = _synthetic_df()
    desk = build_desk_snapshot("ABC", live=False, df=df, now=_after_close(df))
    log = _day_log()
    payload = decision_to_dict(log, desk, usage={"m": {"calls": 2}}, cost_usd=0.31)
    assert payload["final_action"] == "BUY" and payload["final_size"] == 0.25
    assert payload["trader"]["size"] == 0.4
    assert payload["fund_manager"]["approved"] is True
    assert payload["research_verdict"] == "BULLISH"
    assert payload["debate"][1] == {"speaker": "Bear", "text": "Stretched."}
    assert payload["cost_usd"] == 0.31
    assert "not investment advice" in payload["disclaimer"]
    json.dumps(payload)  # must be serializable as-is

    card = render_decision_card(log, desk)
    assert "FINAL DECISION:   BUY 0.25" in card
    assert "long, target exposure 0.25 of capital" in card
    assert "not investment advice" in card


def test_sell_and_hold_are_described_plainly():
    assert "short or avoid" in desk_mod.exposure_phrase("SELL", 0.3)
    assert "-0.30" in desk_mod.exposure_phrase("SELL", 0.3)
    assert desk_mod.exposure_phrase("HOLD", 0.0) == "no change to the existing stance"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@pytest.fixture
def offline_cli(monkeypatch, tmp_path):
    """Route the CLI at synthetic prices and a temp results/cache tree."""
    df = _synthetic_df()
    monkeypatch.setattr(desk_mod, "fetch_latest_prices", lambda ticker, **_k: df)
    monkeypatch.setattr(cli, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(cli, "LLM_CACHE_DIR", tmp_path / "llm")
    return tmp_path


def test_cli_snapshot_json_is_a_single_document(offline_cli, capsys):
    cli.main(["snapshot", "--ticker", "abc", "--no-live", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["ticker"] == "ABC"
    assert payload["as_of"] == "2026-03-13"
    assert payload["context"].startswith("=== Market snapshot: ABC")
    assert payload["has_fundamentals"] is False and payload["news_headlines"] == 0


def test_cli_snapshot_text_prints_block_then_metadata(offline_cli, capsys):
    cli.main(["snapshot", "--ticker", "ABC", "--no-live", "--date", "2026-02-13"])
    out = capsys.readouterr().out
    assert out.index("=== Market snapshot: ABC") < out.index("--- Snapshot metadata ---")
    assert "as of: 2026-02-13" in out


def test_cli_snapshot_bad_date_exits_cleanly(offline_cli, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["snapshot", "--ticker", "ABC", "--date", "1999-01-01"])
    assert exc.value.code == 1
    assert "error: No price history" in capsys.readouterr().err


def test_cli_desk_json_runs_pipeline_once_and_saves_artifacts(offline_cli, monkeypatch, capsys):
    seen: list = []

    def decide(self, snapshot):
        seen.append(snapshot)
        return _day_log(snapshot.ticker, snapshot.date)

    monkeypatch.setattr(TradingAgentsPipeline, "decide", decide)
    cli.main(["desk", "--ticker", "ABC", "--no-live", "--json", "--debate-rounds", "1"])
    captured = capsys.readouterr()

    payload = json.loads(captured.out)  # stdout is JSON only; progress is on stderr
    assert len(seen) == 1 and seen[0].date == "2026-03-13"
    assert payload["final_action"] == "BUY" and payload["as_of"] == "2026-03-13"
    assert "running the agent desk" in captured.err

    out_dir = offline_cli / "results" / "desk"
    assert (out_dir / "ABC_2026-03-13.md").read_text(encoding="utf-8").startswith(
        "# Trading agents transcript — ABC — 2026-03-13"
    )
    saved = json.loads((out_dir / "ABC_2026-03-13.json").read_text(encoding="utf-8"))
    assert saved["transcript_path"].endswith("ABC_2026-03-13.md")
    # Desk runs must not land where the dashboard replays backtest decisions.
    assert not (offline_cli / "results" / "transcripts").exists()


def test_cli_desk_text_prints_decision_card(offline_cli, monkeypatch, capsys):
    monkeypatch.setattr(
        TradingAgentsPipeline, "decide",
        lambda self, snapshot: _day_log(snapshot.ticker, snapshot.date),
    )
    cli.main(["desk", "--ticker", "ABC", "--no-live"])
    out = capsys.readouterr().out
    assert "FINAL DECISION:   BUY 0.25" in out
    assert "saved:" in out
