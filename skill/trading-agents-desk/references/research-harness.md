# Research harness: backtests and leakage checks

Use this when the user asks whether the desk works, wants a backtest, or
wants to compare against simpler strategies. Everything runs through
`~/.tradinglab/venv/bin/tradinglab <command>` (see Setup in `SKILL.md`).
Output tables print to the terminal. CSV files, figures and transcripts are
saved under `~/.tradinglab/results/`.

## Why a naive backtest of an LLM is wrong

A language model has usually read about the period you are testing. Asking it
to "trade" January 2024 mostly measures what it remembers. The harness fixes
this three ways, and you should use them in this order.

1. **Probe the window.** Ask the model to recall prices and monthly direction
   with no data in the prompt. This measures the leak instead of assuming it.
2. **Anonymize.** Mask the ticker and dates, and rebase prices to an index
   that starts at 100, so the model is much less likely to recognise the
   stock. The shape of the price path can still give it away: this reduces
   leakage, it does not remove it.
3. **Prefer recent windows.** Only data after the model's knowledge cutoff is
   fully clean.

## Commands

No API key needed:

```bash
~/.tradinglab/venv/bin/tradinglab download
```

```bash
~/.tradinglab/venv/bin/tradinglab baselines
```

```bash
~/.tradinglab/venv/bin/tradinglab train-ml
```

- `download` caches daily prices for the 10-ticker default universe from
  2015. Pass `--tickers` to change it.
- `baselines` runs buy-and-hold, MACD, KDJ+RSI, mean reversion and SMA cross
  over the full history.
- `train-ml` runs a walk-forward LightGBM model with embargoed splits, tested
  from 2021.

The next two need `ANTHROPIC_API_KEY` and spend the user's API credit. Work
out the cost and ask before each run.

```bash
~/.tradinglab/venv/bin/tradinglab probe --ticker AAPL --start 2024-01-02 --end 2024-03-28
```

```bash
~/.tradinglab/venv/bin/tradinglab agents --ticker AAPL --start 2026-03-01 --end 2026-06-01 --every 5 --anonymize
```

- `probe` prints a verdict: CONTAMINATED, LIKELY CLEAN or INCONCLUSIVE. It
  costs a few cents.
- `agents` backtests the desk. One decision costs roughly $0.20 to $0.50.
  Without `--every` it decides on every trading day, about 63 decisions and
  $13 to $32 for one quarter. Always pass `--every` (5 decides weekly) or
  `--max-days N` (caps the number of decisions), multiply the number of
  decisions by the cost, and tell the user the total before running. A
  repeat of the same historical window is free, because every response is
  cached on disk and past prices do not change.
- `download` and `train-ml` cost nothing but are not instant. Say so before
  starting them.

This one reprints the saved result tables and costs nothing:

```bash
~/.tradinglab/venv/bin/tradinglab report
```

## Rules the engine enforces

- **No lookahead.** A signal computed at the close of day `t` earns returns
  from day `t+1`.
- **Costs always on.** 10 basis points per unit of turnover. Change with
  `--cost-bps`.
- **Sharpe ratios** are measured against a 4% risk-free rate.

## Reference results (from the package's repository)

Agent run on AAPL with the ticker and dates masked, 1 March to 1 June 2026,
64 trading days. It is a reduced setup: price data only, so of the four
analysts only the market analyst ran. It used the older masking, in which
real price levels were still visible, and no probe result is published for
this window.

| Strategy | Return | Sharpe | Max drawdown |
|---|---:|---:|---:|
| Agent desk | -0.9% | -1.21 | 4.4% |
| Buy and hold | +15.7% | 2.66 | 6.9% |

Rule baselines, equal-weight 10 tickers, 2015 to mid-2026:

| Strategy | Annual return | Sharpe |
|---|---:|---:|
| Buy and hold | +29.5% | 1.08 |
| SMA cross | +4.4% | 0.10 |
| MACD | -4.8% | -0.41 |
| Mean reversion | -9.6% | -0.77 |
| KDJ+RSI | -11.2% | -0.84 |

## How to report a backtest

- Lead with the comparison against buy-and-hold over the same window, net of
  costs.
- State the number of trading days. Under about 250 days a Sharpe gap is
  directional evidence, not statistical proof.
- Report the probe verdict and whether the run was anonymized.
- Name the limits that apply: the default universe is today's large winners
  (survivorship bias), signals are single-asset, and there is no borrow cost
  or market-impact model.
- Never describe a result as proof that the strategy will make money.
