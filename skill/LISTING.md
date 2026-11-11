# Marketplace listing: trading-agents-desk 2.0.0

Copy for listing the skill on a marketplace such as Agensi. The upload is
`dist/trading-agents-desk.zip`, written by `python skill/build_skill.py`.
The design behind this copy is in [docs/skill-v2/DESIGN.md](../docs/skill-v2/DESIGN.md).

## Before you upload

Version 2 installs nothing when it runs, so there is no release tag to create first. The ZIP
holds the instructions and four Python scripts, and that is all a buyer's agent uses.

1. Check the package without changing anything. It fails when a script was edited after its
   integrity stamp was written, when a known answer is not what the script computes, when
   the lint finds something, or when the scripts' own preflight fails.

   ```sh
   python skill/build_skill.py --check
   ```

2. Build. This stamps the scripts if they changed (their hashes and their known answers),
   runs the same checks and writes the ZIP. If it reports "stamped", commit the stamped
   scripts before you upload, so the repository and the ZIP hold the same bytes.

   ```sh
   python skill/build_skill.py
   ```

3. Run the skill from the ZIP once on every agent host you want to name in the table under
   "What it needs", and change the table to what you saw. A host nobody has run it on is not
   named as tested.
4. Upload the ZIP and paste the fields below. Add screenshots only from real runs. Where the
   marketplace asks which of its tools the skill may use, name
   `yahoo-finance:get_historical_data` and no other.

For a later release: change the scripts or the text, run step 2, commit, upload. The marketplace
reloads the skill for every buyer.

## Form fields

| Field | Value |
|---|---|
| Name | Trading Agents Desk: Bull vs Bear, Every Figure From the Data |
| Skill id | `trading-agents-desk` |
| Category | Trading & Investing |
| Version | 2.0.0 |
| Price | $5, one-time |
| License | MIT |
| Docs URL | https://github.com/Kantamaniprakash/trading-agents-lab |

**Summary (one line)**

A bull and a bear argue one ticker from the same dated market data, and a script, not the
agent, puts every figure on the report. Research simulation, not investment advice. The desk
design lost to buy-and-hold in its only test.

**Tags**

trading, stocks, investing, multi-agent, technical-analysis, market-data, research

**Keywords**

stock analysis, bull and bear case, second opinion, trading agents, multi-agent debate,
risk review, checked figures, research simulation

## Page copy

### The problem

When an agent writes a stock analysis, it types the figures itself. A figure on the page may
have been computed from fresh data, remembered from training, lifted from a headline, rounded
the wrong way or given the wrong sign. The page looks the same in every case, and you cannot
tell which figures to trust without redoing the work.

This skill takes the typing away. The agent never types a market number in digits. It names a
fact, and a script prints the value from the data of that run.

### What happens, in order

1. **A script downloads the data.** Daily prices for the past year with the exchange's session
   times, recent headlines and, for a company, a few statement figures, from one host,
   `query1.finance.yahoo.com`. No key, no account, no cookies. When the source answers a
   request with a rate limit, the script waits seven seconds and sends that request once more.
2. **A second script checks the data and computes the figures.** It stops with a fixed sentence
   when the data cannot be used: too few rows, a break of more than 12 days in the dates,
   impossible prices, a download more than 12 days old, an instrument type the desk does not
   cover. It computes every indicator and every other figure the desk may cite, each under a
   fixed name, and prints them as a fact sheet. Anything unusual about the data becomes a flag
   and a note: an unfinished trading day, a newest close filled from the exchange's last
   regular price, rows left out, a shorter break in the dates such as a long holiday,
   statement figures or headlines missing or their request failed, a short history. When the
   source has not yet published a finished session, the view ends at the last complete day,
   and the first line of the report says that the source is behind.
3. **Your agent runs the desk from that fact sheet.** Twelve roles in a fixed order: four
   analysts, a bull and a bear researcher with a facilitator, a trader, three risk analysts
   and a fund manager who can approve the trade, shrink it or reject it.
