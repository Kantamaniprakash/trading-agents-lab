# Marketplace listing: trading-agents-desk

Copy for listing the skill on a marketplace such as Agensi. The upload itself
is `dist/trading-agents-desk.zip`, built by `python skill/build_skill.py`.

## Before you upload

1. **Merge this work into `main` first.** The skill installs the package from
   the `main` branch archive. The `snapshot` and `desk` commands it relies on
   do not exist on `main` until the merge, so a listing uploaded earlier would
   fail for every buyer.
2. **Check the install the way a buyer's agent will run it**, in a clean
   virtual environment:

   ```bash
   python -m pip install "https://github.com/Kantamaniprakash/trading-agents-lab/archive/refs/heads/main.zip"
   ```

   ```bash
   python -m tradinglab.cli snapshot --ticker AAPL
   ```

3. **Build the ZIP.**

   ```bash
   python skill/build_skill.py
   ```

4. **Optional, recommended once it has users:** tag a release and change the
   install URL in `SKILL.md` to `archive/refs/tags/<tag>.zip`. Buyers then get
   a fixed version instead of whatever is on `main` that day.

## Form fields

| Field | Value |
|---|---|
| Name | `trading-agents-desk` |
| Category | Trading & Investing |
| Version | 1.0.0 |
| Docs URL | https://github.com/Kantamaniprakash/trading-agents-lab |
| License | MIT |

**Summary (one line)**

A full trading desk in your agent: four analysts, a bull-vs-bear debate, a
trader and a risk team argue over one stock and return a sized BUY/SELL/HOLD
view with the reasoning.

**Description**

Ask your agent "analyze NVDA" and it runs the multi-agent desk from the
TradingAgents paper (arXiv:2412.20138) on a live market snapshot: daily
prices, technical indicators (RSI, MACD, Bollinger, ATR, ADX and more),
fundamentals and current headlines. A
market, fundamentals, news and sentiment analyst each report. A bull and a
bear debate for two rounds and a facilitator rules. A trader proposes a
position, three risk analysts challenge it and a fund manager approves or cuts
it. You get the decision, the strongest argument on each side, and what would
change the view.

No API keys or data subscriptions are needed for the default workflow. Market
data comes from Yahoo Finance and your own agent does the reasoning.

It also ships the research harness that most LLM-trading demos lack: a
backtest engine with no lookahead and transaction costs always on, five
rule-based baselines, a walk-forward LightGBM benchmark, an anonymization mode
that hides the ticker, dates and price levels from the model, and a
memorization probe that measures whether a model simply remembers the period
you are testing.

Honest by design. In the author's own leakage-controlled test the desk
returned -0.9% over a quarter in which buy-and-hold returned +15.7%. This is
a tool for structured second opinions and for studying LLM trading agents. It
is not a signal service and not investment advice, and it never places orders.

**Tags**

trading, stocks, investing, multi-agent, backtesting, technical-analysis,
market-data, research

**Keywords**

stock analysis, buy sell hold, trading agents, bull bear debate, backtest,
lookahead bias, LLM memorization, walk-forward, risk management, yfinance

**Use cases**

- Get a structured second opinion on a stock before acting on your own view.
- See the strongest bull and bear case for a ticker from the same evidence.
- Backtest an LLM trading agent with costs and without lookahead.
- Check whether a model remembers a historical window before trusting a
  backtest on it.
- Compare agent decisions against buy-and-hold, rule baselines and an ML
  model.

## Permissions to declare

| Permission | Why |
|---|---|
| Terminal | Runs `python -m pip install` once and `python -m tradinglab.cli ...`. |
| Network | Downloads the package from GitHub and public market data from Yahoo Finance. |
| File write | Price cache and results under `~/.tradinglab` only. |
| Environment variables | None for the default workflow. The optional API workflow lets the Anthropic SDK read `ANTHROPIC_API_KEY`; the skill never reads, prints or transmits it itself. |

The package ships no scripts of its own, so there is nothing for the
marketplace to execute from a temporary file. A reviewer may still flag the
install step. The answer is that it installs the listing author's own
MIT-licensed, publicly auditable package.

## Pricing

Start at **$5 one-time**. On Agensi you keep 70%, so $3.50 a sale, and $5 is
the lowest price a buyer can pay by card.

Starting paid is the reversible choice. Moving a listing from paid to free
is painless and past buyers keep access. Moving from free to paid forces
existing users to buy before they can continue. If there are no sales after a
month, drop it to free for reach.

Do not price it as a signal product. The repository is public and MIT
licensed, so the listing sells convenience and packaging, and the published
results do not support a claim of trading edge.

## Data source note

Market data comes from Yahoo Finance through the unofficial `yfinance`
library, which Yahoo intends for personal and research use. The skill
redistributes no data: each buyer's own machine fetches it. That is a
reasonable footing for a low-priced research tool. A higher-priced or
commercial offering should move to a licensed data source.
