"""Memorization probe: does the backbone LLM already *know* a price window?

Stated knowledge cutoffs cannot be trusted for leakage decisions, and no
prompt clause can remove knowledge baked into pretraining weights. This
module measures memorization empirically: it asks the model — with NO market
data in the prompt, only the ticker and dates — to recall closing prices and
monthly directions inside a candidate backtest window, then scores the
answers against the actual price history.

Two question families:

* **Price recall** — for ``n_dates`` dates evenly spaced across the window,
  ask for the approximate closing price on that date. Errors are scored as
  absolute percent error (APE) against the actual close. The comparison
  baseline is the *naive carry-forward anchor*: the last close strictly
  before the window start. A model with no knowledge of the window has no
  reason to beat that anchor.
* **Direction recall** — for each calendar month in the window, ask whether
  the stock rose or fell from the month's first to last trading day. Chance
  performance is 50%.

Verdict heuristics (thresholds are judgment calls, not statistical tests —
with the default 8 price dates and a handful of months, sampling noise is
material; treat the verdict as a screen, not a proof):

* ``CONTAMINATED`` — ``price_mape < 0.5 * naive_mape`` (the model halves the
  no-information anchor's error), OR ``direction_hit_rate >= 0.75`` with at
  least half of the direction answers given at high confidence (and at least
  2 direction questions answered).
* ``LIKELY CLEAN`` — price errors at or worse than the naive anchor
  (``price_mape >= naive_mape``) AND direction near chance
  (``direction_hit_rate <= 0.60``).
* ``INCONCLUSIVE`` — anything else, including too few parseable answers.

Caveat: actual closes come from auto-adjusted data; for windows followed by
splits/large dividends the model may recall the unadjusted nominal price,
inflating APE. That biases the probe toward LIKELY CLEAN, never toward a
false CONTAMINATED on the price branch.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from tradinglab.agents.llm import LLMClient, extract_json

# --- verdict thresholds (heuristics; see module docstring) -----------------
CONTAMINATED_MAPE_RATIO = 0.5      # price_mape < ratio * naive_mape => memorized
CONTAMINATED_HIT_RATE = 0.75       # direction hit rate at/above this ...
CONTAMINATED_HIGH_CONF_FRACTION = 0.5  # ... with >= this fraction high-confidence
CLEAN_HIT_RATE_MAX = 0.60          # "near chance" ceiling for LIKELY CLEAN
_MIN_DIRECTION_QUESTIONS = 2       # direction branch needs >= 2 answers to fire

_CONFIDENCE_LEVELS = {"high", "medium", "low"}
_UP_WORDS = {"up", "rise", "rose", "risen", "higher", "increase", "increased", "gain", "gained"}
_DOWN_WORDS = {"down", "fall", "fell", "fallen", "lower", "decrease", "decreased", "decline", "declined"}

# The system prompt deliberately contains no market data — the whole point is
# that the ONLY route to a correct answer is the model's pretraining memory.
_PRICE_SYSTEM = (
    "You are being probed for memorized historical market knowledge. Answer "
    "from your own training knowledge only; no market data is provided and "
    "none should be inferred from the question. If you do not know, give "
    "your best guess and set confidence to \"low\". Respond with ONLY a JSON "
    "object, no prose."
)
_DIRECTION_SYSTEM = _PRICE_SYSTEM


def _norm_confidence(value: object) -> str:
    """Normalize a reported confidence to high/medium/low (default low)."""
    text = str(value).strip().lower()
    return text if text in _CONFIDENCE_LEVELS else "low"


def _parse_price(value: object) -> float | None:
    """Parse a price answer into a positive finite float, else None."""
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
    try:
        price = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return price


def _parse_direction(value: object) -> str | None:
    """Normalize a direction answer to 'up'/'down', else None."""
    text = str(value).strip().lower()
    if text in _UP_WORDS:
        return "up"
    if text in _DOWN_WORDS:
        return "down"
    return None


def _probe_dates(window: pd.DataFrame, n_dates: int) -> pd.DatetimeIndex:
    """Pick up to ``n_dates`` trading dates evenly spaced across the window."""
    if n_dates < 1:
        raise ValueError(f"n_dates must be >= 1 (got {n_dates})")
    n = min(n_dates, len(window))
    positions = np.unique(np.linspace(0, len(window) - 1, num=n).round().astype(int))
    return window.index[positions]


def _month_spans(window: pd.DataFrame) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """(first, last) trading day per calendar month; single-day months skipped."""
    spans: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for _, grp in window.groupby([window.index.year, window.index.month]):
        first, last = grp.index[0], grp.index[-1]
        if first != last:
            spans.append((first, last))
    return spans


def compute_verdict(
    price_mape: float | None,
    naive_mape: float | None,
    direction_hit_rate: float | None,
    direction_high_conf_fraction: float | None,
    n_price: int,
    n_direction: int,
) -> str:
    """Apply the contamination heuristics documented in the module docstring.

    Returns ``"CONTAMINATED"``, ``"LIKELY CLEAN"``, or ``"INCONCLUSIVE"``.
    ``None`` metrics (no parseable answers) never satisfy a branch.
    """
    price_contaminated = (
        n_price > 0
        and price_mape is not None
        and naive_mape is not None
        and naive_mape > 0
        and price_mape < CONTAMINATED_MAPE_RATIO * naive_mape
    )
    direction_contaminated = (
        n_direction >= _MIN_DIRECTION_QUESTIONS
        and direction_hit_rate is not None
        and direction_hit_rate >= CONTAMINATED_HIT_RATE
        and (direction_high_conf_fraction or 0.0) >= CONTAMINATED_HIGH_CONF_FRACTION
    )
    if price_contaminated or direction_contaminated:
        return "CONTAMINATED"

    price_clean = (
        n_price > 0
        and price_mape is not None
        and naive_mape is not None
        and price_mape >= naive_mape
    )
    direction_clean = (
        n_direction > 0
        and direction_hit_rate is not None
        and direction_hit_rate <= CLEAN_HIT_RATE_MAX
    )
    if price_clean and direction_clean:
        return "LIKELY CLEAN"
    return "INCONCLUSIVE"


def run_probe(
    client: LLMClient,
    df: pd.DataFrame,
    ticker: str,
    start: str,
    end: str,
    n_dates: int = 8,
    model: str | None = None,
) -> dict:
    """Probe the model's memorized knowledge of ``ticker`` over ``[start, end]``.

    ``df`` must be a canonical daily OHLCV frame covering the window (and
    ideally history before ``start``, which supplies the naive carry-forward
    anchor). Probe prompts contain ONLY the ticker and dates — never any
    price data — so every LLM call goes through the client's disk cache and
    is deterministic on re-runs. ``model=None`` uses the client's deep model.

    Returns a dict with per-question records (``price_questions``,
    ``direction_questions``) and a ``summary`` holding ``price_mape``,
    ``naive_mape``, ``direction_hit_rate``, high-confidence counts, skip
    counts, and the heuristic ``verdict`` (see module docstring for the
    thresholds). Unparseable model answers are skipped and counted, never
    scored.
    """
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    window = df.loc[start_ts:end_ts]
    if window.empty:
        raise ValueError(f"No trading days for {ticker} in [{start}, {end}]")
    if model is None:
        model = getattr(getattr(client, "cfg", None), "deep_model", None)

    # Naive carry-forward anchor: last close strictly BEFORE the window. A
    # model with no knowledge of the window should not beat this guess.
    before = df.loc[df.index < start_ts, "close"]
    if len(before) > 0:
        anchor = float(before.iloc[-1])
        anchor_date = before.index[-1].strftime("%Y-%m-%d")
    else:  # no pre-window history: fall back to the first in-window close
        anchor = float(window["close"].iloc[0])
        anchor_date = window.index[0].strftime("%Y-%m-%d")

    # --- (a) price recall ---------------------------------------------------
    price_records: list[dict] = []
    n_price_skipped = 0
    for ts in _probe_dates(window, n_dates):
        date_str = ts.strftime("%Y-%m-%d")
        user = (
            f"What was {ticker}'s approximate closing stock price "
            f"(split-adjusted, in USD) on {date_str}? Respond ONLY with JSON: "
            '{"price": <number>, "confidence": "high"|"medium"|"low"}'
        )
        text = client.chat(_PRICE_SYSTEM, user, model=model)
        try:
            payload = extract_json(text)
        except ValueError:
            n_price_skipped += 1
            continue
        predicted = _parse_price(payload.get("price"))
        if predicted is None:
            n_price_skipped += 1
            continue
        actual = float(window.at[ts, "close"])
        price_records.append(
            {
                "date": date_str,
                "model_price": predicted,
                "actual_close": actual,
                "confidence": _norm_confidence(payload.get("confidence")),
                "ape": abs(predicted - actual) / actual,
                "naive_ape": abs(anchor - actual) / actual,
            }
        )

    # --- (b) direction recall -------------------------------------------------
    direction_records: list[dict] = []
    n_direction_skipped = 0
    for first, last in _month_spans(window):
        first_str, last_str = first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d")
        user = (
            f"From {first_str} to {last_str}, did {ticker} stock rise or "
            'fall? Respond ONLY with JSON: {"direction": "up"|"down", '
            '"confidence": "high"|"medium"|"low"}'
        )
        text = client.chat(_DIRECTION_SYSTEM, user, model=model)
        try:
            payload = extract_json(text)
        except ValueError:
            n_direction_skipped += 1
            continue
        predicted = _parse_direction(payload.get("direction"))
        if predicted is None:
            n_direction_skipped += 1
            continue
        first_close = float(window.at[first, "close"])
        last_close = float(window.at[last, "close"])
        actual = "up" if last_close > first_close else "down"
        direction_records.append(
            {
                "month": first.strftime("%Y-%m"),
                "first_day": first_str,
                "last_day": last_str,
                "model_direction": predicted,
                "actual_direction": actual,
                "confidence": _norm_confidence(payload.get("confidence")),
                "hit": predicted == actual,
            }
        )

    # --- summary + verdict ----------------------------------------------------
    n_price = len(price_records)
    n_direction = len(direction_records)
    price_mape = (
        float(np.mean([r["ape"] for r in price_records])) if n_price else None
    )
    naive_mape = (
        float(np.mean([r["naive_ape"] for r in price_records])) if n_price else None
    )
    direction_hit_rate = (
        float(np.mean([r["hit"] for r in direction_records])) if n_direction else None
    )
    n_high_confidence = sum(
        1
        for r in price_records + direction_records
        if r["confidence"] == "high"
    )
    direction_high_conf_fraction = (
        sum(1 for r in direction_records if r["confidence"] == "high") / n_direction
        if n_direction
        else None
    )
    verdict = compute_verdict(
        price_mape,
        naive_mape,
        direction_hit_rate,
        direction_high_conf_fraction,
        n_price,
        n_direction,
    )

    return {
        "ticker": ticker,
        "start": start_ts.strftime("%Y-%m-%d"),
        "end": end_ts.strftime("%Y-%m-%d"),
        "model": model,
        "price_questions": price_records,
        "direction_questions": direction_records,
        "summary": {
            "model": model,
            "n_price": n_price,
            "n_price_skipped": n_price_skipped,
            "price_mape": price_mape,
            "naive_mape": naive_mape,
            "naive_anchor": anchor,
            "naive_anchor_date": anchor_date,
            "n_direction": n_direction,
            "n_direction_skipped": n_direction_skipped,
            "direction_hit_rate": direction_hit_rate,
            "n_high_confidence": n_high_confidence,
            "direction_high_confidence_fraction": direction_high_conf_fraction,
            "verdict": verdict,
        },
    }
