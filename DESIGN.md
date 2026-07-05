# trading-agents-lab — Design Document

A rigorous research implementation of **TradingAgents: Multi-Agents LLM Financial Trading
Framework** (Xiao et al., arXiv:2412.20138), rebuilt with the methodological rigor the paper
lacks, plus a classical ML alpha stack so we can answer: *does the LLM layer add anything
beyond a well-tuned quant baseline?*

## 1. Research principles (non-negotiable)

1. **No lookahead.** Every signal at date `t` uses only information available at the close of
   `t`. The backtest engine enforces this by shifting positions one day. Features must never
   reference future rows. Labels are strictly forward.
2. **LLM knowledge-cutoff leakage is a first-class threat.** The paper backtests GPT-4o on
   Jan–Mar 2024, which is inside its training data. We mitigate with (a) an anti-leakage
   system-prompt clause, (b) an `--anonymize` mode that masks ticker names and dates AND rebases
   prices to an index (start = 100) with volumes as percent-of-average, so the LLM can
   neither recall memorized price paths nor re-identify the asset from raw levels, and (c) documentation that only post-cutoff
   evaluation is trustworthy.
3. **Transaction costs always on.** Default 10 bps per unit turnover.
4. **Honest reporting.** Multi-year evaluation for rule/ML strategies; per-ticker and
   portfolio metrics; no cherry-picking.
5. **Explainability.** Every agent decision produces a full markdown transcript.

## 2. Directory layout

```
trading-agents-lab/
├── DESIGN.md                    # this file
├── README.md                    # project overview + headline results
├── LICENSE
├── pyproject.toml
├── requirements.txt
├── data/cache/                  # parquet price cache
├── cache/llm/                   # LLM response disk cache (json files)
├── results/                     # metrics tables, equity plots, agent transcripts
│   ├── figures/                 # README figures (rendered by scripts/make_figures.py)
│   └── transcripts/             # per-decision-day agent debate transcripts
├── scripts/make_figures.py      # regenerates the README figures from results + cache
├── src/tradinglab/
│   ├── __init__.py
│   ├── config.py                # central path constants + config dataclasses
│   ├── data/{__init__,prices,fundamentals,news}.py
│   ├── features/{__init__,indicators,dataset}.py
│   ├── models/{__init__,ml}.py
│   ├── backtest/{__init__,metrics,engine,baselines}.py
│   ├── agents/{__init__,llm,state,prompts,roles,graph}.py
│   └── cli.py
└── tests/test_{indicators,metrics,engine,dataset,agents_backtest}.py
```

## 3. Shared conventions (every module MUST follow these)

- Python 3.10. Dependencies: pandas, numpy, yfinance, lightgbm, scikit-learn, matplotlib,
  pyarrow, anthropic. Nothing else.
- Absolute imports: `from tradinglab.backtest.metrics import compute_metrics`.
- **Price DataFrame schema** (the canonical `df` passed everywhere): one ticker per frame,
  `DatetimeIndex` (tz-naive, sorted ascending, business days), lowercase columns:
  `open, high, low, close, volume`. Prices are **auto-adjusted** (yfinance `auto_adjust=True`),
  so `close` is the adjusted close. Multi-ticker collections are `dict[str, pd.DataFrame]`.
- **Signal semantics**: a signal is a `pd.Series` (float, index ⊆ df.index) where
  `signal[t] ∈ [-1, +1]` is the *desired position* computed with data up to and including the
  close of `t`. `+1` = fully long, `-1` = fully short, `0` = flat.
- **Engine shift discipline**: the engine (and ONLY the engine) converts signals to positions
  via `position = signal.shift(1).fillna(0.0)`. Held position during day `t` earns
  `close[t]/close[t-1] - 1`. Strategies never shift themselves.
- **Costs**: `cost[t] = (cost_bps / 1e4) * |position[t] - position[t-1]|`, subtracted from
  gross return.
- Every public function gets a concise docstring. No prints in library code (CLI prints).
- Windows-friendly: use `pathlib.Path`, UTF-8 (`open(..., encoding="utf-8")`) everywhere.

## 4. config.py (reference)

Dataclasses `DataConfig`, `BacktestConfig`, `MLConfig`, `AgentConfig`, `LabConfig` with
`PROJECT_ROOT`, `DATA_CACHE_DIR`, `LLM_CACHE_DIR`, `RESULTS_DIR` path constants.
Every module reads its configuration from `src/tradinglab/config.py`; nothing else
defines paths or tunables.

