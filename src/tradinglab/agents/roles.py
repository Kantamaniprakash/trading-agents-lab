"""Agent roles for the TradingAgents pipeline.

Each function wraps one team of the multi-agent workflow: analysts produce
reports, bull/bear researchers debate, a trader proposes a trade, and the
risk team vets it. All LLM traffic goes through
:class:`tradinglab.agents.llm.LLMClient`.
"""
from __future__ import annotations

import math
from typing import Callable

from tradinglab.agents.llm import LLMClient, extract_json
from tradinglab.agents.prompts import (
    BEAR_SYSTEM,
    BULL_SYSTEM,
    FACILITATOR_SYSTEM,
    FUND_MANAGER_SYSTEM,
    FUNDAMENTALS_ANALYST_SYSTEM,
    MARKET_ANALYST_SYSTEM,
    NEUTRAL_SYSTEM,
    NEWS_ANALYST_SYSTEM,
    RISKY_SYSTEM,
    SAFE_SYSTEM,
    SENTIMENT_ANALYST_SYSTEM,
    TRADER_SYSTEM,
)
from tradinglab.agents.state import (
    AnalystReport,
    DebateResult,
    DebateTurn,
    MarketSnapshot,
    RiskVerdict,
    TraderDecision,
)

_VALID_ACTIONS = {"BUY", "SELL", "HOLD"}
_VALID_VERDICTS = {"BULLISH", "BEARISH", "NEUTRAL"}


def _chat_json(call: Callable[[str, str], str], system: str, user: str) -> dict | None:
    """Call the LLM and parse a JSON object; retry once with a stricter
    instruction, returning None if the second attempt is also unparseable."""
    text = call(system, user)
    try:
        return extract_json(text)
    except ValueError:
        pass
    text = call(system, user + "\n\nRespond with ONLY the JSON object.")
    try:
        return extract_json(text)
    except ValueError:
        return None


def _clean_action(action: object, size: object) -> tuple[str, float]:
    """Normalize an (action, size) pair: invalid action -> HOLD, size clamped to [0, 1]."""
    act = str(action).strip().upper()
    if act not in _VALID_ACTIONS:
        act = "HOLD"
    try:
        val = float(size)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        val = 0.0
    if not math.isfinite(val):
        val = 0.0
    return act, min(max(val, 0.0), 1.0)


def _format_reports(reports: list[AnalystReport]) -> str:
    """Render analyst reports as a labeled text block."""
    parts = ["ANALYST REPORTS"]
    for report in reports:
        parts.append(f"--- {report.role} ---\n{report.summary}")
    return "\n\n".join(parts)


def _format_turns(title: str, turns: list[DebateTurn]) -> str:
    """Render debate turns as a labeled text block."""
    if not turns:
        return f"{title}\n(no turns yet)"
    parts = [title]
    for turn in turns:
        parts.append(f"[{turn.speaker}] {turn.text}")
    return "\n\n".join(parts)


def run_analysts(client: LLMClient, snapshot: MarketSnapshot) -> list[AnalystReport]:
    """Run the analyst team on a snapshot.

    The market analyst always runs (deep model). The fundamentals analyst runs
    only when the snapshot carries fundamentals; the news and sentiment
    analysts run only when it carries news (all three on the quick model).
    """
    context = snapshot.context_block()
    reports = [
        AnalystReport(role="Market Analyst",
                      summary=client.deep(MARKET_ANALYST_SYSTEM, context))
    ]
    if snapshot.fundamentals:
        reports.append(
            AnalystReport(role="Fundamentals Analyst",
                          summary=client.quick(FUNDAMENTALS_ANALYST_SYSTEM, context)))
    if snapshot.news:
        reports.append(
            AnalystReport(role="News Analyst",
                          summary=client.quick(NEWS_ANALYST_SYSTEM, context)))
        reports.append(
            AnalystReport(role="Sentiment Analyst",
                          summary=client.quick(SENTIMENT_ANALYST_SYSTEM, context)))
    return reports


