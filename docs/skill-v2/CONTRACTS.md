# Trading Agents Desk skill v2: contracts

This file is the specification the four scripts, the skill text and the tests are built against.
"MUST" is binding. Where it and the code disagree, fix one of them in the same change.

Rev 4 (after the review of version 2) changed: the hash rule and the escapes rule in section 1;
`E_SERVER`, the symbol rule and the statement currency in section 2; `E_DATES`, the merged
`E_STALE`, the fact sheet without group headings, money with its currency, and the facts
`vol_sum2`, `vol_sum5` and the series row `volume` removed, in section 3; statement dates,
headline quoting and the touching rule in section 4; the receipt printed once, attempts 1 to 3,
`D_NOTE` for an unknown session and the price-file header in section 5; what the page draws in
section 6.

Shipped files (all ASCII, LF line endings, under `skill/trading-agents-desk/`):

```
SKILL.md
references/roles.md
references/lab.md
scripts/get_data.py     network, reads no files
scripts/brief.py        no network
scripts/render.py       no network
scripts/page.py         no network
LICENSE.txt
```

## 1. Rules for all four scripts

- Python 3.9 grammar and standard library only. Allowed imports: `sys, json, math, re, hashlib,
  datetime, html, urllib.request, urllib.parse, urllib.error` (the three `urllib` ones only in
  `get_data.py`).
- Each script is at most 20,000 bytes, because the agent writes each one out from the skill text
  on every first run.
- The source is readable: one statement per line, lines of at most 100 characters, names that say
  what they hold, a comment where a rule is not obvious. No minified or packed code. When a script
  does not fit, a feature is cut, never the formatting. The style sheet inside `page.py` may be
  compact, as style sheets are.
- Known exception to the naming rule: `brief.py` and `render.py` are at the size cap and keep
  short names in their arithmetic, such as `o, h, lo, c, v` for the price columns, `n` for the
  row count, `F` for the facts and `D` for the dates. A rename costs about 600 bytes in each,
  which would cut a feature. The build prints the number of one-letter names in each script and
  a test keeps it from growing. New code in any script follows the rule.
- No backslash-u, backslash-U or backslash-N escape in a script. Some agent hosts decode such a
  sequence into the character while they write the file out, and the copy then fails its
  integrity check. A character outside ASCII is spelled `chr(n)`. The build lint refuses the
  escapes.
- No `os`, `subprocess`, `socket`, `eval`, `exec`, `open` for writing except the output paths given
  on the command line, no environment access in the script's own code.
- Inputs are read as bytes. A UTF-8 or UTF-16 byte-order mark is accepted and removed. Unicode minus
  and no-break space are normalised by `get_data.py`, `brief.py` and `render.py`. `page.py` reads
  only the two files the other scripts wrote, which are ASCII, and changes no character of them.
- Output is ASCII only (`json.dumps` default, `errors="backslashreplace"` on stdout and stderr).
- A script never prints a traceback. Every failure is one line on stderr starting with `STOP ` or
  `ERROR `, followed by a code and a fixed sentence, and a non-zero exit code.
- Exit codes: 0 ok. 2 stop (data or input problem; the sentence is shown to the user unchanged).
  3 integrity (the script or a sibling was altered, or a known-answer test failed). 4 usage, or
  a file named on the command line that cannot be read or written. For `render.py`, 1 means the
  draft has errors to fix.
- An output that is closed or full: `get_data.py` ends with `ERROR E_WRITE`, `brief.py` with
  `ERROR E_USAGE` and `render.py` with `ERROR E_FILE`, each with exit code 4, and Python's own
  complaint about the stream is not shown. Their files are written by then. `page.py`, which
  prints one line, does not guard this: Python adds its own lines and ends with exit code 120.
- Dates are computed with epoch arithmetic and the exchange offset in the data. Never local time,
  never `zoneinfo`. "Now" is the fetch time recorded in the bundle, never the machine clock.