## 5. Module contracts

### 5.1 data/prices.py
```python
def fetch_prices(tickers: list[str] | str, start: str, end: str | None = None,
                 cache_dir: Path | None = None, force: bool = False) -> dict[str, pd.DataFrame]
```
- Downloads daily OHLCV via `yfinance.download(ticker, start=..., end=..., auto_adjust=True,
  progress=False)`. Normalizes to the canonical schema (lowercase cols, tz-naive index,
  drop rows with any NaN in ohlcv, sort index). Handles yfinance sometimes returning
  MultiIndex columns (flatten by taking level 0 or the ticker level, whichever holds
  open/high/low/close/volume).
- Caches per ticker to `{cache_dir}/{ticker}_{start}_{end}.parquet`; `force=True` re-downloads.
  When `end is None`, use the string "latest" in the cache filename and consider the cache
  stale after 3 days (compare file mtime).
- Raises `ValueError` with a clear message if a ticker returns no data.
```python
def load_universe(cfg: DataConfig) -> dict[str, pd.DataFrame]   # convenience wrapper
```

### 5.2 data/fundamentals.py
```python
def fetch_fundamentals_snapshot(ticker: str) -> dict   # best-effort, live-mode only
```
- Uses `yfinance.Ticker(ticker)`: `.info` (trailingPE, forwardPE, marketCap, profitMargins,
  returnOnEquity, debtToEquity, revenueGrowth, earningsGrowth — guard every key with .get),
  and quarterly income statement revenue/net-income trend if available. Wrap everything in
  try/except; return `{"available": False, "error": str(e)}` on failure.
```python
def format_fundamentals(snapshot: dict) -> str   # human-readable block for agent prompts
```
- Document clearly: yfinance fundamentals are CURRENT, not point-in-time; using them in a
  historical backtest leaks future data, so the agent backtest only injects fundamentals when
  running in live mode.

### 5.3 data/news.py
```python
def fetch_current_news(ticker: str, limit: int = 8) -> list[dict]  # [{"title","publisher","date"}]
```
- `yfinance.Ticker(ticker).news`; tolerate schema variations (keys may be nested under
  "content"); best-effort, return [] on failure. Live-mode only; same leak warning as above.

### 5.4 features/indicators.py — pure pandas, no external TA library
Individual functions (each takes the canonical `df`, returns `pd.Series` aligned to df.index,
NaN for warm-up rows, **rolling/expanding logic only — no negative shifts**):
```python
sma(close, n); ema(close, n); rsi(close, n=14)
macd(close, fast=12, slow=26, signal=9) -> pd.DataFrame  # cols: macd, macd_signal, macd_hist
bollinger(close, n=20, k=2.0) -> pd.DataFrame            # cols: bb_mid, bb_upper, bb_lower, bb_pctb
atr(df, n=14); adx(df, n=14); cci(df, n=20)
stochastic(df, n=14, d=3) -> pd.DataFrame                # cols: stoch_k, stoch_d, kdj_j (=3K-2D)
obv(df); vwma(df, n=20); mfi(df, n=14)
roc(close, n=10); williams_r(df, n=14)
```
Standard textbook formulas (RSI = Wilder's smoothing via `ewm(alpha=1/n, adjust=False)`;
ADX = Wilder; CCI with 0.015 constant; MFI from typical price × volume).
```python
def compute_indicator_frame(df: pd.DataFrame) -> pd.DataFrame
```
Returns a DataFrame with columns exactly:
`sma_10, sma_50, sma_200, ema_12, ema_26, rsi_14, macd, macd_signal, macd_hist, bb_mid,
bb_upper, bb_lower, bb_pctb, atr_14, adx_14, cci_20, stoch_k, stoch_d, kdj_j, obv, vwma_20,
mfi_14, roc_10, willr_14`.

### 5.5 features/dataset.py
```python
FEATURE_COLUMNS: list[str]   # module-level constant listing every feature name produced

def build_features(df: pd.DataFrame) -> pd.DataFrame
```
Scale-free features only (no raw price levels). Exactly these columns:
- `ret_1, ret_5, ret_10, ret_21` — past simple returns over those windows
- `vol_5, vol_21` — std of daily returns over window
- `close_over_sma10, close_over_sma50, close_over_sma200` — close/sma − 1
- `rsi_14` (scaled to [0,1] by /100), `bb_pctb`, `macd_hist_norm` (macd_hist / close),
  `atr_norm` (atr_14 / close), `adx_14` (/100), `cci_20` (/100, clipped to [-3,3]),
  `stoch_k` (/100), `mfi_14` (/100), `willr_14` (/-100 → [0,1]), `roc_10` (/100)