4. **Your agent writes a draft that names facts.** It writes `{{rsi14}}`, not a number. Evidence
   lists are lists of fact names.
5. **A third script writes the report.** It sends the draft back if it holds a typed digit, an
   unknown fact name, a reference that stands straight after another indicator's name (as in
   "MFI {{rsi14}}"), or advice wording, and says what to fix. A figure that stands further
   from the wrong name is not caught. Your agent gets two more attempts. Then the script
   inserts each figure, applies the desk's decision rules, prints the report and adds the
   receipt.
6. **A fourth script can draw the report as a page.** Your agent runs it when its host can
   show or save an HTML file, or when you ask for the page. The report in the chat is the
   answer either way.

### The test this desk lost

The desk design has one test on record, and it lost. A reduced, price-only version, run
through a model API on one stock for one quarter with the ticker and dates masked, returned
-0.9% while buy-and-hold returned +15.7%. The skill as packaged here has not been backtested
at all, and it cannot be: an agent that knows the ticker and the date may remember what
happened next.

That result is printed on every report. This skill sells two-sided reasoning you can check. It
does not sell a prediction, a signal or an edge.

### What you get

For one ticker, in the chat:

- The header: symbol, name, the date of the newest price and when the data was downloaded.
  A price file was not downloaded, so for one the header says "prices from a file".
- Which side won the argument, and the paper desk's decision: BUY, SELL or HOLD with a size as
  a fraction of a paper book. The desk starts from no position and places no orders.
- Why, in up to three sentences.
- Bull against bear, side by side: core claim, best evidence, one more point, and the weak
  point of each case.
- One to three price levels that would change the view, each with the move from the newest
  price that would reach it.
- The risk review.
- Data notes: every flag and note from the data check, which roles were skipped, and how the
  roles were run.
- The headlines the desk saw, marked as third-party text.
- The receipt, the test record above and the disclaimer.

The same report can be drawn as one HTML page. From top to bottom the page has:

- A dark header band with four tiles: which side won the argument, the paper desk's
  decision, the last price with its one-day change, and how many figures the script
  inserted. The line "Research simulation, not advice. This desk design lost to
  buy-and-hold in its only test." stands at the top of the page, under the first line of
  the header.
- The "why" sentences in large type.
- A notice strip with the data notes, when the data check raised a flag.
- A price chart of the newest 120 daily prices with the 50-day and 200-day averages, and
  the report's levels as dashed lines.
- Meters for five indicators (RSI, stochastic %K, MFI, Bollinger %B and ADX), each on its
  real scale with the usual thresholds marked, and the returns over 1, 5, 21 and 63 trading
  days.
- The bull case and the bear case as two equal cards: claim, evidence, one more point and
  the weak point.
- The levels on a price ladder with the last price, and as a table that says how far the
  price is from each, beside the risk review.
- Statement figures as tiles, each with its own date and, for a money figure, its own
  currency, beside the headlines the desk saw.
- The receipts: the test record made prominent, the three receipt lines, the data notes and
  the disclaimer.

A BUY or a SELL is shown in the same plain style as a HOLD. There is no score, dial or star
rating. The file is plain: its styles and drawings are inside it, with no JavaScript and no
external requests. On a narrow screen the cards stack in one column.

### What it needs

- An agent that can run commands and write files.
- Python 3.9 or newer where those commands run. Nothing is installed: the scripts use the
  standard library only. The table below says which versions and systems have been run.
- Either network access from the command tool to `query1.finance.yahoo.com`, or a price file
  you supply (date, open, high, low, close, volume; 60 daily rows or more). With a price file
  there are no headlines and no statement figures, and the report says so.
- A saved result of the marketplace's price-history tool also works as a price file, except
  for listings in Australia and New Zealand. That result carries no exchange dates, and for
  an exchange that opens late in the UTC day the script stops instead of guessing them. This
  route was tried once, on 2026-11-11, with a real saved result for one US share.