- Every script supports `--version`, printing `trading-agents-desk <script> 2.0.0`.

### Integrity

- Each script holds `SELF = "<12 hex>"`: the first 12 hex digits of SHA-256 over its own bytes with
  the stamp line removed, line endings normalised to LF and trailing whitespace stripped. The
  stamp line is a line that is exactly `SELF = "<12 hex>"`. No other line is left out, whatever
  it starts with, and a byte-order mark in front of the file counts as a change. One rule for
  the three scripts that check and for the build.
  `brief.py`, `render.py` and `page.py` check it on every run (they read their own file). `get_data.py` does
  not read itself.
- `SELF = "000000000000"` marks a development copy: the script prints `WARN unstamped development
  copy` on stderr and skips the comparison. The build stamps every script and `--check` fails on
  a zero stamp, so the package never holds one. The check is there to catch a damaged or
  half-written copy. It is no protection against a change made on purpose.
- `brief.py` also holds `SIBLINGS = {"get_data.py": "<12 hex>", "render.py": "<12 hex>", "page.py": "<12 hex>"}`.
- `python brief.py --preflight <dir>` checks itself and `render.py` in `<dir>` (both MUST be
  there), and `get_data.py` and `page.py` when they are there: a host with no network has no use
  for the first, and a run that draws no page has no use for the second. It runs the indicator
  known-answer test and prints `PREFLIGHT ok 2.0.0 python <version> checked <names>` (the file
  names it checked, in alphabetical order) or exits 3. A file that is there but altered is always
  an error.
- `render.py` runs a built-in known-answer case on every run: one draft that must render and one
  that must be refused.
- `skill/build_skill.py` stamps the hashes and the known answers (`KAT` in `brief.py` and
  `render.py`, computed by running the script's own known-answer function on the building Python).
  A known answer exists to catch a different result on the buyer's Python or a damaged copy, not a
  regression: the tests against the package cover regressions, and CI runs the stamped scripts on
  every supported Python and system. `--check` fails on a stale hash or a stale known answer.

## 2. `get_data.py`

```
python get_data.py SYMBOL OUT_PATH
```

- `SYMBOL` MUST match `^[A-Za-z0-9.^=-]{1,15}$` and hold at least one letter or digit, else
  `STOP E_SYMBOL`. It is upper-cased and percent-encoded before use.
- One host only: `https://query1.finance.yahoo.com`. Three GET requests, 10-second timeout each:
  1. `/v8/finance/chart/<S>?range=1y&interval=1d&events=div%2Csplit` (bars and session facts)
  2. `/v1/finance/search?q=<S>&newsCount=10&quotesCount=0` (headlines)
  3. `/ws/fundamentals-timeseries/v1/finance/timeseries/<S>?symbol=<S>&type=<list>&period1=<now-800d>&period2=<now>`
     with types `trailingPeRatio, trailingForwardPeRatio, trailingMarketCap, trailingPegRatio,
     trailingTotalRevenue, trailingNetIncome, quarterlyTotalRevenue, quarterlyNetIncome,
     quarterlyTotalDebt, quarterlyStockholdersEquity`
- User-Agent: `trading-agents-desk/2.0 (+https://github.com/Kantamaniprakash/trading-agents-lab)`.
  No cookies, no login step, no retries, no second identity.
- Request 1 failing is a stop. Requests 2 and 3 failing are recorded and the run continues.
- Stops: `E_SYMBOL`, `E_NOT_FOUND` (404; if the symbol has a dot, the sentence suggests a hyphen for
  share classes), `E_REFUSED` (401, 403, 429, 999: "the data source refused this network; this is
  not a problem with the ticker and waiting rarely helps"), `E_NO_ROUTE` (no connection or
  timeout), `E_SERVER` (500 and above: the source was reached and answered with an error of its
  own; it may work later), `E_CERT` (certificate not trusted), `E_SHAPE` (an answer that is not
  the expected JSON, or any other status).
- Errors: `E_USAGE` (exit 4), `E_WRITE` (exit 4: the output file cannot be written, or the result
  line cannot be printed), `E_INTERNAL` (exit 3).
- Output: `bundle.v1` written to `OUT_PATH`, and one line on stdout: `DATA ok <SYMBOL> bars=<n>
  headlines=<n> statements=<ok|absent>`.

### `bundle.v1`

```json
{"schema": "bundle.v1", "symbol": "NVDA", "source": "yahoo-chart",
 "fetched_at": 1794369960,
 "chart": {"meta": {...as returned...}, "timestamp": [...], "open": [...], "high": [...],
           "low": [...], "close": [...], "volume": [...]},
 "news": [{"title": "...", "publisher": "...", "time": 1794371570, "tickers": ["NVDA"]}],
 "statements": {"trailingPeRatio": [{"asOf": "2026-11-09", "value": 22.59}],
                "trailingMarketCap": [{"asOf": "2026-11-09", "value": 4.08e12, "currency": "USD"}],
                "...": []},
 "errors": {"news": null, "statements": "HTTP 404"}}
```

- `fetched_at` is the epoch of the chart response's HTTP `Date` header (fallback:
  `meta.regularMarketTime`).