def run_research_debate(client: LLMClient, snapshot: MarketSnapshot,
                        reports: list[AnalystReport], n_rounds: int) -> DebateResult:
    """Run `n_rounds` full bull/bear exchanges, then a facilitator verdict.

    The facilitator's JSON is parsed tolerantly; if both parse attempts fail
    the verdict defaults to NEUTRAL.
    """
    base = snapshot.context_block() + "\n\n" + _format_reports(reports)
    transcript: list[DebateTurn] = []
    for _ in range(n_rounds):
        for speaker, system in (("Bull", BULL_SYSTEM), ("Bear", BEAR_SYSTEM)):
            user = (
                base
                + "\n\n"
                + _format_turns("DEBATE SO FAR", transcript)
                + f"\n\nIt is your turn, {speaker} researcher. Make your case, "
                  "citing the data above, and rebut your opponent's latest "
                  "argument if one exists."
            )
            transcript.append(DebateTurn(speaker=speaker, text=client.deep(system, user)))

    facilitator_user = (
        base
        + "\n\n"
        + _format_turns("FULL DEBATE TRANSCRIPT", transcript)
        + "\n\nJudge the debate and deliver your verdict as JSON."
    )
    payload = _chat_json(client.deep, FACILITATOR_SYSTEM, facilitator_user)
    if payload is None:
        return DebateResult(
            transcript=transcript,
            verdict="NEUTRAL",
            rationale="Facilitator output was unparseable after retry; defaulting to NEUTRAL.",
        )
    verdict = str(payload.get("verdict", "NEUTRAL")).strip().upper()
    if verdict not in _VALID_VERDICTS:
        verdict = "NEUTRAL"
    return DebateResult(
        transcript=transcript,
        verdict=verdict,
        rationale=str(payload.get("rationale", "")).strip(),
    )


def run_trader(client: LLMClient, snapshot: MarketSnapshot,
               reports: list[AnalystReport], debate: DebateResult) -> TraderDecision:
    """Ask the trader (deep model) for a BUY/SELL/HOLD decision with a size in [0, 1]."""
    user = (
        snapshot.context_block()
        + "\n\n"
        + _format_reports(reports)
        + "\n\n"
        + _format_turns("RESEARCH DEBATE", debate.transcript)
        + f"\n\nFACILITATOR VERDICT: {debate.verdict}\nRationale: {debate.rationale}"
        + "\n\nProvide your trading decision as JSON."
    )
    payload = _chat_json(client.deep, TRADER_SYSTEM, user)
    if payload is None:
        return TraderDecision(
            action="HOLD",
            size=0.0,
            rationale="Trader output was unparseable after retry; defaulting to HOLD.",
        )
    action, size = _clean_action(payload.get("action"), payload.get("size"))
    return TraderDecision(action=action, size=size,
                          rationale=str(payload.get("rationale", "")).strip())


def run_risk_debate(client: LLMClient, snapshot: MarketSnapshot,
                    decision: TraderDecision, n_rounds: int) -> RiskVerdict:
    """Run the risk team over a proposed trade and return the fund manager's verdict.

    Risky/safe/neutral analysts each comment once per round (quick model); the
    fund manager (deep model) issues the final JSON. If the manager does not
    approve, or its output is unparseable, the final decision is HOLD with
    size 0.
    """
    base = (
        snapshot.context_block()
        + "\n\nPROPOSED TRADE\n"
        + f"action={decision.action}, size={decision.size:.2f}\n"
        + f"Trader rationale: {decision.rationale}"
    )
    comments: list[DebateTurn] = []
    for _ in range(n_rounds):
        for speaker, system in (("Risky", RISKY_SYSTEM), ("Safe", SAFE_SYSTEM),
                                ("Neutral", NEUTRAL_SYSTEM)):
            user = (
                base
                + "\n\n"
                + _format_turns("RISK DISCUSSION SO FAR", comments)
                + f"\n\nIt is your turn, {speaker} risk analyst. Assess the "
                  "proposed trade from your perspective, citing the data above."
            )
            comments.append(DebateTurn(speaker=speaker, text=client.quick(system, user)))

    manager_user = (
        base
        + "\n\n"
        + _format_turns("RISK TEAM DISCUSSION", comments)
        + "\n\nIssue your final decision as JSON."
    )
    payload = _chat_json(client.deep, FUND_MANAGER_SYSTEM, manager_user)
    if payload is None:
        return RiskVerdict(
            approved=False,
            final_action="HOLD",
            final_size=0.0,
            rationale="Fund manager output was unparseable after retry; defaulting to HOLD.",
        )
    raw_approved = payload.get("approved", False)
    if isinstance(raw_approved, str):
        approved = raw_approved.strip().lower() in {"true", "yes", "1"}
    else:
        approved = bool(raw_approved)
    action, size = _clean_action(payload.get("final_action"), payload.get("final_size"))
    if not approved:
        action, size = "HOLD", 0.0
    return RiskVerdict(approved=approved, final_action=action, final_size=size,
                       rationale=str(payload.get("rationale", "")).strip())