- For the page: a host that can show or save an HTML file. Without one you still get the
  whole report in the chat.

What has been tried so far, and what has not:

| What | Status |
|---|---|
| The four scripts: automated tests on generated data, the full suite, on Linux with Python 3.10, 3.11, 3.12 and 3.13, for the build before the last round of fixes | tested on 2026-11-11. The suite is run again on the final build |
| The four scripts in CI on GitHub, for the pull request's commit before the two rounds of fixes: the preflight and the standard-library flow on Linux, macOS and Windows with Python 3.9, 3.13, 3.14 and 3.15 (12 legs; the Linux 3.9 leg runs on Ubuntu 24.04) | tested: all 12 legs passed. It is run again on the final commit |
| Live downloads from one Linux cloud machine on 2026-11-11, 18 symbols, with the build before the two rounds of fixes | tested once: 12 of the 18 were answered (11 with data, 1 with "no data for this symbol"). The source's rate limit turned the other 6 away within 20 minutes. That is why version 2 now waits once, asks once more and names the limit plainly |
| The marketplace's price-history tool: a real saved result for one US share, read by `brief.py` | tested once on 2026-11-11 |
| An agent following the skill from start to finish | see the release notes |
| The page as a buyer's host shows it | not tested yet. It was looked at in one headless browser only |

Rows change to "tested" only after a real run on that host.

### Limits

- Research simulation. Not investment advice, not a signal service. It never trades and tells
  your agent not to call brokerage tools.
- It takes no personal inputs: not your holdings, account size, goals or risk tolerance.
- One ticker per run, meant for current data. A download whose newest price is more than 12
  days old stops. A price file is taken as it is: the script cannot know how old a file is.
  The header then says "prices from a file", and the agent is told to say so above the report
  when the newest date is more than a few days old. The agent is also told never to run the
  desk on a past date to see how it would have done. No script can hold it to that.
- Daily prices for one year. Shares, funds, indexes and crypto pairs. No options, futures,
  currency pairs or intraday data.
- The data source is unofficial and can refuse a network or change its answers. Then the run
  stops with a sentence that says so. It does not switch to another source by itself.
- The data source limits requests from shared cloud networks. The script waits once and asks
  once more. If the limit on the price request still holds, the run stops with a sentence
  that says so, and a price file or a later run are the ways forward.
- When only the headline request or the statement request fails, the report says that the
  request failed, and the desk runs without the roles that need them.
- On a host that loads the skill through the marketplace, the agent may have to write the
  four scripts out, about 72 KB of text, before the first command. That costs time and
  tokens, and a copy with a wrong character is refused by the preflight.
- An AI host may decline to run the roles for some requests. The skill then tells the agent
  to say that no report was made.
- In an unfinished trading day, which for a crypto pair is every current day, the newest row
  is not final. The script widens that row's range to hold its last price, so a last price
  above the day's published high does not stop a run. Every average, gap, return and
  indicator on the sheet includes that row. The report says that the session was unfinished.
- The source's quotes can be delayed. For half an hour after the closing bell the report
  says "unfinished" while the source's quote is older than the bell.
- A thinly traded share can still stop the run. When the source's rows put the open or the
  close of a finished day outside that day's range, the script stops with a sentence that
  names the date. There is no report for that ticker from the download, and the agent
  offers to run on a price file that you supply.
- A fund, an index or a crypto pair is not asked for statement figures: its report says
  that they are absent, and the desk runs without the fundamentals analyst.
- A saved price history can carry a wrong date that the script does not catch: one with a
  single row stamped late in the UTC day, or with time stamps written as text.
- One row with a missing value gives one note each, so a history with many such rows gives a
  long list of notes in the report and on the page.
- A headline is shown inside double quotes, and a double quote in it is shown as a single
  one. A headline can still end its own quotation with guillemets or with two apostrophes.
- The decision is a judgment by a language model. A second run on the same data can give
  another size and, near a neutral verdict, another action.
