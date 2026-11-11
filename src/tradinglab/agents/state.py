"""Structured communication protocol between the trading agents.

Plain dataclasses carry state between pipeline stages (analysts -> debate ->
trader -> risk) and render human-readable transcripts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class MarketSnapshot:
    """Everything the agents are allowed to see for one ticker on one date."""

    ticker: str
    date: str                    # "YYYY-MM-DD"
    price_table: str             # last 10 rows OHLCV as fixed-width text
    indicator_report: str        # current values of key indicators
    returns_summary: str         # 1d/5d/21d/63d returns, vol, 52w-high distance
    fundamentals: str | None = None
    news: list[str] | None = None
    anonymize: bool = False
    # Facts about the data itself that change how it must be read (a partial
    # session, a stale bar, incomplete fundamentals). Backtests never set it,
    # so their prompts are unchanged.
    data_notes: list[str] | None = None

    def context_block(self) -> str:
        """Render the full snapshot as one text block for agent prompts.

        When ``anonymize`` is set, the header uses masked identifiers
        ("TICKER-X" / "Day T"); the tables themselves are already masked by
        the snapshot builder in that case. As a final defense-in-depth pass,
        the externally sourced text (fundamentals and news, which arrive
        pre-rendered) is scrubbed of the real ticker, and the whole block of
        ISO dates. The ticker scrub is deliberately limited to that text and
        is case-sensitive: applied to everything, a ticker that is also a
        word or a label (A, T, LOW, KEY, CCI) would rewrite the tables.
        """
        ticker = "TICKER-X" if self.anonymize else self.ticker
        date = "Day T" if self.anonymize else self.date
        parts = [f"=== Market snapshot: {ticker} | analysis date: {date} ==="]
        if self.data_notes:
            parts += ["", "--- Data notes (read before the tables) ---"]
            parts += [f"- {note}" for note in self.data_notes]
        parts += [
            "",
            "--- Recent price history (OHLCV) ---",
            self.price_table,
            "",
            "--- Technical indicators ---",
            self.indicator_report,
            "",
            "--- Returns summary ---",
            self.returns_summary,
        ]
        fundamentals = self.fundamentals
        news = self.news
        if self.anonymize and self.ticker:
            # The ticker as typed and in upper case, so "aapl" still masks "AAPL".
            forms = sorted({self.ticker, self.ticker.upper()}, key=len, reverse=True)
            pattern = re.compile(r"\b(?:" + "|".join(map(re.escape, forms)) + r")\b")
            if fundamentals:
                fundamentals = pattern.sub("TICKER-X", fundamentals)
            if news:
                news = [pattern.sub("TICKER-X", headline) for headline in news]
        if fundamentals:
            parts += ["", "--- Fundamentals ---", fundamentals]
        if news:
            parts += ["", "--- Recent news headlines ---"]
            parts += [f"- {headline}" for headline in news]
        block = "\n".join(parts)
        if self.anonymize:
            block = re.sub(r"\d{4}-\d{2}-\d{2}", "[DATE]", block)
        return block


@dataclass
class AnalystReport:
    """One analyst's written assessment."""

    role: str
    summary: str


@dataclass
class DebateTurn:
    """A single statement in a debate transcript."""

    speaker: str
    text: str


@dataclass
class DebateResult:
    """Full bull/bear debate transcript plus the facilitator's ruling."""

    transcript: list[DebateTurn]
    verdict: str                 # BULLISH | BEARISH | NEUTRAL
    rationale: str


@dataclass
class TraderDecision:
    """The trader's proposed action."""

    action: str                  # BUY | SELL | HOLD
    size: float                  # position size in [0, 1]
    rationale: str


@dataclass
class RiskVerdict:
    """The risk team / fund manager's final ruling on the trader's proposal."""

    approved: bool
    final_action: str
    final_size: float
    rationale: str


@dataclass
class AgentDayLog:
    """Complete record of one decision day for one ticker."""

    ticker: str
    date: str
    reports: list[AnalystReport] = field(default_factory=list)
    debate: DebateResult = None  # type: ignore[assignment]
    decision: TraderDecision = None  # type: ignore[assignment]
    risk: RiskVerdict = None  # type: ignore[assignment]
    final_action: str = "HOLD"
    final_size: float = 0.0

    def to_markdown(self) -> str:
        """Render the full decision transcript as readable markdown."""
        lines = [
            f"# Trading agents transcript — {self.ticker} — {self.date}",
            "",
            f"**Final decision: {self.final_action}** (size {self.final_size:.2f})",
            "",
            "## Analyst team",
        ]
        if self.reports:
            for report in self.reports:
                lines += ["", f"### {report.role}", "", report.summary]
        else:
            lines += ["", "_No analyst reports._"]

        lines += ["", "## Research debate"]
        if self.debate is not None:
            for turn in self.debate.transcript:
                lines += ["", f"**{turn.speaker}:**", "", turn.text]
            lines += [
                "",
                f"**Facilitator verdict:** {self.debate.verdict}",
                "",
                f"**Rationale:** {self.debate.rationale}",
            ]
        else:
            lines += ["", "_No debate recorded._"]

        lines += ["", "## Trader decision"]
        if self.decision is not None:
            lines += [
                "",
                f"- **Action:** {self.decision.action}",
                f"- **Size:** {self.decision.size:.2f}",
                f"- **Rationale:** {self.decision.rationale}",
            ]
        else:
            lines += ["", "_No trader decision recorded._"]

        lines += ["", "## Risk management"]
        if self.risk is not None:
            lines += [
                "",
                f"- **Approved:** {'yes' if self.risk.approved else 'no'}",
                f"- **Final action:** {self.risk.final_action}",
                f"- **Final size:** {self.risk.final_size:.2f}",
                f"- **Rationale:** {self.risk.rationale}",
            ]
        else:
            lines += ["", "_No risk verdict recorded._"]

        lines += [
            "",
            "## Final",
            "",
            f"**{self.final_action}** at size {self.final_size:.2f}",
            "",
        ]
        return "\n".join(lines)
