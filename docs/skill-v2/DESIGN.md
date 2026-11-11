# Trading Agents Desk skill, version 2: design

Status: approved in outline by the owner on 2026-11-11. Revision 2, after a red-team review.
The exact file formats are in [CONTRACTS.md](CONTRACTS.md).

## The product

A bull and a bear argue one stock from the same dated data. A script fetches the data and computes
every indicator. The agent runs the desk and writes its report using named references to those
figures. A second script inserts every figure from the data and draws the report as a clean page.
The agent never types a market number.

The listing states up front that this desk design lost to buy-and-hold in its only test (-0.9%
against +15.7%, one stock, one quarter). The skill sells checkable two-sided reasoning, never a
prediction.

## Why version 1 is being replaced

1. It needs a terminal, pip, GitHub, PyPI and 755 MB. One test host refused the install.
2. The marketplace's security scan marked it "fail". The run-time install is the likely cause.
3. Nothing checks the output. The one audited report was verified by hand.
4. Every fix needs a release tag, an upload and a review.
5. The report is a wall of text.

## How version 2 works

Four small scripts, Python standard library only (3.9 or newer), nothing installed.

| Script | Network | Job |
|---|---|---|
| `get_data.py` | yes, one host | Downloads daily prices with session facts, headlines and statement figures. Reads no files. Identifies itself honestly, uses no cookies and no login step |
| `brief.py` | no | Validates the data, computes the indicators and about 80 named facts, writes every data problem as a flag and a note, prints the fact sheet the desk reads |
| `render.py` | no | Reads the agent's draft, inserts every figure by reference, refuses any digit the agent typed, applies the decision rules and prints the report |
| `page.py` | no | Draws the finished report as one self-contained page |

The buyer's agent does five things: check Python is available, get the data, build the brief, run
the desk roles and write a draft, render, draw the page. On a failed render it fixes the draft, at
most twice.

### Report by reference

Version 1 let the agent type figures and hoped they were right. The first version 2 design checked
typed figures afterwards against a pool of data values. The red team broke that: a wrong sign, two
swapped figures, an invented round number and a number lifted from a headline all passed, and a
third of the figures in a correct report failed.

So the agent no longer types figures at all. It writes `{{rsi14}}` and the script prints `23.95`.
Evidence lists are lists of fact names, and the script prints each with its own label, so a figure
cannot sit under the wrong label there. Any digit typed in the draft is an error that names the
nearest facts. Dates and headlines are references too.

What this guarantees: every figure on the report is a real figure from the fetched data, with the
right sign and unit, inserted by code.
What it does not guarantee, and the listing says so: that the sentence around a figure is right,
that the desk chose the facts that matter, or that its view is any good. Quantity words such as
"half" and "double" are counted and shown on the receipt as not checked.

### The page

`page.py` writes one self-contained HTML page: a header band with four summary tiles, the "why"
sentence in large type, a price
chart with the two averages and the levels that would change the view, indicator meters on their
real scales, bull against bear in two equal columns, the levels table, the risk review,
fundamentals, the headlines the desk saw, and the receipts. No score dial, no tier badge: the desk
has no validated score. The owner chose this layout from three working mock-ups. `render.py`
prints the same report as Markdown for the chat.

### Data

- Default route: `get_data.py` asks Yahoo Finance's public chart, search and statement endpoints on
  one host, `query1.finance.yahoo.com`, with a self-identifying User-Agent. Tested on 2026-11-11.
- No-network route: `brief.py` also accepts a price file (date, open, high, low, close, volume) or
  the saved result of the marketplace's price-history tool. Fundamentals and headlines are then
  flagged as absent.
- Yahoo is an unofficial source. The listing and every report say the skill is not affiliated with
  or endorsed by Yahoo and that use of the data is subject to Yahoo's terms.

### Stops and flags

A data problem is never analysed silently. Stops have fixed sentences written by the script: unknown
symbol, refused by the data source, no route, certificate not trusted, too few bars, a gap in the
dates, impossible prices, data older than 12 days, an unsupported instrument type. Flags travel into
the report: unfinished session, a close filled from the exchange's last regular price, rows dropped,
fundamentals absent, no headlines, short history.

### Integrity

Each script carries the hash of its own text and stops if it was copied wrongly. One preflight
command checks all four copies and runs known-answer tests on the indicator code and on the
renderer.

## What leaves the skill

The run-time install, the paid API run, the backtest instructions, the web-search fallback for
headlines and the timed retry rules. The Python package stays in this repository as the research
lab, unchanged. The skill no longer installs it, so fixes no longer need a release tag.

## Decisions and their reasons

| Question | Decision | Reason |
|---|---|---|
| Typed figures checked afterwards, or figures by reference? | By reference | The red team showed that checking afterwards cannot bind a figure to its meaning |
| Marketplace data tools or a direct download? | Direct download by default, price file as the no-network route | The tools hand the agent 62 KB of rows to copy by hand. The download never passes data through the model |
| Browser disguise and login step for fundamentals? | No | Statement figures are available without them, each with its own date |
| Twelve roles or fewer? | Twelve in 2.0, decided by experiment later | Change one layer at a time |
| A score or a probability? | Neither | Neither can be validated at any sample size a buyer will reach |
| One big script or several? | Four, each under 20 KB | One talks to the network and reads nothing. Three read files and never touch the network. The page is separate so the checked report does not depend on it |

## Engineering

- Indicator parity: the scripts must match the package's pandas code on 200 generated series.
- Fixtures use real payload shapes with generated numbers, so no market data is redistributed.
- The renderer has tests per error class: wrong sign, swapped names, typed digits, unknown names,
  headline text, missing sections, advice phrases.
- Build lint: Python 3.9 grammar, an import allowlist, size caps, the marketplace's documented fail
  patterns, and stamped hashes.
- CI runs the shipped scripts on Python 3.9 and the newest release on Linux, macOS and Windows.
- A nightly canary classifies each run as ok, refused or changed, and opens an issue only on changed.

## Phases

1. Version 2.0: the four scripts, the rewritten skill, tests, lint, CI, the canary, buyer-style
   test runs on several instruments, new listing text. The listing makes no measured claim yet.
2. The proof: a protocol committed before any run. Three arms: an agent alone, an agent with the
   same fact sheet and no renderer, and the full skill, judged by an independent audit. Results by
   error class, published whatever they show. Screenshots from real runs.
3. On evidence only: fewer roles, a re-check of an old report's conditions, a free companion
   listing, a price change.

## Risks

| Risk | Mitigation |
|---|---|
| Yahoo changes or refuses the endpoints | One fixed identity, no retries, a fixed stop sentence that says the fault is at the source, the price-file route, the canary, and updates that reach every buyer because skills reload |
| The buyer's host has no internet from its code tool | The price-file route, and a host table on the listing that says what was tested |
| An agent copies a script wrongly | Self-hash and known-answer tests stop the run |
| The stamp lends a weak view more authority than it deserves | The receipt says in fixed words what is and is not guaranteed, and the test the desk lost is on every report |
| The listing still sells nothing | $5 for 30 days after the proof is live, then free if fewer than five sales |
