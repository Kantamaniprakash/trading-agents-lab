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
a backtest harness with transaction costs and leakage controls. In that
harness's own test the desk did not beat buy-and-hold, which is why the rules
below are strict about what this output is.

## Ground rules

1. **Disclose once.** Put this sentence above your first report in a
   conversation, and do not repeat it after that:

   > This is a research aid, not investment advice: in the tool's own
   > leakage-controlled backtest (one ticker, one quarter) the agent desk
   > returned -0.9% while buy-and-hold returned +15.7%, so read what follows
   > as structured reasoning, not a proven edge.

2. **This skill never trades.** Do not call a brokerage, order or account
   tool as part of it, even when one is connected.

3. **The snapshot is the only market data.** Do not take quotes,
   fundamentals or news from other tools, or prices and events from memory.
   The single exception is the headline search in step 2 below.

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

Yahoo prints warnings such as "Crumb fetch rate-limited" on stderr. They are
not errors. Judge a run by its exit code and its JSON, not by stderr.

## Workflow 1: desk analysis (default)

Use this for "analyze NVDA", "second opinion on my TSLA position", "buy, sell
or hold AAPL?".

### Step 1: fetch the snapshot

```bash
python -m tradinglab.cli snapshot --ticker NVDA --json
```

A non-zero exit code is an error: report its message and stop. Otherwise the
JSON has these keys.

| Key | What to do with it |
|---|---|
| `context` | The evidence. It is everything the desk may cite. When the data has a problem it opens with a "Data notes" section that the roles must respect. |
| `as_of`, `fetched_at`, `last_close` | Report header. `fetched_at` is UTC. |
| `partial_session` | `true` means the last bar is an unfinished trading day, so `last_close` is the latest price, not a close. |
| `stale` | `true` means the newest bar is days old. |
| `has_fundamentals`, `fundamentals_missing` | Whether there is a fundamentals block, and which of its figures are absent. |
| `news_headlines` | How many headlines the snapshot holds. |
| `notes` | Every data problem in plain words. Read all of them. |
| `disclaimer` | The last line of your report, word for word. |

### Step 2: check the data

- **`stale` is true:** stop and tell the user. Do not analyse old data as if
  it were current.
- **`has_fundamentals` is false, `fundamentals_missing` is not empty, or
  `news_headlines` is 0:** Yahoo may have rate-limited the request. Wait 30
  seconds and fetch once more. Keep whichever result has fewer gaps. Do not
  retry a second time. Funds and some foreign listings never have these
  figures, and that is fine.
- **Still no fundamentals block:** skip the fundamentals analyst.
- **Still no headlines:** skip the news and sentiment analysts. If you have a
  web search tool you may instead collect up to 8 recent headlines (date,
  publisher, title) and use them as the news block. Say in the report that
  the headlines came from your own search.
- **`partial_session` is true:** carry on. Write "last price", not "close",
  and tell the user that every indicator will move by the close.
- **The user's position:** if the user said what they hold in this stock,
  give that to the trader as the existing stance. If they did not, assume no
  position and say so in the report.

### Step 3: run the desk

Read `references/roles.md` before the first run. It holds the brief for each
role, the inputs each role may see, the output formats and the indicator
conventions. The order is fixed: analysts, two bull/bear rounds and a
facilitator verdict, the trader, one risk round, the fund manager.

Keep the roles independent. If you can spawn subagents, give Bull, Bear and
each risk analyst their own subagent with only the inputs listed for that
role. Otherwise write the turns one at a time, and in each turn use only that
role's listed inputs. A single context cannot fully un-see earlier turns, so
say in the report that the seats were not isolated.

Keep every analyst report, debate turn and risk comment in your working notes
or a scratch file. The report below leaves them out, and the user may ask.

**Evidence rule.** Every market figure you cite (a price, an indicator, a
ratio, a return, a date) must appear in `context` or be simple arithmetic on
figures that do. Position sizes are the desk's own judgment. A debate that
quietly imports outside facts cannot be checked by the user.

### Step 4: report

Use this template. Offer the full analyst reports and debate; do not paste
them unless asked.

```markdown
## <TICKER> desk view, as of <as_of> (<"close" or "last price"> <last_close>, fetched <fetched_at>)

**Decision: <BUY size | SELL size | HOLD>** (<what that means in plain words>)
**Research verdict:** <BULLISH|BEARISH|NEUTRAL>. <One or two sentences from the facilitator.>
**Trader vs verdict:** <Only when the action departs from the verdict: one sentence saying why.>

### Why
- **Bull:** <Strongest bull evidence, with figures from the snapshot.>
- **Bear:** <Strongest bear evidence, with figures from the snapshot.>
- **Risk review:** <What the risk analysts argued, and what the fund manager did to the trader's proposal.>

### What would change this view
- <Two or three concrete, observable conditions.>

### Data notes
- <Each problem from `notes`, any analysts skipped, where headlines came from, the position assumed, whether the seats were isolated.>

<disclaimer>
```

## Reading a decision

- **BUY s**: hold a long position worth fraction `s` of the capital set
  aside for this name.
- **SELL s**: hold a short of size `s`. For a long-only user, read it as
  "exit or avoid".
- **HOLD**: keep the existing stance. With no position, that means stay out.
  A HOLD has no size.
- **Rejected by the fund manager**: flat, reported as HOLD.
- Sizes scale with conviction and are usually small. The reference run never
  exceeded 0.35.
- The horizon is the next few trading days, not a long-term rating.
- The decision is a judgment, not a calculation. A second run on the same
  snapshot can give a different size, and near a NEUTRAL verdict a different
  action. Tell the user this if they treat the size as precise. Workflow 2
  gives a repeatable record.

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

For "does this actually work?", "backtest the agents on MSFT", "how would the
desk have done last March?" or "is this window contaminated?", read
`references/research-harness.md`.

One rule matters here. Do not run Workflow 1 on a past date to see how the
desk "would have done". You may remember what happened next, and so the
result measures memory, not skill. Historical evaluation must go through
`agents --anonymize` and `probe`, which exist for exactly this problem.

## Guardrails

- Never state or imply that the output is advice, a recommendation to trade,
  or a prediction with a known hit rate.
- Never invent a figure. If the snapshot lacks it, say it is missing.
- Never place, stage or size a real order from this output.
- If the user asks for many tickers, run them one at a time and say that the
  desk has no portfolio-level view: the decisions are independent.
