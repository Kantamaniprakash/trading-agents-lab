# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
  `TRADINGLAB_HOME` if it is not. `snapshot`, `download` and `report` no longer need it.
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

[0.2.0]: https://github.com/Kantamaniprakash/trading-agents-lab/releases/tag/v0.2.0
[0.1.0]: https://github.com/Kantamaniprakash/trading-agents-lab/releases/tag/v0.1.0
