"""Offline tests for the memorization probe (scoring + verdict logic).

No network: the LLM is a FakeClient returning scripted JSON. Covers the
CONTAMINATED path (perfect recall), the LIKELY CLEAN path (anchor-or-worse
prices, chance-level direction), skip counting for unparseable answers, the
no-price-data-in-prompt invariant, and compute_verdict thresholds directly.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from tradinglab.agents.probe import compute_verdict, run_probe


def _rising_df(n: int = 160) -> pd.DataFrame:
    """Deterministic, strictly rising close series (direction 'up' each month)."""
    idx = pd.bdate_range("2024-01-01", periods=n)
    close = 100.0 + 0.5 * np.arange(n)
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(n, 1e6),
        },
        index=idx,
    )


class FakeClient:
    """Minimal LLMClient stand-in: routes chat() to a scripted handler."""

    def __init__(self, handler):
        self.handler = handler
        self.prompts: list[tuple[str, str]] = []

    def chat(self, system: str, user: str, model: str | None = None,
             max_tokens: int | None = None) -> str:
        self.prompts.append((system, user))
        return self.handler(user)


def _perfect_recall_handler(df: pd.DataFrame):
    """Answer every question exactly right, always with high confidence."""

    def handler(user: str) -> str:
        span = re.search(r"From (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})", user)
        if span:
            first, last = pd.Timestamp(span.group(1)), pd.Timestamp(span.group(2))
            direction = "up" if df.at[last, "close"] > df.at[first, "close"] else "down"
            return f'{{"direction": "{direction}", "confidence": "high"}}'
        date = re.search(r"on (\d{4}-\d{2}-\d{2})", user)
        assert date, f"unexpected probe prompt: {user!r}"
        actual = float(df.at[pd.Timestamp(date.group(1)), "close"])
        return f'{{"price": {actual}, "confidence": "high"}}'

    return handler


def test_perfect_recall_is_contaminated():
    df = _rising_df()
    client = FakeClient(_perfect_recall_handler(df))
    result = run_probe(client, df, "TEST", "2024-03-01", "2024-05-31", n_dates=8)

    s = result["summary"]
    assert s["n_price"] == 8 and s["n_price_skipped"] == 0
    assert s["price_mape"] == pytest.approx(0.0)
    assert s["naive_mape"] > 0  # rising series: the pre-window anchor is wrong
    assert s["direction_hit_rate"] == pytest.approx(1.0)
    assert s["n_direction"] == 3  # Mar, Apr, May
    assert s["n_high_confidence"] == 8 + 3
    assert s["verdict"] == "CONTAMINATED"


def test_anchor_level_answers_are_likely_clean():
    df = _rising_df()
    window = df.loc["2024-03-01":"2024-05-31"]
    anchor = float(df.loc[df.index < window.index[0], "close"].iloc[-1])

    def handler(user: str) -> str:
        if "rise or fall" in user:
            # Always wrong on a rising series -> hit rate 0 (near/below chance).
            return '{"direction": "down", "confidence": "low"}'
        # Strictly worse than the carry-forward anchor on a rising series.
        return f'{{"price": {anchor * 0.9}, "confidence": "low"}}'

    result = run_probe(FakeClient(handler), df, "TEST", "2024-03-01", "2024-05-31")
    s = result["summary"]
    assert s["price_mape"] > s["naive_mape"]
    assert s["direction_hit_rate"] == pytest.approx(0.0)
    assert s["n_high_confidence"] == 0
    assert s["verdict"] == "LIKELY CLEAN"


def test_unparseable_answers_are_skipped_and_counted():
    df = _rising_df()
    client = FakeClient(lambda user: "I cannot recall that, sorry.")
    result = run_probe(client, df, "TEST", "2024-03-01", "2024-05-31", n_dates=5)

    s = result["summary"]
    assert s["n_price"] == 0 and s["n_price_skipped"] == 5
    assert s["n_direction"] == 0 and s["n_direction_skipped"] == 3
    assert s["price_mape"] is None and s["direction_hit_rate"] is None
    assert s["verdict"] == "INCONCLUSIVE"
    assert result["price_questions"] == [] and result["direction_questions"] == []


def test_probe_prompts_contain_no_price_data():
    df = _rising_df()
    client = FakeClient(_perfect_recall_handler(df))
    run_probe(client, df, "TEST", "2024-03-01", "2024-05-31", n_dates=8)

    assert client.prompts, "probe made no LLM calls"
    for system, user in client.prompts:
        for text in (system, user):
            # Dates are the only numerals allowed (YYYY-MM-DD): no decimal
            # numbers (price levels) and no volume-scale integers.
            assert not re.search(r"\d+\.\d+", text), f"price leaked into prompt: {text!r}"
            assert not re.search(r"\d{5,}", text), f"level/volume leaked into prompt: {text!r}"
        assert "TEST" in user


def test_model_override_is_passed_through():
    df = _rising_df()
    seen: list[str | None] = []

    class RecordingClient(FakeClient):
        def chat(self, system, user, model=None, max_tokens=None):
            seen.append(model)
            return super().chat(system, user, model=model, max_tokens=max_tokens)

    client = RecordingClient(_perfect_recall_handler(df))
    result = run_probe(client, df, "TEST", "2024-03-01", "2024-03-31",
                       n_dates=2, model="claude-test-model")
    assert result["model"] == "claude-test-model"
    assert seen and all(m == "claude-test-model" for m in seen)


@pytest.mark.parametrize(
    "price_mape, naive_mape, hit, high_frac, n_price, n_dir, expected",
    [
        # model halves the naive anchor error -> memorized
        (0.01, 0.10, 0.5, 0.0, 8, 3, "CONTAMINATED"),
        # direction branch alone: high hit rate, mostly high confidence
        (None, None, 0.80, 0.80, 0, 4, "CONTAMINATED"),
        # at/worse than naive + near-chance direction -> clean
        (0.12, 0.10, 0.50, 0.0, 8, 3, "LIKELY CLEAN"),
        # better than naive but not 2x, direction below 0.75 -> inconclusive
        (0.06, 0.10, 0.70, 0.9, 8, 3, "INCONCLUSIVE"),
        # high hit rate but low confidence and prices at naive -> not contaminated
        (0.10, 0.10, 1.0, 0.0, 8, 3, "INCONCLUSIVE"),
        # a single direction answer cannot fire the direction branch
        (None, None, 1.0, 1.0, 0, 1, "INCONCLUSIVE"),
        # nothing parseable at all
        (None, None, None, None, 0, 0, "INCONCLUSIVE"),
    ],
)
def test_compute_verdict_thresholds(price_mape, naive_mape, hit, high_frac,
                                    n_price, n_dir, expected):
    assert compute_verdict(price_mape, naive_mape, hit, high_frac,
                           n_price, n_dir) == expected