- `vlm_z21` — volume z-score vs trailing 21d mean/std
- `dist_52w_high` — close / rolling 252d max(close) − 1; `dist_52w_low` analog with min
- `dow` — day of week as int 0–4
```python
def build_dataset(prices: dict[str, pd.DataFrame], horizon: int = 1
                  ) -> tuple[pd.DataFrame, pd.Series, pd.Series]
```
- Pools tickers. Returns `(X, y, y_bin)` with MultiIndex `(ticker, date)`.
- `y[t] = close[t+horizon]/close[t] - 1` (via `shift(-horizon)` on close BEFORE alignment);
  `y_bin = (y > 0)` as int. Drop rows where any feature or label is NaN.
- CRITICAL: features at row `t` must not change if rows > `t` are modified (tests verify).

### 5.6 backtest/metrics.py
```python
def equity_curve(returns: pd.Series) -> pd.Series          # (1+r).cumprod()
def max_drawdown(equity: pd.Series) -> float                # positive fraction, e.g. 0.23
def compute_metrics(returns: pd.Series, rf: float = 0.0,
                    periods_per_year: int = 252) -> dict
```
Returns dict with keys: `n_days, cumulative_return, annualized_return, annualized_vol,
sharpe, sortino, max_drawdown, calmar, hit_rate`.
- `annualized_return` = (1+cum)^(252/n) − 1; `sharpe` = (mean(r − rf_daily)/std(r)) · √252
  with `rf_daily = rf/252`; sortino uses textbook downside deviation about zero:
  `dd = sqrt(mean(min(excess, 0)^2))`, `sortino = mean(excess)/dd · √252`;
  calmar = ann_return / max_drawdown; hit_rate = fraction of non-zero-return days with r > 0.
  Drawdown inside compute_metrics is measured from initial capital (inception point 1.0
  prepended to the equity series), so losses before a new high count.
  Division-by-zero cases (zero vol, zero drawdown, no non-zero-return days, empty
  input) return np.nan.
```python
def metrics_table(named_returns: dict[str, pd.Series], rf: float = 0.0) -> pd.DataFrame
```

### 5.7 backtest/engine.py
```python
@dataclass
class BacktestResult:
    name: str
    positions: pd.Series
    gross_returns: pd.Series
    net_returns: pd.Series
    equity: pd.Series           # from net returns
    turnover: pd.Series
    metrics: dict               # compute_metrics(net_returns, rf)

def backtest_signals(df: pd.DataFrame, signal: pd.Series, cost_bps: float = 10.0,
                     rf: float = 0.0, allow_short: bool = True,
                     name: str = "strategy") -> BacktestResult
```
- Reindex signal to df.index, ffill, fillna(0), clip to [-1,1]; if not allow_short clip to
  [0,1]. `position = signal.shift(1).fillna(0)`. Market return from `close.pct_change()`.
  First row return = 0. Turnover = `position.diff().abs().fillna(position.abs())` (the first
  position incurs entry cost).
```python
def backtest_portfolio(results: dict[str, BacktestResult], rf: float = 0.0,
                       name: str = "portfolio") -> BacktestResult
```
- Equal-weight average of per-ticker **net** returns on the union index (missing → 0 for
  that ticker that day, average over all tickers present in the dict). Positions/turnover:
  cross-ticker mean (documented as approximate).

### 5.8 backtest/baselines.py — the paper's five baselines
Each: `(df: pd.DataFrame) -> pd.Series` (signal per §3 semantics; may take extra kwargs
with defaults).
```python
def buy_and_hold(df) -> +1 everywhere
def macd_strategy(df)              # +1 while macd > macd_signal else -1
def kdj_rsi_strategy(df)           # +1 when rsi_14 < 30 or kdj_j < 20; -1 when rsi_14 > 70
                                   # or kdj_j > 80; hold prior signal otherwise (ffill, start 0)
def zmr_strategy(df, window=20, entry_z=1.0, exit_z=0.3)
                                   # z = (close - sma(window)) / rolling std(window);
                                   # -1 when z > entry_z, +1 when z < -entry_z,
                                   # 0 when |z| < exit_z, else hold prior (ffill)
def sma_cross_strategy(df, fast=10, slow=50)   # +1 when sma_fast > sma_slow else -1
STRATEGIES: dict[str, Callable]    # {"buy_hold","macd","kdj_rsi","zmr","sma_cross"}
```
Use indicators from features/indicators.py. NaN warm-up rows → signal 0.

