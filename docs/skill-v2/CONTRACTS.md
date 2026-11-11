# Trading Agents Desk skill v2: contracts

This file is the specification the three scripts, the skill text and the tests are built against.
"MUST" is binding. Where it and the code disagree, fix one of them in the same change.

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
- Each script is at most 20,000 bytes. Targets: `get_data.py` 5,000, `brief.py` 14,000,
  `render.py` 14,000, `page.py` 12,000.
- No `os`, `subprocess`, `socket`, `eval`, `exec`, `open` for writing except the output paths given
  on the command line, no environment access in the script's own code.
- Inputs are read as bytes. A UTF-8 or UTF-16 byte-order mark is accepted and removed. Unicode minus
  and no-break space are normalised.
- Output is ASCII only (`json.dumps` default, `errors="backslashreplace"` on stdout and stderr).
- A script never prints a traceback. Every failure is one line on stderr starting with `STOP ` or
  `ERROR `, followed by a code and a fixed sentence, and a non-zero exit code.
- Exit codes: 0 ok. 2 stop (data or input problem; the sentence is shown to the user unchanged).
  3 integrity (the script or a sibling was altered, or a known-answer test failed). 4 usage.
  For `render.py`, 1 means the draft has errors to fix.
- Dates are computed with epoch arithmetic and the exchange offset in the data. Never local time,
  never `zoneinfo`. "Now" is the fetch time recorded in the bundle, never the machine clock.
- Every script supports `--version`, printing `trading-agents-desk <script> 2.0.0`.

### Integrity

- Each script holds `SELF = "<12 hex>"`: the first 12 hex digits of SHA-256 over its own text with
  the `SELF` line removed, line endings normalised to LF and trailing whitespace stripped.
  `brief.py`, `render.py` and `page.py` check it on every run (they read their own file). `get_data.py` does
  not read itself.
- `brief.py` also holds `SIBLINGS = {"get_data.py": "<12 hex>", "render.py": "<12 hex>", "page.py": "<12 hex>"}`.
- `python brief.py --preflight <dir>` checks itself and the three siblings in `<dir>`, runs the
  indicator known-answer test, and prints `PREFLIGHT ok 2.0.0 python <version>` or exits 3.
- `render.py` runs a built-in known-answer case on every run: one draft that must render and one
  that must be refused.
- `skill/build_skill.py` stamps the hashes. A test fails if a stamped hash is stale.

## 2. `get_data.py`

```
python get_data.py SYMBOL OUT_PATH
```

- `SYMBOL` MUST match `^[A-Za-z0-9.^=-]{1,15}$`, else `STOP E_SYMBOL`. It is upper-cased and
  percent-encoded before use.
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
  share classes), `E_REFUSED` (429, 403, 999: "the data source refused this network; this is not a
  problem with the ticker and waiting rarely helps"), `E_NO_ROUTE` (no connection or timeout),
  `E_CERT` (certificate not trusted), `E_SHAPE` (an answer that is not the expected JSON).
- Output: `bundle.v1` written to `OUT_PATH`, and one line on stdout: `DATA ok <SYMBOL> bars=<n>
  headlines=<n> statements=<ok|absent>`.

### `bundle.v1`

```json
{"schema": "bundle.v1", "symbol": "NVDA", "source": "yahoo-chart",
 "fetched_at": 1794369960,
 "chart": {"meta": {...as returned...}, "timestamp": [...], "open": [...], "high": [...],
           "low": [...], "close": [...], "volume": [...]},
 "news": [{"title": "...", "publisher": "...", "time": 1794371570, "tickers": ["NVDA"]}],
 "statements": {"trailingPeRatio": [{"asOf": "2026-11-09", "value": 22.59}], "...": []},
 "errors": {"news": null, "statements": "HTTP 404"}}
```

- `fetched_at` is the epoch of the chart response's HTTP `Date` header (fallback:
  `meta.regularMarketTime`).
- `close` is Yahoo's split-adjusted close, not the dividend-adjusted one. Nulls are kept as `null`.
- `news` keeps only items whose ticker list contains the symbol. Titles are cut to 160 characters.

## 3. `brief.py`

```
python brief.py --preflight DIR
python brief.py BUNDLE_OR_PRICE_FILE BRIEF_OUT [--symbol S --currency C]
```

