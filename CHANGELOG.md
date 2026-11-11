# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Agent skill 2.0.0** (`skill/trading-agents-desk/`). The skill no longer installs this
  package. It ships four scripts that use the Python standard library only (3.9 or newer),
  and nothing is installed when it runs:
  - `get_data.py` downloads a year of daily prices with the exchange's session times, recent
    headlines and a few statement figures from one host, `query1.finance.yahoo.com`, with a
    User-Agent that names this project, no cookies and no login step.
  - `brief.py` checks the data, stops with a fixed sentence when it cannot be used, computes
    the indicators (the same definitions as `tradinglab.features.indicators`) and prints a
    fact sheet of named facts, with every data problem as a flag and a note. It also accepts a
    price file, for a host with no network. Its row rules follow the row shapes the data
    source was seen to send:
    - Two rows for one exchange date become one, and nothing is said.
    - A newest row with no close is filled from the exchange's last regular price when the
      session is over and that price lies inside the row's range (flag `close_filled`).
    - A row with a missing value among the newest three ends the view at the last complete
      day. When that row belongs to a finished session the flag is `prices_behind`, and the
      first line of the report and of the page says that the source is behind.
    - An older row with a missing value is dropped (flag `rows_dropped`).
    - A break of 6 to 12 days in the dates is flagged (`history_gap`). A longer one stops
      the run, as does any break of more than a day for a cryptocurrency.
  - `render.py` reads the agent's draft, in which figures are named (`{{rsi14}}`) and never
    typed. It sends back a draft with a typed digit, an unknown fact or advice wording,
    inserts every figure from the data, applies the desk's decision rules and prints the
    report with a receipt. It also saves the resolved report, in which every reference is
    already text, for the page. `--verify` checks a saved report against its data.
  - `page.py` draws the finished report as one self-contained HTML page: a header band with
    four tiles, the "why" sentences, a price chart with the two averages and the levels,
    indicator meters on their real scales, the bull and the bear case as two equal cards, a
    level ladder, statement tiles, the headlines and the receipts. The page is an optional
    step: the agent draws it when its host can show or save an HTML file, or when the user
    asks, and the Markdown report is the answer either way.
  Each script carries a hash of its own text. `brief.py --preflight` checks every script the
  run uses (the download script and the page script may be left out) and runs a known-answer
  test. The scripts are readable source: one statement per line and lines of at most 100
  characters. The design and the file formats are in `docs/skill-v2/`.
  Changes from the review of version 2, before its first release:
  - No script holds a backslash-u escape any more. A host that decodes such an escape while
    it writes a script out altered the copy, and the preflight then refused it.
  - A headline can no longer end its own quotation in the report: a double quote inside a
    title is shown as a single one, and a publisher loses its round brackets.
  - A statement money figure is printed with its currency (`4.08T USD`), which is often not
    the currency of the price, and a statement figure is printed with its date wherever its
    label is printed.
  - A saved price history from an exchange that opens late in the UTC day (Sydney in its
    summer, New Zealand) stops with `E_DATES`: it carries no exchange offset, and its dates
    used to come out one day early. A saved history keeps its own symbol and currency.
  - A report from a price file says "prices from a file" where it used to claim a fetch
    time, and "close" is refused for its newest price, as for an unfinished session.
  - Numbers spelled out in words are counted as quantity words. A sign, unit or letter that
    touches a reference is refused. A hint never guesses a fact by nearness in value. The
    receipt is printed once, and a run takes at most three attempts.
  - One hash rule for the build and the three scripts that check themselves: only the stamp
    line itself is left out, and a byte-order mark counts as a change.
  - `get_data.py` gives a server error at the source its own sentence (`E_SERVER`), refuses a
    symbol with no letter or digit, and ends cleanly when its output is closed or full.
  - On the page a level's label no longer has a dash before its price, the axis never uses
    exponent notation, the words beside a meter follow the number shown, and the returns are
    named in days.
  - To make room in the scripts: the fact sheet lost its nine group headings (the facts keep
    their order), the facts `vol_sum2` and `vol_sum5` and the series row `volume` are gone,
    `E_CLOCK` is folded into `E_STALE`, the closing line that offered the full debate moved
    from `render.py` to the skill text, and `page.py` leaves the characters of its two
    inputs as they are.