### 5.9 models/ml.py
```python
@dataclass
class WalkForwardResult:
    predictions: pd.Series        # MultiIndex (ticker, date), only test-period rows
    feature_importance: pd.DataFrame  # index=feature, cols=[gain] averaged over refits
    n_refits: int

def walk_forward_predict(X: pd.DataFrame, y: pd.Series, cfg: MLConfig) -> WalkForwardResult
```
- Expanding-window walk-forward: sort unique dates; first training window = all dates <
  `cfg.test_start`; then for each chunk of `cfg.retrain_step` trading dates, train on all
  data up to (chunk_start − `cfg.embargo_days` trading days) and predict the chunk.
  LightGBM `LGBMRegressor(n_estimators=400, learning_rate=0.05, num_leaves=31,
  subsample=0.8, colsample_bytree=0.8, min_child_samples=50, random_state=42, verbose=-1)`.
- Dates are the MultiIndex level "date"; train/test masks are by date across all tickers.
```python
def predictions_to_signals(predictions: pd.Series, threshold: float = 0.0,
                           long_only: bool = False) -> dict[str, pd.Series]
```
- Per ticker: `+1 if pred > threshold else (-1 if pred < -threshold else 0)`;
  long_only maps −1 → 0. threshold is in return space (e.g. 0.0005 ≈ 5 bps).

### 5.10 agents/llm.py
```python
class LLMClient:
    def __init__(self, cfg: AgentConfig, cache_dir: Path | None = None): ...
    def chat(self, system: str, user: str, model: str | None = None,
             max_tokens: int | None = None) -> str
    def quick(self, system: str, user: str) -> str    # model = cfg.quick_model
    def deep(self, system: str, user: str) -> str     # model = cfg.deep_model
    @property
    def usage(self) -> dict   # {model: {"calls", "input_tokens", "output_tokens", "cache_hits"}}
```
- `anthropic.Anthropic()` (env `ANTHROPIC_API_KEY` / ambient auth). On construction, do NOT
  fail if key missing; fail with a clear RuntimeError on first `.chat()` call instead.
- **Never pass `temperature`** (removed on Opus 4.8 — returns 400). Just
  `client.messages.create(model=..., max_tokens=..., system=..., messages=[{"role":"user",
  "content": user}])`, then join all `block.text` for blocks with `type == "text"`.
- Disk cache: key = sha256 of `f"{model}|{system}|{user}"`, file
  `cache/llm/{key}.json` storing `{"model","response","input_tokens","output_tokens"}`.
  Cache hit returns immediately and increments `cache_hits`. This makes backtests
  deterministic and re-runnable for free.
- Retry: SDK already retries 429/5xx twice; construct with `max_retries=4`. Additionally
  catch `anthropic.APIConnectionError` and retry up to 2 times with 5s sleep.
```python
def extract_json(text: str) -> dict   # tolerant: find first '{'..matching '}' and json.loads;
                                      # strip ```json fences; raise ValueError if unparseable
```

### 5.11 agents/state.py — the structured communication protocol
```python
@dataclass
class MarketSnapshot:
    ticker: str
    date: str                    # "YYYY-MM-DD"
    price_table: str             # last 10 rows OHLCV as fixed-width text
    indicator_report: str        # current values of key indicators, formatted text
    returns_summary: str         # 1d/5d/21d/63d returns, vol, 52w-high distance
    fundamentals: str | None = None
    news: list[str] | None = None
    anonymize: bool = False

    def context_block(self) -> str
```
- `context_block()` renders everything into one text block. When `anonymize=True`, replace
  the ticker with `"TICKER-X"` and the date with `"Day T"` **everywhere** (the builders in
  graph.py already produce masked tables in that case; this class just picks which header
  to render).
```python
@dataclass AnalystReport:  role: str; summary: str
@dataclass DebateTurn:     speaker: str; text: str
@dataclass DebateResult:   transcript: list[DebateTurn]; verdict: str; rationale: str
@dataclass TraderDecision: action: str; size: float; rationale: str   # action ∈ BUY/SELL/HOLD
@dataclass RiskVerdict:    approved: bool; final_action: str; final_size: float; rationale: str
@dataclass
class AgentDayLog:
    ticker: str; date: str
    reports: list[AnalystReport]; debate: DebateResult
    decision: TraderDecision; risk: RiskVerdict
    final_action: str; final_size: float
    def to_markdown(self) -> str    # full readable transcript with sections per team
