# Marketplace listing: trading-agents-desk

Copy for listing the skill on a marketplace such as Agensi. The upload itself
is `dist/trading-agents-desk.zip`, built by `python skill/build_skill.py`.

## Before you upload

The ZIP holds instructions only. The code it runs is installed from this
repository at the release tag named in `SKILL.md`, so the tag has to exist
before anyone can use the listing.

1. **Merge this work into `main`.**
2. **Tag the release and push the tag.** The tag must match the version in
   `pyproject.toml`, which is what `SKILL.md` pins.

   ```bash
   git tag v0.2.0
   ```

   ```bash
   git push origin v0.2.0
   ```

3. **Build the ZIP.** The build checks that the pinned release can be
   downloaded and refuses to write the ZIP when it cannot.

   ```bash
   python skill/build_skill.py
   ```

4. **Install it the way a buyer's agent will**, to see it work end to end.

   ```bash
   python3 -m venv ~/.tradinglab/venv
   ```

   ```bash
   ~/.tradinglab/venv/bin/python -m pip install "https://github.com/Kantamaniprakash/trading-agents-lab/archive/refs/tags/v0.2.0.zip"
   ```

   ```bash
   ~/.tradinglab/venv/bin/tradinglab snapshot --ticker AAPL
   ```

5. **Ask the marketplace one question before relying on approval.** Agensi's
   documentation does not say whether a skill may tell the buyer's agent to
   install a package at run time. This skill does exactly that, so ask
   whether it is acceptable. Its scanner fails piping a download into a
   shell and warns on file writes and network calls; a pinned `pip install`
   is neither named as allowed nor as forbidden.

For every later release: bump the version, tag it, change the URL in
`SKILL.md` to the new tag, rebuild and re-upload. A test fails if the pinned
tag and the package version disagree.

## Form fields

| Field | Value |
|---|---|
| Name | `trading-agents-desk` |
| Category | Trading & Investing |
| Version | 1.0.0 |
| Docs URL | https://github.com/Kantamaniprakash/trading-agents-lab |
| License | MIT |

**Summary (one line)**

Research simulation of the TradingAgents multi-agent desk: four analysts, a
bull-vs-bear debate, a trader and a risk team argue over one stock and return
a sized BUY/SELL/HOLD view. Not investment advice.

**Description**

Ask your agent "analyze NVDA" and it runs a simulated trading desk on a
current market snapshot: daily prices, technical indicators (RSI, MACD,
Bollinger, ATR, ADX and more), fundamentals and headlines. A market,
fundamentals, news and sentiment analyst each report. A bull and a bear debate
for two rounds and a facilitator rules. A trader proposes a position, three
risk analysts challenge it and a fund manager approves or cuts it. You get the
desk's decision, the strongest argument on each side, and what would change
the view.

Honest by design. This workflow has not been backtested. The only test of the
desk design was a reduced, price-only version run through the Anthropic API on
one stock for one quarter, with the ticker and dates masked: it returned -0.9%
while buy-and-hold returned +15.7%. This is a tool for structured second
opinions and for studying LLM trading agents. It is not a signal service and
not investment advice. It ships no order-placing code and tells your agent not
to call brokerage tools.

The desk is an independent reimplementation of the design in the TradingAgents
paper (Xiao et al., arXiv:2412.20138). It is not the paper authors' code.

No API key or data subscription is needed for the default workflow. Market
data is Yahoo Finance daily bars fetched by your own machine, and your own
agent does the reasoning. It uses the unofficial yfinance library and is not
affiliated with or endorsed by Yahoo; your use of the data is subject to
Yahoo's terms. The snapshot says when the newest bar is an
unfinished session, when data is stale and when Yahoo returned gaps, and the
skill tells your agent what to do in each case.

It also ships the research harness that most LLM-trading demos lack: a
backtest engine with no lookahead and transaction costs always on, five
rule-based baselines, a walk-forward LightGBM benchmark, an anonymization mode
that hides the ticker, dates and price levels from the model, and a
memorization probe that measures whether a model simply remembers the period
you are testing. The backtests of the agent desk and the probe call the
Anthropic API and need your own key.

The same code is free and MIT licensed on GitHub. The listing sells the
packaging: one install, and instructions your agent can follow.

**Tags**

trading, stocks, investing, multi-agent, backtesting, technical-analysis,
market-data, research

**Keywords**

stock analysis, bull and bear case, trading agents, multi-agent debate,
backtest, lookahead bias, LLM memorization, walk-forward, risk review,
research simulation

**Use cases**

- See the strongest bull and bear case for a ticker argued from the same
  evidence.
- Get a structured second opinion on a stock, with the reasoning shown.
- Backtest an LLM trading agent with costs and without lookahead.
- Check whether a model remembers a historical window before trusting a
  backtest on it.
- Compare agent decisions against buy-and-hold, rule baselines and an ML
  model.

## Permissions to declare

| Permission | Why |
|---|---|
| Terminal | Creates a virtual environment, runs `pip install` once, and runs the `tradinglab` command. |
| Network | Downloads the package from GitHub (the archive URL redirects to codeload.github.com), its Python dependencies from PyPI, and public market data from Yahoo Finance. The optional API workflows call the Anthropic API. |
| File write | A dedicated virtual environment, price cache and results, all under `~/.tradinglab`. The standard per-user cache folders of pip, matplotlib and yfinance are also written. Nothing is written to the project directory. |
| Environment variables | None are required for the default workflow. `TRADINGLAB_HOME` optionally moves the data folder. The optional API workflows let the Anthropic SDK read `ANTHROPIC_API_KEY`; the skill never reads, prints or transmits it itself. `TRADINGLAB_QUICK_MODEL` and `TRADINGLAB_DEEP_MODEL` optionally change the models those workflows use. |

The ZIP ships no scripts, so there is nothing for the marketplace to execute
from a temporary file.

## Pricing

Start at **$5 one-time**. On Agensi you keep 70% of the net sale price
excluding VAT, at most $3.50 a sale, and $5 is the lowest price a buyer can
pay by card. Agensi's terms let it change that split with 30 days' notice.

Starting paid is the reversible choice. Moving a listing from paid to free
is painless and past buyers keep access. Moving from free to paid forces
existing users to buy before they can continue. Paid listings also get a
ranking boost in the marketplace's search. If there are no sales after a
month, drop it to free for reach.

Do not price or describe it as a signal product. The repository is public and
MIT licensed, so the listing sells convenience and packaging, and the
published results do not support a claim of trading edge. Every paid skill
carries a 30-day money-back guarantee for a skill that "does not do what its
page promises", which is one more reason to promise only what it does.

Two things the marketplace does that are worth knowing. It may attach its own
paid tools to a skill whose description names a model or service, and creators
get no share of those tool charges. And buyers of a paid listing receive a
licence that forbids copying it, while the public repository stays MIT: say
so if a buyer asks.

## Data source note

Market data comes from Yahoo Finance through `yfinance`, an unofficial
open-source library that is not affiliated with, endorsed or vetted by Yahoo.
Its own documentation says it is intended for research and educational
purposes, refers users to Yahoo's terms of use, and says Yahoo's finance API
is intended for personal use only.

The skill redistributes no data: each buyer's own machine fetches it for that
buyer's own use. Even so, a paid listing is a commercial product at any
price, and those terms do not clearly allow one to be built on this data.
This is the weakest point of a paid listing. Read Yahoo's terms, or move to a
licensed data source, before taking payment. A free listing carries less of
this risk.
