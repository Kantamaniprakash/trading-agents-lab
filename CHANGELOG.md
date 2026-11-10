# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `cli snapshot`: prints the exact market snapshot the agents see for one ticker (prices,
  indicators, returns, and for the latest close fundamentals and headlines) as text or
  JSON. Needs no API key, so it doubles as a prompt inspector and as the data source for
  external agents.
- `cli desk`: one-shot agent decision on the latest close from the terminal, matching the
  dashboard's AI Trading Desk. Writes a transcript and a JSON record to `results/desk/`.
- Agent skill in `skill/trading-agents-desk/` (a `SKILL.md` plus role briefs and a harness
  reference) that lets Claude Code, Codex, Cursor and similar agents run the desk
  themselves from a snapshot, with `skill/build_skill.py` to package it as a ZIP and
  `skill/LISTING.md` with marketplace listing copy.
- Data-quality notes on every desk snapshot: a partial intraday bar, a stale latest bar,
  and missing or incomplete fundamentals or headlines are reported instead of silently
  analysed. The ones that change how the data must be read are also written into the
  prompt (`MarketSnapshot.data_notes`), so the agents see them as well as the caller.
- Desk snapshots retry Yahoo once when it answers with an empty payload, which is how it
  responds to rate-limited requests.

### Changed
- Caches and results resolve to `TRADINGLAB_HOME` when set, the source checkout when
  running from one (unchanged behaviour), and `~/.tradinglab` for a regular `pip install`,
  which previously wrote inside the interpreter's `lib` directory.

### Fixed
- Desk snapshots re-download a cached price file whose last bar was captured mid-session,
  so an intraday reading is never served later as that day's close.
- A failed fundamentals fetch is no longer passed to the fundamentals analyst as if it
  were a fundamentals block (desk path only).
- The fundamentals block labels debt/equity as a percentage. Yahoo quotes it in percent,
  so the unlabelled figure read as a leverage multiple a hundred times too large.

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

[0.1.0]: https://github.com/Kantamaniprakash/trading-agents-lab/releases/tag/v0.1.0