- `close` is Yahoo's split-adjusted close, not the dividend-adjusted one. Nulls are kept as `null`.
- `news` keeps only items whose ticker list contains the symbol. Titles are cut to 160 characters.
- A statement entry keeps the source's `currencyCode` as `currency` when it is a three-letter
  code. The source names one for each money figure, and it is often not the currency of the
  price (pence against pounds, a US listing of a foreign company). Ratios carry none.

## 3. `brief.py`

```
python brief.py --preflight DIR
python brief.py BUNDLE_OR_PRICE_FILE BRIEF_OUT [--symbol S --currency C]
```

Input is detected by content: `bundle.v1`; the marketplace price-history result (an object holding
`history: [{open, high, low, close, volume, timestamp}]`, at any depth, with `symbol` and
`currency`); or CSV text with a header `date,open,high,low,close,volume` (`--symbol` required).
For the last two, `source` is `price-file`, headlines and statements are absent, session facts are
unknown, and the fetch time is the last bar's date. That time is used for the rules below and is
never shown as a fetch time: nothing was downloaded. A saved price history names its own symbol
and currency; `--symbol` and `--currency` do not change them. The symbol MUST be a plain ticker
as in section 2, else `STOP E_SYMBOL`.

### Validation, in this order

| Code | Rule | Result |
|---|---|---|
| `E_INPUT` | Not one of the three shapes | stop |
| `E_TYPE` | `instrumentType` known and not one of EQUITY, ETF, CRYPTOCURRENCY, INDEX, MUTUALFUND | stop |
| `E_DATES` | A price file with two or more rows stamped at 20:00 UTC or later (Sydney in its summer opens at 23:00 UTC of the day before, New Zealand at 21:00 or 22:00). The file carries no exchange offset, so the exchange date of such a row may be the next UTC date, or may not | stop. No date is guessed. A file with dates (the CSV shape) and the download are not affected |
| same day | Two rows on the same exchange date (seen for Sydney after the close: one stamped at the session start with no close, one stamped at the last trade with it) | One row is kept: the newest complete one, else the newest. Nothing is said |
| fill | The newest row has open, high, low and volume but no close, the session is over, the type is not CRYPTOCURRENCY, `regularMarketPrice` carries a `regularMarketTime` on that row's exchange date and lies within the row's low-to-high range (give or take 0.01%) | Close filled from it. Flag `close_filled`, note names the date |
| behind | After the two rules above, a row among the newest 3 lacks a value (seen for London and Frankfurt once the next session opens: the finished session's row is empty; and for crypto: an empty row before a valued newest row) | The history is cut before the oldest such row. If a cut row belongs to a finished session: flag `prices_behind`, and the note names the first missing date, the date the view now ends at, and the source's last regular price with its date. If every cut row belongs to the session in progress, a note says that session has no bar yet |
| rows | Any older row with a missing value | Dropped. Flag `rows_dropped`. The note names the date and says that changes beside it span two sessions |
| `E_OHLC` | A price <= 0, a volume below 0, or low > open/close, or high < open/close, in any kept row. Low and high get 0.01% of slack, as in the fill rule, because the source rounds | stop, naming the date |
| `E_FEW` | Fewer than 60 kept rows | stop |
| `E_GAP` | More than 12 calendar days between two kept rows in the newest 60 (more than 1 for CRYPTOCURRENCY). Rows are put in date order first, so a file written newest first is read like any other | stop, naming both dates |
| gap | More than 5 and at most 12 calendar days between two kept rows in the newest 60 (a long exchange holiday, or missing rows), not for CRYPTOCURRENCY | Flag `history_gap`, note names both dates |
| `E_STALE` | Newest bar more than 12 days before the fetch time, or more than a day after it | stop |
| short | Fewer than 200 rows | Flag `short_history`; 200-day and 52-week facts are absent |
| session | Newest bar time >= `currentTradingPeriod.regular.start` and fetch time < its `end` | `session = unfinished`. Missing facts: `unknown`. Else `finished`. CRYPTOCURRENCY is `unfinished` unless the bar is before the current UTC day |

### Indicators

Same definitions as `tradinglab.features.indicators` (the oracle): SMA 10/50/200, RSI 14 and ATR 14
and ADX 14 with Wilder smoothing seeded like pandas `ewm(alpha=1/n, adjust=False)`, MACD 12/26/9
with `ewm(span, adjust=False)`, Bollinger 20/2 with population standard deviation, CCI 20,
Stochastic 14/3 with J = 3K - 2D, MFI 14. Returns over 1, 5, 21 and 63 bars. 21-bar annualised
volatility of daily returns (sample standard deviation times sqrt(252)). 52-week high and low of the
close over the newest 252 bars. A division by zero yields an absent fact and a note, never a number.

### `brief.v1`

```json
{"schema": "brief.v1", "version": "2.0.0", "symbol": "NVDA", "name": "NVIDIA Corporation",
 "type": "EQUITY", "currency": "USD", "unit": "USD", "source": "yahoo-chart",
 "as_of": "2026-11-10", "fetched_at": "2026-11-11 04:06 UTC", "session": "finished",
 "data_id": "a1b2c3d4", "flags": [], "notes": [{"id": "n1", "text": "..."}],
 "facts": {"rsi14": {"v": 23.9512, "t": "23.95", "label": "RSI(14)", "kind": "level"}},
 "series": {"date": ["..."], "close": [...], "sma50": [...], "sma200": [...]},
 "headlines": [{"id": "h1", "date": "2026-11-11", "publisher": "...", "title": "..."}]}
```

- `data_id`: first 8 hex of SHA-256 over the kept rows (date and five values each).
- `series`: the newest 120 kept rows for the chart, oldest first: `date`, `close`, `sma50` and
  `sma200`. The newest 10 rows are also in `facts` as the table (`date.N`, `open.N` and so on).
- `unit` is `currency` except for minor units: `GBp`/`GBX` pence, `ZAc` cents, `ILA` agorot; a note
  says so.
- A fact: `v` the number (or text for dates), `t` the display text, `label`, `kind` one of
  `price, pct, level, count, ratio, money, date, text`. Optional `as_of` (a date) for statement
  figures, and `sign` (`+`/`-`) for kinds that carry direction.
- Display: prices with 2 decimals, or 4 significant figures when below 10. Percent facts with 2
  decimals and an explicit sign when the fact is a change or a gap; a change that rounds to zero
  is `+0.00%`, never `-0.00%`. Counts as `426.6M`. Money as `4.08T USD`: the figure, then the
  currency the bundle names for it (no currency when it names none). Never exponent notation.
- stdout: the fact sheet, plain text: a first line `# <symbol> | <name> | <type> | <unit> |
  session <session>`, then one fact per line `id | label | text` in the order of the fact names
  below (a statement fact has a fourth part, `as of <date>`), then `# Notes` and the notes,
  `# Flags` and the flags, `# Headlines` and the headline list (`h1 | date | publisher | title`),
  and the line `BRIEF ok <SYMBOL> <as_of> facts=<n> data_id=<id>`.

### Fact names

Header: `last, as_of, prev_close, chg1` (one-day change).
Table, newest first, N from 0 to 9: `date.N, open.N, high.N, low.N, close.N, volume.N, chg.N` (close
against the previous close), `gap.N` (open against the previous close).
Indicators: `rsi14, macd, macd_signal, macd_hist, bb_pctb, bb_upper, bb_lower, atr14, atr14_pct,
adx14, cci20, stoch_k, stoch_d, stoch_j, mfi14`.
Averages: `sma10, sma50, sma200, sma10_gap, sma50_gap, sma200_gap` (close against the average).
Returns and risk: `ret1, ret5, ret21, ret63, vol21`.
Range: `high52, high52_date, high52_gap, low52, low52_date, low52_gap`.
Volume: `vol_avg20, vol_ratio` (newest against the 20-bar average).
Moves: `move_max, move_max_date` (largest one-day change in the table).
Statements (each with `as_of`): `pe_trailing, pe_forward, peg, mcap, rev_ttm, ni_ttm, margin_ttm,
rev_q, rev_q_yoy, ni_q, ni_q_yoy, debt, equity, debt_equity`.
A fact that cannot be computed is absent from `facts` and named in a note.

## 4. The draft the agent writes

A plain text file. Each slot starts on a line of its own with `[name]` and runs to the next slot.

```
[verdict] NEUTRAL
[why] One to three sentences.
[bull.claim] One sentence.
[bull.evidence] rsi14, stoch_k, bb_pctb, mfi14
[bull.also] One sentence.
[bull.weak] One sentence.
[bear.claim] ...
[bear.evidence] sma10_gap, sma50_gap, sma200_gap, adx14
[bear.also] ...
[bear.weak] ...
[levels]
close.1 | bull | on a close above with falling volume
low52 | bear | on a close below
[trader] HOLD 0.0
[manager] approved HOLD 0.0
[risk] Two to four sentences.
[roles] single
[skipped] none
```

- Required slots: all of the above. `[verdict]` is BULLISH, BEARISH or NEUTRAL. `[trader]` and
  `[manager]` are an action (BUY, SELL, HOLD) and a size from 0.0 to 1.0; `[manager]` starts with
  `approved` or `rejected`. `[roles]` is `single`, `subagents` or `mixed`. `[skipped]` lists roles
  that did not run, or `none`.
- `[levels]` has one to three rows: a price-kind fact name, `bull` or `bear`, and words.
- Evidence slots hold two to five fact names. The renderer prints each as label and value.
- In prose, `{{name}}` prints the fact's text. `{{name|label}}` prints label and text.
  `{{name|dir}}` on a signed percent prints the size and a direction word ("13.76% below").
  `{{name|gap}}` on a price prints its distance from the last close ("+5.8%").
  `{{hN}}` prints headline N as a quoted title with publisher and date.
- A statement figure is named with its date wherever its label is printed, in `{{name|label}}`
  and in an evidence row: `trailing P/E 22.59 (as of 2026-11-09)`. The date can be far older
  than the prices. A bare `{{name}}` prints the figure alone.
- A headline cannot end its own quotation: a double quote inside the title is shown as a single
  quote, and the publisher and date lose their round brackets.
- The slot grammar, `HOLD 0.0` style values in `[trader]` and `[manager]`, and references are the
  only places a digit may appear. A digit anywhere else in a slot is an error. So is a sign or
  symbol straight before a reference (unless a word or a reference stands before it, as in a
  range), and a letter, a digit, a percent sign or another reference straight after one.
- Decision rules are applied by the renderer: an action outside the set becomes HOLD; a size outside
  0 to 1 is clamped; a size is rounded to the two decimals it is printed with, and one that
  rounds to zero is a HOLD; a HOLD has size 0; `rejected` means HOLD 0; the final action cannot
  be one the trader did not propose and the final size cannot exceed the trader's.

## 5. `render.py`

```
python render.py BRIEF DRAFT OUT_MD OUT_REPORT [--attempt N]
python render.py --verify BRIEF OUT_MD
```

`N` is 1, 2 or 3. Other lines: `STOP E_BRIEF` (exit 2: the first file is not a brief.v1
document), `ERROR E_FILE` and `ERROR E_USAGE` (exit 4), `ERROR E_SELF` and `ERROR E_KAT` (exit 3).

Errors (exit 1, one line each on stdout, `FIX <slot>: <what> | try: <a hint>`):

| Code | Meaning |
|---|---|
| `D_SLOT` | A required slot is missing, empty or malformed |
| `D_NAME` | A reference names a fact that is not in the brief. The three names nearest in letters are listed |
| `D_DIGIT` | A digit typed outside a reference, or a sign, unit, letter or second reference that touches one. The hint names the facts that print the typed figure; when none does it says to use words, and never guesses a fact by nearness in value |
| `D_FORMAT` | A format that does not fit the fact's kind |
| `D_LABEL` | A reference directly follows another fact's label (for example "MFI {{rsi14}}") |
| `D_PHRASE` | Advice, certainty or personal-position wording from the fixed list |
| `D_NOTE` | The brief's session is unfinished or unknown and the draft says "close" for the last price |
| `D_LEN` | A prose slot over 600 characters |

On success it writes the Markdown report and the resolved report (`report.v1`, below) and prints
the Markdown report, and nothing after it. The receipt stands once, in the report's Receipts
block. It is generated, never typed by the agent:

```
Figures: <n> inserted by code from the data. Quantity words: <k>, not checked.
Data <data_id> to <as_of>, <session>. Report <report_id>. Attempt <N>. Script 2.0.0.
This covers sourcing and arithmetic, not judgment.
```

Quantity words are fractions and multiples ("half", "twice", "three-fold") and numbers spelled
out ("twelve", "thirty", "a hundred", "millions"). They are counted in any sense and not checked.

`report_id` is the first 8 hex of SHA-256 over the report body. `--verify` recomputes the figures
and both ids from the brief and the saved Markdown and prints `VERIFY ok` or the differences.

The Markdown report and the page carry the same content: header, the argument and the paper
desk, why, bull against bear, levels, risk review, data notes (from the brief's notes and flags,
skipped roles, how the roles ran), headlines the desk saw, receipts with the test record and the
disclaimer. The two fixed texts (the test record and the disclaimer) are held by `render.py` and
travel to the page in `report.v1`. When the brief has the flag `prices_behind`, the header line
says the source is behind. The header ends with `data fetched <time>`, or with `prices from a
file` when the brief's source is `price-file`. The data notes name the flags in words
(`prices behind`). Headline text is escaped and shown only in the headlines block and where
`{{hN}}` is used. The report ends with the disclaimer.

### `report.v1`

Every reference is already resolved to text, so `page.py` needs no reference engine.

```json
{"schema": "report.v1", "symbol": "NVDA", "data_id": "a1b2c3d4", "report_id": "9f3a1c2e",
 "attempt": 1, "figures": 31, "quantity_words": 0, "script": "2.0.0",
 "argument": {"code": "NEUTRAL", "text": "Neither side won"},
 "desk": {"action": "HOLD", "size": 0.0, "text": "No trade"},
 "why": "...",
 "bull": {"claim": "...", "evidence": [{"label": "RSI(14)", "text": "23.95"}], "also": "...", "weak": "..."},
 "bear": {"claim": "...", "evidence": [], "also": "...", "weak": "..."},
 "levels": [{"label": "Previous close, 2026-11-09", "price": 178.67, "price_t": "178.67",
             "gap_t": "+5.8%", "side": "bull", "words": "..."}],
 "risk": "...", "roles": "...", "skipped": [], "headlines_used": ["h1"],
 "receipt": ["...", "...", "..."], "test_record": "...", "disclaimer": "..."}
```

Every text in `report.v1` is plain text: no Markdown escapes and no character references. `roles`
is the sentence the report prints for how the roles ran. `skipped` is a list of role names.

`argument.text` is "The bull case won", "The bear case won" or "Neither side won". `desk.text` is
"No trade" for HOLD, and for BUY and SELL a size word and direction ("Small long", "Small short";
"Medium" from 0.34, "Large" from 0.67). The word goes by `desk.size`, which holds two decimals.

## 6. `page.py`

```
python page.py BRIEF REPORT OUT_HTML
```

Draws the report as one self-contained HTML page: inline CSS, inline SVG, no JavaScript, no external
requests. It prints `PAGE ok <SYMBOL> <report_id> bytes=<n>`. Every string from both inputs is
escaped. The report MUST belong to the brief: same `symbol` and same `data_id`, else
`STOP E_PAIR`. Other lines: `STOP E_INPUT` (exit 2: a file is not the brief or the report),
`ERROR E_FILE` and `ERROR E_USAGE` (exit 4), `ERROR E_SELF` (exit 3). The page holds the meter scales (the range and the two convention thresholds)
for `rsi14`, `stoch_k`, `mfi14`, `bb_pctb` and `adx14`. Layout, in order: a dark header band with four tiles (the argument, the paper desk, the
last close with its one-day change, the count of figures inserted by code); the "why" text in large
type; a notice strip when the brief has flags; the price chart with the 50-day and 200-day averages
and the report's levels, beside indicator meters on their real scales and the returns; bull and
bear as two equal cards; the levels with a price ladder, beside the risk review; statement figures
as tiles, beside the headlines the desk saw; receipts with the test record made prominent.

No composite score, dial, star rating or tier badge. A BUY or SELL outcome is shown in the same
plain style as a HOLD. Colour is never the only carrier of meaning.

What is drawn follows the inputs: every point of a line at its row and its value, a higher price
higher on the page, each axis number on its grid line and written in plain decimals, a month
name at the first row of that month, each level line at its price. A level's label starts where
its dashed line starts, so that no dash stands before the price like a minus sign. The words
beside a meter go by the value as it is shown, to two decimals. The returns are named in days,
as the report names them. For a brief from a price file the header says "Prices from a file" in
place of a fetch time.

## 7. Tests

- Parity: `brief.py` indicators equal the package's on 200 seeded synthetic series (relative 1e-9
  on values, equal display text).
- Shapes: fixtures with the real payload shapes and generated numbers for all three inputs, plus
  the edge cases in section 3.
- Renderer: every error code has a test that triggers it and a clean draft that passes.
- Mutation: copies of each script with single-character changes must be refused by the preflight.
  So must a copy with a byte-order mark, with a second line that starts like the stamp line,
  or with a no-break space for a space.
- Page drawing: the points of every line, the axis, the month names, the level lines and their
  labels, the end dots and the ladder are compared with the inputs.
- Hosts that decode escapes: each script, with every backslash-u sequence decoded, is the same
  text and passes the preflight.
- Lint in `skill/build_skill.py`: grammar, imports, sizes, ASCII, stamped hashes, and no packaged
  file matching the marketplace's documented fail patterns.
