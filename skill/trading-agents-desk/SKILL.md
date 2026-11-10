---
name: trading-agents-desk
description: Runs a multi-agent trading-desk analysis on one stock (analyst team, bull-vs-bear debate, trader, risk team, fund manager) from a live market snapshot and returns a sized BUY/SELL/HOLD view with the full reasoning. Use when the user asks to analyze a ticker, wants a second opinion on a trade, asks whether to buy, sell or hold a stock, or wants to backtest LLM trading agents without lookahead or memorization leakage.
license: MIT
---

# Trading agents desk

A structured second opinion on a single stock. You fetch a market snapshot
(prices, technical indicators, fundamentals, headlines) with one command, then
run it through the desk from the TradingAgents paper (arXiv:2412.20138): four
analysts, a bull-vs-bear debate, a trader, a three-way risk review and a fund
manager who signs off.

It is built on the open-source `trading-agents-lab` package, which also ships
a backtest harness with transaction costs and leakage controls.

## Say this before the first analysis

This is a research aid, not investment advice and not a signal service. The
package's own leakage-controlled backtest is the honest benchmark: over one
quarter on one ticker the desk returned -0.9% against +15.7% for buy-and-hold.
The value is the structured reasoning and the explicit counter-arguments, not
a proven edge. Say so in one sentence the first time you use this skill in a
conversation.

This skill never trades. Do not call a brokerage or order tool as part of it,
even when one is connected.

## Setup (once per environment)

Requires Python 3.10+ and network access to GitHub and Yahoo Finance. Install
into the active virtual environment; create one first if there is none.

```bash
python -m pip install "https://github.com/Kantamaniprakash/trading-agents-lab/archive/refs/heads/main.zip"
```

```bash
python -m tradinglab.cli snapshot --help
```

If the second command lists `--ticker`, setup is done. Caches and results are
written to `~/.tradinglab` (set `TRADINGLAB_HOME` to move them). The default
workflow needs no API key.

## Workflow 1: desk analysis (default)

Use this for "analyze NVDA", "second opinion on my TSLA position", "buy, sell
or hold AAPL?".

1. **Fetch the snapshot.**

   ```bash
   python -m tradinglab.cli snapshot --ticker NVDA --json
   ```

   `context` is the evidence block. `notes` lists data problems. `as_of` and
   `last_close` go in your report.

2. **Act on the notes before analysing.**
   - Partial session: carry on, and tell the user the last bar is intraday.
   - Stale bar or an error: stop and report it. Do not analyse old data as
     if it were current.
   - No fundamentals: skip the fundamentals analyst.
   - No headlines: skip the news and sentiment analysts. If you have a web
     search tool you may instead collect up to 8 recent headlines (date,
     publisher, title) and use them as the news block; say in the report that
     the headlines came from your own search.

3. **Run the desk in order.** The role briefs, the inputs each role is
   allowed to see and the required output formats are in
   `references/roles.md`. Read it before the first run. The order is fixed:
   analysts, then two bull/bear rounds and a facilitator verdict, then the
   trader, then one risk round and the fund manager.

4. **Keep the roles independent.** If you can spawn subagents, give Bull,
   Bear and each risk analyst their own subagent with only the inputs listed
   for that role, so no side reads the other's private reasoning. Otherwise
   write each turn in sequence and argue each side in good faith.

5. **Report** with the template below.

The evidence base is the snapshot. Every number you cite must appear in it.
Do not add prices, events or forecasts from memory: a debate that quietly
imports outside facts cannot be checked by the user.

## Workflow 2: reproducible API run (optional)

The package can run the same desk through the Anthropic API, with a fixed
model per role, a saved transcript and a disk cache that makes re-runs free
and deterministic.

```bash
python -m tradinglab.cli desk --ticker NVDA --json
```

This needs `ANTHROPIC_API_KEY` and spends about $0.20 to $0.50 of the user's
API credit per run, so ask before the first run. The JSON carries
`final_action`, `final_size`, every analyst report, the debate, token usage
and `transcript_path`. Use it when the user wants an auditable record, or
wants the result independent of the agent they are talking to.

## Workflow 3: backtests and leakage checks

For "does this actually work?", "backtest the agents on MSFT", or "is this
window contaminated?", read `references/research-harness.md`.

One rule matters here. Do not run Workflow 1 on a past date to see how the
desk "would have done". You may remember what happened next, and so the
result measures memory, not skill. Historical evaluation must go through
`agents --anonymize` and `probe`, which exist for exactly this problem.

## Reading a decision

- **BUY s**: hold a long position worth fraction `s` of the capital set
  aside for this name.
- **SELL s**: hold a short of size `s`. For a long-only user, read it as
  "exit or avoid".
- **HOLD**: keep whatever stance was already in place.
- **Rejected by the fund manager**: flat, HOLD at size 0.
- Sizes scale with conviction and are usually small. The reference run never
  exceeded 0.35.
- The horizon is the next few trading days, not a long-term rating.

## Report template

```markdown
## <TICKER> desk view, as of <as_of> (close <last_close>)

**Decision: <BUY|SELL|HOLD> <size>** (<what that exposure means in plain words>)
**Research verdict:** <BULLISH|BEARISH|NEUTRAL>. <One or two sentences from the facilitator.>

### Why
- <Strongest bull evidence, with the numbers from the snapshot.>
- <Strongest bear evidence, with the numbers from the snapshot.>
- <What the risk team changed, and why the fund manager approved or cut it.>

### What would change this view
- <Two or three concrete, observable conditions.>

### Data notes
- <Anything from `notes`, and which analysts were skipped.>

Research output, not investment advice. In this tool's own leakage-controlled
backtest the agent desk did not beat buy-and-hold.
```

Keep the full analyst reports and debate out of the reply unless the user
asks for them. Offer them instead.

## Guardrails

- Never state or imply that the output is advice, a recommendation to trade,
  or a prediction with a known hit rate.
- Never invent a number. If the snapshot lacks it, say it is missing.
- Never place, stage or size a real order from this output.
- If the user asks for many tickers, run them one at a time and say that the
  desk has no portfolio-level view: the decisions are independent.
