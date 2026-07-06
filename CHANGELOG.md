# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