- `skill/build_skill.py` is rewritten for version 2. It stamps the integrity hashes and the
  known answers (each computed by running the script's own known-answer function), lints
  every packaged file (Python 3.9 grammar, an import allowlist and file-access rules per
  script, readable source, size caps, ASCII with LF endings, the fail patterns of a
  marketplace scan, no install command and no archive address), runs the shipped preflight
  and each script's `--version`, and writes the same deterministic ZIP as before. `--check`
  writes nothing and fails on a stale hash or a stale known answer. The release pin and the
  remote check are gone, because nothing is installed from a release any more.
- `skill/LISTING.md` is rewritten for version 2: it leads with the problem, states the test
  the desk lost, describes the page part by part, says what the receipt guarantees and what
  it does not, and marks every host as tested or not tested yet. It makes no measured claim.

### Added
- `tests/skill_v2/`: tests for the four scripts and the skill text on generated numbers, an
  end-to-end test, and `test_portable.py`, the same flow with the standard library only. The
  end-to-end test runs a download through a stand-in for the network, then brief, report,
  `--verify` and page. It rebuilds the whole Markdown report from the brief and the draft,
  and compares every text on the page that holds a digit with the brief or the resolved
  report. It also runs the row shapes seen live from the data source through the whole
  chain, and changed copies of each script against the preflight. The page tests also
  compare what the page draws with the data: every point of each line, the axis, the month
  names, the level lines and the ladder.
- CI job `skill-scripts`: runs the preflight and `test_portable.py` with plain Python 3.9,
  3.13, 3.14 and 3.15 on Linux, macOS and Windows. The package job also runs `skill/build_skill.py --check`.
- `.github/workflows/canary.yml`: a nightly and manual run of the committed scripts against
  the live data source for three symbols. A refusal by the source is a notice. A brief with a
  flag, such as `prices_behind`, is an ordinary result. A changed answer format fails the job.
- `.gitattributes` keeps the skill's files LF in every checkout, and the formatter (the
  pre-commit hooks and `ruff format`) leaves the stamped scripts alone: a reformatted script
  fails its own integrity check.

### Removed
- From the skill only: the run-time install of this package, `references/research-harness.md`
  with the backtest and API-run instructions, the web-search fallback for headlines and the
  timed retry rules. The package, its CLI and the dashboard are unchanged.

### Fixed
- `cli snapshot` and `cli desk`: London and Frankfurt shares were reported as "one session
  behind" for many hours after every close, because Yahoo's daily row for the finished
  session had no close yet and was dropped. The desk now completes that bar with Yahoo's
  own last regular-market price and says so in a data note (in `notes` and in the
  prompt). It does this only for the newest row, when the price was traded on that row's
  date, the session is over by the same rules that label an unfinished bar, the price
  lies within the row's low-to-high range, and the instrument is not a cryptocurrency;
  otherwise the snapshot is flagged `prices_behind` exactly as before. Reading that row
  costs one extra Yahoo request, made only when the newest session is missing. Around a
  dividend the row is not filled, nor when Yahoo sent an empty row for a session between
  the last complete bar and that row. Backtests and every other command read prices
  as before.

## [0.2.0] - 2026-11-10

### Added
- `cli snapshot`: prints the exact market snapshot the agents see for one ticker (prices,
  indicators, returns, and for the newest bar fundamentals and headlines) as text or JSON.
  Needs no API key, so it doubles as a prompt inspector and as the data source for
  external agents.
- `cli desk`: one agent decision on the newest bar from the terminal. Saves a transcript
  and a JSON record under `results/desk/`. It exits before any LLM call when the snapshot
  is stale, prices are behind, or live data was wanted and none came back
  (`--allow-degraded` overrides).
- Data-quality reporting for the `snapshot` and `desk` commands. Prices are downloaded
  fresh for every snapshot (a download under 15 minutes old is reused) into a desk-only
  cache folder. Whether the newest bar is an unfinished session is decided on the
  exchange's own clock, cross-checked against Yahoo's last-trade time, and reported as
  `true`, `false` or unknown. An old or stale bar, prices that could not be refreshed, a
  newest session Yahoo returned no usable bar for, a non-dollar currency, and empty or
  incomplete fundamentals each produce a note, and the notes that change how the data
  must be read are written into the prompt as well (`MarketSnapshot.data_notes`).
  The dashboard's AI Trading Desk and `agents --live` do not have these guards yet: they
  still refresh prices on cache age alone and still hand a failed fundamentals fetch to
  the fundamentals analyst as text.