```

### 5.12 agents/prompts.py
Module-level string constants. Every system prompt ends with `ANTI_LEAKAGE`:
```python
ANTI_LEAKAGE = (
  "\n\nCRITICAL CONSTRAINT: Base your analysis ONLY on the data provided above. "
  "You must behave as if you have no knowledge of any market events, prices, or news "
  "after the analysis date. Do not use memorized knowledge of what happened to this or "
  "any related stock. Treat this as out-of-sample data.")
```
Prompts (concise, 5–10 sentences each, professional): `MARKET_ANALYST_SYSTEM` (technical
analysis of the indicator report), `FUNDAMENTALS_ANALYST_SYSTEM`, `NEWS_ANALYST_SYSTEM`,
`SENTIMENT_ANALYST_SYSTEM`, `BULL_SYSTEM`, `BEAR_SYSTEM` (debaters: argue position, rebut
opponent's last argument, cite data), `FACILITATOR_SYSTEM` (judge debate, output JSON
`{"verdict": "BULLISH|BEARISH|NEUTRAL", "rationale": "..."}`), `TRADER_SYSTEM` (output JSON
`{"action": "BUY|SELL|HOLD", "size": 0.0-1.0, "rationale": "..."}`), `RISKY_SYSTEM`,
`SAFE_SYSTEM`, `NEUTRAL_SYSTEM` (risk debaters), `FUND_MANAGER_SYSTEM` (output JSON
`{"approved": true|false, "final_action": "...", "final_size": 0.0-1.0, "rationale": "..."}`).

### 5.13 agents/roles.py
```python
def run_analysts(client, snapshot) -> list[AnalystReport]
```
- Market analyst via `client.deep`; fundamentals analyst only if snapshot.fundamentals;
  news+sentiment analysts only if snapshot.news; those three via `client.quick`.
```python
def run_research_debate(client, snapshot, reports, n_rounds) -> DebateResult
```
- Bull and bear alternate (`client.deep`), each sees the analyst reports + debate so far;
  n_rounds full exchanges; facilitator (`client.deep`) returns the JSON verdict.
  Parse with `extract_json`; on ValueError retry once appending
  "Respond with ONLY the JSON object."; on second failure default NEUTRAL.
```python
def run_trader(client, snapshot, reports, debate) -> TraderDecision
def run_risk_debate(client, snapshot, decision, n_rounds) -> RiskVerdict
```
- Same JSON-parse-retry pattern. Risk team: risky/safe/neutral each comment once per round
  (`client.quick`), fund manager (`client.deep`) issues final JSON. Clamp size to [0,1];
  invalid action → HOLD. If fund manager not approved → final = HOLD/0.

### 5.14 agents/graph.py
```python
def snapshot_from_history(df, date, ticker, lookback=60, anonymize=False,
                          fundamentals=None, news=None) -> MarketSnapshot
```
- Uses ONLY `df.loc[:date]`; an assertion enforces that the slice contains no future
  rows. Builds: price table (last 10 rows, rounded);
  indicator report from `compute_indicator_frame` latest row (RSI, MACD vs signal, %B,
  ATR, ADX, CCI, stoch K/D, close vs SMA10/50/200, MFI); returns summary (1/5/21/63-day
  returns, 21d annualized vol, distance from 52w high/low). When anonymize=True the
  ENTIRE working slice is rebased before anything is rendered: OHLC is scaled by
  `k = 100 / close` at the first displayed row (so the first displayed close is exactly
  100.0 and prices are index levels) and volume is replaced by relative volume
  (% of the slice's mean, 100 = average day). The price table, indicator report and
  returns summary are all computed from the rebased frame, so every number is internally
  consistent — scale-free indicators (RSI/ADX/%B/stoch/MFI, returns, vol) are unchanged
  by construction while price-level ones (SMA/MACD/Bollinger/ATR) scale with k. The
  table is labeled "indexed (start = 100)" / "% of average", the index is renamed to
  "T-9".."T-0", and no real dates/ticker/price levels/volume magnitudes appear anywhere.
```python
class TradingAgentsPipeline:
    def __init__(self, client: LLMClient, cfg: AgentConfig): ...
    def decide(self, snapshot) -> AgentDayLog   # analysts → debate → trader → risk → final