Input is detected by content: `bundle.v1`; the marketplace price-history result (an object holding
`history: [{open, high, low, close, volume, timestamp}]`, at any depth, with `symbol` and
`currency`); or CSV text with a header `date,open,high,low,close,volume` (`--symbol` required).
For the last two, `source` is `price-file`, headlines and statements are absent, session facts are
unknown, and the fetch time is the last bar's date.

### Validation, in this order

| Code | Rule | Result |
|---|---|---|
| `E_INPUT` | Not one of the three shapes | stop |
| `E_TYPE` | `instrumentType` known and not one of EQUITY, ETF, CRYPTOCURRENCY, INDEX, MUTUALFUND | stop |
| rows | A row with a null close | Dropped with a note, except the two cases below |
| fill | The newest row has open, high, low but no close, the session is over, and `regularMarketPrice` carries a `regularMarketTime` on that row's exchange date | Close filled from it. Flag `close_filled`, note names the date |
| `E_PENDING` | A null row directly before an unfinished newest bar (seen for crypto) | stop: "the source has not published the previous close yet" |
| `E_OHLC` | A price <= 0, or low > open/close, or high < open/close, in any kept row | stop, naming the date |
| `E_FEW` | Fewer than 60 kept rows | stop |
| `E_GAP` | More than 5 calendar days between two kept rows in the newest 60 (more than 1 for CRYPTOCURRENCY) | stop, naming both dates |
| `E_STALE` | Newest bar more than 12 days before the fetch time | stop |
| `E_CLOCK` | Newest bar after the fetch time by more than a day | stop |
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
 "bars": [{"date": "2026-11-10", "open": 173.97, "high": 174.69, "low": 165.89, "close": 168.89,
           "volume": 426599700}],
 "series": {"date": ["..."], "close": [...], "sma50": [...], "sma200": [...], "volume": [...]},
 "headlines": [{"id": "h1", "date": "2026-11-11", "publisher": "...", "title": "..."}],
 "scales": {"rsi14": [0, 100, 30, 70]},
 "disclaimer": "...", "test_record": "..."}