- Twelve roles take many model turns, and the token use that goes with them.
- The report is in English.
- The page is extra. It is drawn only where the host can show or save a file, and it is not
  what the receipt covers. The page script trusts the fixed texts inside the report file that
  the report script saved: it does not check that the test record and the disclaimer in that
  file are unchanged. On the page the 200-day line starts where a year of data allows: for a
  share that is over the newest 50 or so of the chart's 120 days. And level labels in the
  chart can overlap when three levels are given and two of them are almost at one price.
- Not affiliated with or endorsed by Yahoo. Your use of the data is subject to Yahoo's terms.

### What the receipt guarantees, and what it does not

Every report ends with three generated lines: how many figures the script inserted, which data
they came from (an id, the date of the newest price and whether that trading day was finished),
an id for the report, the attempt number and the script version.

What the receipt guarantees:

- Every figure in the desk's part of the report is a figure from the run's data (the download,
  or the price file you supplied), with the sign, the percent mark and, for statement money,
  the currency the script gives it, inserted by code. The agent typed none of them: a draft
  with a typed digit is refused.
- In evidence lists and level rows, each figure stands under its own label, printed by the
  script.
- A script copy that was damaged in copying stops the run. Three of the four scripts check
  their own text every time they run, and a preflight checks every script that the run
  uses.
- A saved report can be checked again later against its data: the script recounts the figures,
  recomputes the report id and compares the data id.

What it does not guarantee:

- That the sentence around a figure is right, or that it names the right fact. "The trend is
  strong" next to a real figure is still the agent's sentence.
- That a statement figure is recent. Each one is printed with its date where the script
  prints its label. A figure the agent cites without its label is printed without the date.
- That the desk chose the facts that matter, or that its view is any good.
- Quantity words such as "half" and "double", and number words such as "thirty" and
  "forties". They are counted on the receipt and not checked. "One" and fractions in words
  ("a fifth") are not counted at all.
- A currency word or sign the agent writes beside a figure. The header names the unit of the
  prices, such as pence, and a statement money figure carries its own currency code. The
  script does not check a typed "dollars".
- That the source's data is right. The script checks for gaps, stale data and impossible
  prices. It cannot know that a price the source sent is wrong.
- That your agent showed you the printed report unchanged. The skill tells it to. The script
  cannot control what the agent displays.
- The page. The check on a saved report reads the report text, not the page. The page is
  drawn by its own script from the same data and the same resolved report, and the tests in
  the repository compare its texts and what it draws (the lines, the axis, the levels) with
  the data. A run does not check it again.
- A signature. The check on a saved report catches an edit, unless the person who edited it
  also recomputed the report id. The scripts' self-check catches a copying error. It is not
  protection against a script that someone changed on purpose.

### Questions

**Isn't this what my agent already does?**

Your agent can already write a bull case and a bear case. Left alone, it also types every
figure in them, and you get no way to tell a computed figure from a remembered one. With this
skill the figures come from a script that downloaded the data during the run, a draft with a
typed digit is sent back, the risk review can shrink a trade and never enlarge it, and every
report states that this desk design lost its only test. If all you want is an opinion, you do
not need this skill.

**Does it predict prices?**

No. Its one test is on this page, and it lost. Read the output as a structured second opinion.

**Does it need an API key or a subscription?**

No. Your agent's own model does the reasoning and your machine downloads the data. If the
command tool has no internet or the source turns the download away, one option is the
marketplace's own price-history tool, on a host that saves a tool's result as a file. It has
a small per-use charge, about $0.07, that the marketplace shows you for approval first.

**What does it put on my machine?**

Nothing is installed. Each run uses one new temporary folder that holds the scripts, the
downloaded data, the fact sheet, the draft, the report and, when it is drawn, the page. Your
agent asks whether to keep the folder and removes it when you do not want it.

**Do I always get the page?**

No. The report in the chat is the answer. Your agent draws the page when its host can show or
save an HTML file, or when you ask for it, and tells you where the file is.