```
```python
def run_agent_backtest(df, ticker, start, end, client, cfg, out_dir=None, every=1,
                       anonymize=False, cost_bps=10.0, rf=0.0, long_only=False,
                       fundamentals=None, news=None,
                       max_days=None) -> tuple[BacktestResult, list[AgentDayLog]]
```
- Iterate trading dates in [start, end] taking every `every`-th date. For each: build
  snapshot (history only), `pipeline.decide`, map action → signal value
  (BUY→+final_size, SELL→(−final_size if not long_only else 0), HOLD→np.nan i.e. carry
  previous via engine ffill). Assemble the sparse signal Series over the full df.index
  restricted to [start, end], run `backtest_signals`. Save each day log as
  `{out_dir}/{ticker}_{date}.md` when out_dir given. Print one progress line per decision
  day (this is CLI-adjacent, allowed here).

### 5.15 cli.py
argparse with subcommands (all print readable tables and save CSV/PNG to results/):
- `download` — fetch universe, print row counts per ticker.
- `baselines [--tickers ...] [--start] [--end] [--cost-bps]` — run all 5 baselines per
  ticker + equal-weight portfolio; print metrics_table; save `results/baselines_metrics.csv`
  and equity-curve PNG per ticker (matplotlib, all strategies on one axes, `Agg` backend).
- `train-ml [--horizon] [--threshold] [--long-only]` — build dataset, walk-forward,
  signals, backtests vs buy_hold benchmark over the SAME test window; print metrics table +
  top-15 feature importances; save `results/ml_metrics.csv`, `results/ml_equity_{ticker}.png`,
  `results/ml_predictions.csv`.
- `agents --ticker AAPL --start ... --end ... [--every 1] [--anonymize] [--live]
  [--long-only] [--max-days N]` — needs API key; runs agent backtest; `--live` additionally
  fetches fundamentals+news (only sensible when end ≈ today); prints final metrics and
  token usage/cost summary; transcripts to `results/transcripts/`.
- `report` — load saved CSVs and print a combined summary.
main() entry: `python -m tradinglab.cli <cmd>`.

## 6. Tests (pytest; `pythonpath = ["src"]` configured in pyproject)

- **test_indicators.py**: sma/ema exact values on a small hand-computed series; RSI in
  [0,100] and equals 100 on a strictly rising series (after warm-up); bollinger mid == sma20.
  **No-lookahead property**: for a random-walk df (seeded), compute_indicator_frame, then
  modify the last 30 rows and recompute — all indicator values on the untouched prefix
  (minus last 30) must be identical (`assert_frame_equal` on the prefix).
- **test_metrics.py**: constant positive returns → sharpe > 0 and equals mean/std·√252
  analytically for a known small series; equity of [0.1, -0.05] == [1.1, 1.045];
  max_drawdown of a constructed peak-trough series equals known value; empty/zero-vol guarded.
- **test_engine.py**: signal +1 at t affects return only from t+1 (construct 5-day df with
  known closes; assert exact net returns incl. 10 bps entry cost); costs: flipping +1→−1
  charges 2× turnover cost; buy_and_hold equity ≈ price relative net of one entry cost;
  long_only clip works.
- **test_dataset.py**: labels use the future (y at t equals close[t+1]/close[t]−1 for
  horizon=1 on a known series); **feature no-lookahead**: perturb future rows, features on
  the prefix unchanged; no NaNs in returned X/y.
- **test_agents_backtest.py**: live fundamentals/news reach the agent snapshots;
  `--max-days` caps decision count without shrinking the evaluation window; CLI rejects
  non-positive `--every`/`--max-days`; price download end date is inclusive — all with
  the agent pipeline stubbed and yfinance monkeypatched (no network, no API calls).
- Tests must not hit the network — construct synthetic DataFrames (seeded RNG, ~400 rows,
  business-day index).

## 7. Cost model for the agent layer (documentation)

Per decision day ≈ 2 deep + ~2 quick calls minimum (no news/fundamentals in historical
mode): market analyst (deep), bull/bear ×n_rounds (deep), facilitator (deep), trader (deep),
risk trio (quick ×3), fund manager (deep). With defaults (2 debate rounds) ≈ 8 deep + 3
quick calls ≈ 15–25k input tokens/day. Use `--every 5` and `--max-days` to bound cost; the
disk cache makes repeat runs free.
