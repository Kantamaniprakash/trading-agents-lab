# Trading Agents Desk skill, version 2: design

Status: approved in outline by the owner on 2026-11-11. Revision 2, after a red-team review.
Revision 3 brings this text in line with CONTRACTS.md rev 3: four scripts, the page as an
optional step, the row rules, and who stamps the known answers.
The exact file formats are in [CONTRACTS.md](CONTRACTS.md).

## The product

A bull and a bear argue one stock from the same dated data. One script fetches the data and a
second computes every indicator. The agent runs the desk and writes its report using named
references to those figures. A third script inserts every figure from the data and prints the
report, and a fourth can draw it as a clean page. The agent never types a market number.

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
| `render.py` | no | Reads the agent's draft, inserts every figure by reference, refuses any digit the agent typed, applies the decision rules, prints the report and saves it in resolved form for the page |
| `page.py` | no | Draws the finished report as one self-contained page. An optional step |

The buyer's agent does six things: check Python is available, get the data, build the brief, run
the desk roles and write a draft, render, and draw the page where its host can show one. On a
failed render it fixes the draft, at most twice.

The agent writes each script out from the skill text on a first run, so every script stays under
20,000 bytes and is plain readable source: one statement per line, lines of at most 100
characters, a comment where a rule is not obvious. When a script does not fit, a feature is cut,
never the formatting. The agent writes only the scripts a run uses: `brief.py` and `render.py`
always, `get_data.py` when its host has network access, `page.py` when it will draw the page.

### Report by reference

Version 1 let the agent type figures and hoped they were right. The first version 2 design checked
typed figures afterwards against a pool of data values. The red team broke that: a wrong sign, two
swapped figures, an invented round number and a number lifted from a headline all passed, and a
third of the figures in a correct report failed.

So the agent no longer types figures at all. It writes `{{rsi14}}` and the script prints `23.95`.
Evidence lists are lists of fact names, and the script prints each with its own label, so a figure
cannot sit under the wrong label there. Any digit typed in the draft is an error that names the
nearest facts. Dates and headlines are references too.

What this guarantees: every figure on the report is a real figure from the run's data (the
download, or a price file), with the right sign and unit, inserted by code. A statement money
figure carries the currency the source names for it, which is often not the currency of the price,
and a statement figure is printed with its date wherever the script prints its label.
What it does not guarantee, and the listing says so: that the sentence around a figure is right
or names the right fact, that the desk chose the facts that matter, or that its view is any good.
Quantity words such as "half" and "double", and numbers spelled out in words, are counted and
shown on the receipt as not checked. A headline is quoted so that it cannot end its own quotation:
a double quote inside a title is shown as a single one.

### The page

`page.py` writes one self-contained HTML page: a header band with four summary tiles, the "why"
sentence in large type, a notice strip when the data has a flag, a price
chart with the two averages and the levels that would change the view, indicator meters on their
real scales, bull against bear in two equal cards, the levels on a price ladder and as a table,
the risk review, statement figures as tiles, the headlines the desk saw, and the receipts. No
score dial, no tier badge: the desk has no validated score. The owner chose this layout from
three working mock-ups. To fit readable source into 20,000 bytes the page lost its volume bars,
its legend row, its print rules and its tablet layout.

The page is an optional step. `render.py` prints the report as Markdown for the chat, and that
report is the answer. The agent draws the page afterwards when its host can show or save an HTML
file, or when the user asks, says where the file is, and never pastes the HTML into the chat.

`render.py` also saves the resolved report (`report.v1`), in which every reference is already
text. `page.py` draws from that file and the brief, so it needs no reference engine, and it
refuses a report that was not made from the brief it is given. `--verify` checks the Markdown
report. Nothing checks the page at run time: the tests compare every text on it that holds a
figure, and what it draws (each line point, the axis, the month names, the levels, the ladder),
with the data.

### Data

- Default route: `get_data.py` asks Yahoo Finance's public chart, search and statement endpoints on
  one host, `query1.finance.yahoo.com`, with a self-identifying User-Agent. Tested on 2026-11-11.
- No-network route: `brief.py` also accepts a price file (date, open, high, low, close, volume) or
  the saved result of the marketplace's price-history tool. Fundamentals and headlines are then
  flagged as absent, and the report claims no fetch time. The saved result carries time stamps
  and no exchange offset. For an exchange that opens late in the UTC day (Sydney in its summer,
  New Zealand) the exchange date of a row cannot be told from the stamp, so `brief.py` stops
  (`E_DATES`) instead of printing dates that may be a day early. The real tool was never called
  for such a listing; the rule rests on the stamps the download shows for Sydney.
- Yahoo is an unofficial source. The listing and every report say the skill is not affiliated with
  or endorsed by Yahoo and that use of the data is subject to Yahoo's terms.

### Stops and flags

A data problem is never analysed silently. Stops have fixed sentences written by the script: unknown
symbol, refused by the data source, no route, an error at the source itself, certificate not
trusted, too few bars, a gap in the dates, impossible prices, data older than 12 days or dated
after the fetch, a saved price history whose dates cannot be read, an unsupported instrument type. Flags travel into
the report: unfinished session, a close filled from the exchange's last regular price, prices
behind, rows dropped, a shorter gap in the dates, fundamentals absent, no headlines, short history.

The row rules follow the daily-row shapes the source was seen to send on 2026-11-11:

- Two rows for one exchange date (Sydney after the close) become one: the newest complete one.
  Nothing is said.
- A newest row with no close, once its session is over (London and Frankfurt from the close until
  the next open), takes the exchange's last regular price as its close when that price is dated on
  the row's day and lies inside the row's range. Flag `close_filled`.
- A row with a missing value among the newest three ends the view before it. When that row is a
  finished session (London and Frankfurt minutes after the next open, or a crypto day), the flag
  is `prices_behind`, the note names the dates and the source's last regular price, and the first
  line of the report and of the page says that the source is behind. When it is only the session
  in progress, a note says so and nothing is flagged.
- An older row with a missing value is dropped. Flag `rows_dropped`.
- A gap of more than 12 days between two kept rows stops the run (more than 1 day for crypto).
  A gap of 6 to 12 days, such as a long exchange holiday, is flagged as `history_gap`.

The earlier stop for a close that the source had not published yet is gone: that case is now
`prices_behind`, and the desk runs on the last complete day.

### Integrity

Each script carries the hash of its own text and stops if it was copied wrongly. One preflight
command checks the copies a run uses and names them in its answer: `brief.py` and `render.py`
must be there, `get_data.py` and `page.py` are checked when they are there, and a file that is
there but altered is always an error. The preflight runs a known-answer test on the indicator
code, and the renderer runs its own on every run.

`skill/build_skill.py` stamps the hashes and the known answers. It computes a known answer by
running the script's own known-answer function, so the build never holds a second copy of that
logic. A known answer is there to catch a different result on a buyer's Python or a damaged
copy. It is not a regression test: the tests against the package are, and CI runs the stamped
scripts on every supported Python and system. The build's `--check` fails on a stale hash or a
stale known answer. No formatter may rewrite a stamped script, so the formatter hooks skip them.

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
- Build lint: Python 3.9 grammar, an import allowlist, file-access rules per script, readable
  source (one statement per line, lines of at most 100 characters), size caps, the marketplace's
  documented fail patterns, and stamped hashes and known answers.
- The end-to-end test rebuilds the whole Markdown report from the brief and the draft, and
  compares every text on the page that holds a digit with the brief or the resolved report.
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