- Agent skill in `skill/trading-agents-desk/` (a `SKILL.md` plus role briefs and a harness
  reference) that lets Claude Code, Codex, Cursor and similar agents run the desk
  themselves from a snapshot. `skill/build_skill.py` packages it as a ZIP and refuses to
  when the release tag the skill installs from cannot be downloaded; `skill/LISTING.md`
  has marketplace listing copy.
- A `tradinglab` console command, equivalent to `python -m tradinglab.cli`, and a
  `--version` flag.

### Changed
- Caches and results resolve to `TRADINGLAB_HOME` when set, the source checkout when
  running from one (unchanged behaviour), and `~/.tradinglab` for a regular `pip install`,
  which previously wrote inside the interpreter's `lib` directory.
- The fundamentals block labels debt/equity as a percentage (`Debt/equity (%)`). Yahoo
  quotes it in percent, so the bare figure read as a leverage multiple a hundred times too
  large. This changes the live prompts of `agents --live` and the dashboard desk.
- Headlines are collapsed to a single line of at most 200 characters, so third-party text
  cannot pose as a section of the prompt.
- An empty price download raises `PriceDataError` (still a `ValueError`) whose message and
  `reason` say whether Yahoo was rate-limiting or did not know the symbol, when Yahoo said
  which. It used to blame the symbol in every case.
- A price cache directory that cannot be written is a warning, not an error: prices that
  were downloaded are still returned.
- Commands that save results (`baselines`, `train-ml`, `agents`, `probe`, `desk`) check at
  start-up that the results folder is really writable and exit with one line naming
  `TRADINGLAB_HOME` if it is not; `download` checks its cache folder the same way.
  `snapshot` and `report` no longer need a writable folder at all.
- The quarterly revenue and net income lines name the currency the company reports in
  when Yahoo provides it (`Quarterly revenue (USD, newest first)`).

### Fixed
- `--anonymize` no longer rewrites the tables for tickers that are also words or labels.
  The ticker scrub ran over the whole block, so ticker `T` turned row label `T-9` into
  `TICKER-X-9` and ticker `LOW` erased the `low` column. It now touches only the
  third-party text (fundamentals and headlines).

## [0.1.0] - 2026-07-05

### Added
- Faithful reimplementation of the multi-agent LLM trading framework from *TradingAgents*
  (Xiao et al., arXiv:2412.20138): four analysts feed a bull-vs-bear research debate, a
  trader synthesizes a decision, a risk-management trio debates it, and a fund manager
  approves the final trade.
- Leakage-safe evaluation harness with an anti-leakage prompt clause on every agent and an
  `--anonymize` mode that masks ticker names and dates and rebases prices to an index
  (first close = 100) with volume as percent-of-average, so no raw price or volume level
  reaches the prompt.
- Memorization probe (`cli probe`) that measures training-data leakage per window by asking
  the model to recall closing prices and monthly direction, scored against a naive
  carry-forward anchor and a coin-flip baseline (CONTAMINATED / LIKELY CLEAN / INCONCLUSIVE).
- Backtest engine with no-lookahead by construction (t+1 signal shift) and always-on 10 bps
  per-unit-turnover transaction costs, validated by perturbation-based property tests.
- The paper's five rule baselines plus a LightGBM walk-forward model (expanding window,
  embargoed splits) evaluated over 11.5 years of data across 10 tickers.
- Streamlit trader dashboard (`app.py`) with Results, AI Trading Desk, Paper Portfolio, and
  Transcripts pages, plus paper-style figures reproducing the original Fig. 6/7 layouts.
- Disk-cached LLM responses (`cache/llm/`) for free, deterministic reruns, per-decision
  markdown transcripts for explainability, and a 62-test pytest suite including no-lookahead
  perturbation tests.

[Unreleased]: https://github.com/Kantamaniprakash/trading-agents-lab/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Kantamaniprakash/trading-agents-lab/releases/tag/v0.2.0
[0.1.0]: https://github.com/Kantamaniprakash/trading-agents-lab/releases/tag/v0.1.0