**What if the data source refuses?**

The run stops and shows a fixed sentence that says the fault is at the source and not with the
ticker, and which HTTP status the source gave. After a refusal or a rate limit your choices
are a price file that you supply, or the marketplace's price-history tool. After a rate limit
the script has already waited once and asked once more, and a new run a few minutes later may
work. Allowing the host for your command tool helps only when that tool has no internet at all.

**Does it look at my portfolio or place trades?**

No. It asks for no holdings, and its instructions forbid brokerage, order, account, watchlist
and alert tools, including to read a quote.

**Can I check a report later?**

Yes, while you still have the run's folder: `render.py --verify` takes the brief file and the
saved report and prints `VERIFY ok` or the differences.

**Is the code available elsewhere?**

Yes, under the MIT licence. The public repository holds the research lab the desk design
comes from. Version 2 of the skill, these scripts and instructions, is there on a pull request
branch until that request is merged, and on the main branch from then on. The listing sells
the packaging.

**Why twelve roles?**

It is the role set of the lab's pipeline, which follows the TradingAgents paper (Xiao et al.,
arXiv:2412.20138). This is an independent reimplementation, not the paper authors' code.
Fewer roles may come later, decided by experiment.

## Permissions to declare

| Permission | Why |
|---|---|
| Terminal | Runs Python: a version check, a one-line command that makes one temporary folder, the four scripts, and a one-line command that removes the folder only if its name starts with `desk-` and it holds `brief.py`. Where the host can, a command also copies the scripts into the folder. |
| Network | One host, `query1.finance.yahoo.com`, from `get_data.py` only. Its User-Agent string holds the repository's address, which is never contacted. The other three scripts cannot import a network module, and the build checks that. |
| File write | One new temporary folder per run. Nothing is written to the project folder. |
| Environment variables | None are read by the scripts. Python's own network library follows the system's proxy settings when `get_data.py` runs. |
| Marketplace tool | `yahoo-finance:get_historical_data`, the marketplace's price-history tool. Only when the command tool has no internet or the source refuses, and only when the user chooses it. The marketplace shows the price, about $0.07, for approval first. |

The ZIP ships four scripts, written as plain readable Python. The instructions tell the agent
to copy them unchanged, and a copy that was changed on the way stops with an integrity error.
That check catches a copying error. It is not protection against a change made on purpose.

## Pricing

Start at **$5, one-time**. Starting paid is the reversible choice: a paid listing can be made
free later and past buyers keep access, while a free listing that turns paid locks existing
users out until they buy.

The plan in the design: keep $5 for 30 days after the comparison in phase 2 is published, then
make the listing free if fewer than five copies sold.

Do not price or describe it as a signal product. The repository is public and MIT licensed, so
the listing sells packaging, and the one published result does not support a claim of trading
edge. Paid skills carry a money-back promise for a skill that does not do what its page says,
which is one more reason this page promises only what the scripts do. Check the marketplace's
current creator terms for the revenue split before you set the price.

## Note for the seller, not listing text: the data provider's terms

This section is for the person who lists the skill. None of it goes on the listing page.

`get_data.py` asks Yahoo Finance's public chart, search and statement endpoints directly, with
a User-Agent that names this project, no cookies and no login step. These endpoints are
unofficial. The skill is not affiliated with, endorsed or vetted by Yahoo.

The skill redistributes no data: each buyer's own machine downloads it for that buyer's own
use, and the tests in this repository hold generated numbers only. Even so, a paid listing is
a commercial product at any price, and Yahoo's terms do not clearly allow one to be built on
this data. This is the weakest point of a paid listing. Read the terms, or move to a licensed
source, before taking payment. A free listing carries less of this risk.

## What this page must not say

- No measured claim about the skill: no error rates, no share of figures caught, no comparison
  with an agent working alone. Those numbers do not exist until the phase 2 protocol has run.
- No performance claim beyond the one test on record, which the desk lost.
- No "tested" mark on a host nobody has run it on.