```

- `data_id`: first 8 hex of SHA-256 over the kept rows (date and five values each).
- `bars`: the newest 10 kept rows, oldest first. `series`: the newest 120 rows for the chart.
- `unit` is `currency` except for minor units: `GBp`/`GBX` pence, `ZAc` cents, `ILA` agorot; a note
  says so.
- A fact: `v` the number (or text for dates), `t` the display text, `label`, `kind` one of
  `price, pct, level, count, ratio, money, date, text`. Optional `as_of` (a date) for statement
  figures, and `sign` (`+`/`-`) for kinds that carry direction.
- Display: prices with 2 decimals, or 4 significant figures when below 10. Percent facts with 2
  decimals and an explicit sign when the fact is a change or a gap. Counts and money as `426.6M`,
  `4.08T`. Never exponent notation.
- stdout: the fact sheet, plain text, one fact per line `id | label | text`, grouped, followed by
  the notes, the flags, the headline list (`h1 | date | publisher | title`) and the line
  `BRIEF ok <SYMBOL> <as_of> facts=<n> data_id=<id>`.

### Fact names

Header: `last, as_of, prev_close, chg1` (one-day change).
Table, newest first, N from 0 to 9: `date.N, open.N, high.N, low.N, close.N, volume.N, chg.N` (close
against the previous close), `gap.N` (open against the previous close).
Indicators: `rsi14, macd, macd_signal, macd_hist, bb_pctb, bb_upper, bb_lower, atr14, atr14_pct,
adx14, cci20, stoch_k, stoch_d, stoch_j, mfi14`.
Averages: `sma10, sma50, sma200, sma10_gap, sma50_gap, sma200_gap` (close against the average).
Returns and risk: `ret1, ret5, ret21, ret63, vol21`.
Range: `high52, high52_date, high52_gap, low52, low52_date, low52_gap`.
Volume: `vol_avg20, vol_ratio` (newest against the 20-bar average), `vol_sum2, vol_sum5`.
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
- The slot grammar, `HOLD 0.0` style values in `[trader]` and `[manager]`, and references are the
  only places a digit may appear. A digit anywhere else in a slot is an error.
- Decision rules are applied by the renderer: an action outside the set becomes HOLD; a size outside
  0 to 1 is clamped; a HOLD has size 0; `rejected` means HOLD 0; the final action cannot be one the
  trader did not propose and the final size cannot exceed the trader's.

## 5. `render.py`

```
python render.py BRIEF DRAFT OUT_MD OUT_REPORT [--attempt N]
python render.py --verify BRIEF OUT_MD
```

Errors (exit 1, one line each on stdout, `FIX <slot>: <what> | try: <nearest fact names>`):

| Code | Meaning |
|---|---|
| `D_SLOT` | A required slot is missing, empty or malformed |
| `D_NAME` | A reference names a fact that is not in the brief. The three nearest names are listed |
| `D_DIGIT` | A digit typed outside a reference |
| `D_FORMAT` | A format that does not fit the fact's kind |
| `D_LABEL` | A reference directly follows another fact's label (for example "MFI {{rsi14}}") |
| `D_PHRASE` | Advice, certainty or personal-position wording from the fixed list |
| `D_NOTE` | The brief has an unfinished session and the draft says "close" for the last price |
| `D_LEN` | A prose slot over 600 characters |

On success it writes the Markdown report and the resolved report (`report.v1`, below), prints the
Markdown report, and prints the receipt as the last lines. The receipt is generated, never typed by the agent:

```
Figures: <n> inserted by code from the data. Quantity words: <k>, not checked.
Data <data_id> to <as_of>, <session>. Report <report_id>. Attempt <N>. Script 2.0.0.
This covers sourcing and arithmetic, not judgment.
```

`report_id` is the first 8 hex of SHA-256 over the report body. `--verify` recomputes the figures
and both ids from the brief and the saved Markdown and prints `VERIFY ok` or the differences.

The Markdown report and the page carry the same content: header, the argument and the paper
desk, why, bull against bear, levels, risk review, data notes (from the brief's notes and flags,
skipped roles, how the roles ran), headlines the desk saw, receipts with the test record and the
disclaimer. Fixed texts come from the brief. Headline text is escaped and shown only in the
headlines block and where `{{hN}}` is used.

### `report.v1`

Every reference is already resolved to text, so `page.py` needs no reference engine.

```json
{"schema": "report.v1", "symbol": "NVDA", "report_id": "9f3a1c2e", "attempt": 1, "figures": 31,
 "quantity_words": 0, "script": "2.0.0",
 "argument": {"code": "NEUTRAL", "text": "Neither side won"},
 "desk": {"action": "HOLD", "size": 0.0, "text": "No trade"},
 "why": "...",
 "bull": {"claim": "...", "evidence": [{"label": "RSI(14)", "text": "23.95"}], "also": "...", "weak": "..."},
 "bear": {"claim": "...", "evidence": [], "also": "...", "weak": "..."},
 "levels": [{"label": "Previous close, 2026-11-09", "price": 178.67, "price_t": "178.67",
             "gap_t": "+5.8%", "side": "bull", "words": "..."}],
 "risk": "...", "roles": "...", "skipped": [], "headlines_used": ["h1"],
 "receipt": ["...", "...", "..."]}
```

`argument.text` is "The bull case won", "The bear case won" or "Neither side won". `desk.text` is
"No trade" for HOLD, and for BUY and SELL a size word and direction ("Small long", "Small short";
"Medium" from 0.34, "Large" from 0.67).

## 6. `page.py`

```
python page.py BRIEF REPORT OUT_HTML
```

Draws the report as one self-contained HTML page: inline CSS, inline SVG, no JavaScript, no external
requests. It prints `PAGE ok <SYMBOL> <report_id> bytes=<n>`. Every string from both inputs is
escaped. Layout, in order: a dark header band with four tiles (the argument, the paper desk, the
last close with its one-day change, the count of figures inserted by code); the "why" text in large
type; a notice strip when the brief has flags; the price chart with the 50-day and 200-day averages
and the report's levels, beside indicator meters on their real scales and the returns; bull and
bear as two equal cards; the levels with a price ladder, beside the risk review; statement figures
as tiles, beside the headlines the desk saw; receipts with the test record made prominent.

No composite score, dial, star rating or tier badge. A BUY or SELL outcome is shown in the same
plain style as a HOLD. Colour is never the only carrier of meaning.

## 7. Tests

- Parity: `brief.py` indicators equal the package's on 200 seeded synthetic series (relative 1e-9
  on values, equal display text).
- Shapes: fixtures with the real payload shapes and generated numbers for all three inputs, plus
  the edge cases in section 3.
- Renderer: every error code has a test that triggers it and a clean draft that passes.
- Mutation: copies of each script with single-character changes must be refused by the preflight.
- Lint in `skill/build_skill.py`: grammar, imports, sizes, ASCII, stamped hashes, and no packaged
  file matching the marketplace's documented fail patterns.
