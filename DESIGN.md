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
├── data/cache/                  # parquet price cache (desk/ holds one-shot desk downloads)
├── cache/llm/                   # LLM response disk cache (json files)
├── results/                     # metrics tables, equity plots, agent transcripts
│   ├── figures/                 # README figures (rendered by scripts/make_figures.py)
│   ├── transcripts/             # per-decision-day agent debate transcripts (backtests)
│   └── desk/                    # one-shot `desk` runs: transcript + JSON (git-ignored)
├── scripts/make_figures.py      # regenerates the README figures from results + cache
├── skill/
│   ├── trading-agents-desk/     # agent skill: SKILL.md + references/ (text only)
│   ├── build_skill.py           # validates the skill and writes dist/<name>.zip
│   └── LISTING.md               # marketplace listing copy + upload checklist
├── src/tradinglab/
│   ├── __init__.py
│   ├── config.py                # central path constants + config dataclasses
│   ├── data/{__init__,prices,fundamentals,news}.py
│   ├── features/{__init__,indicators,dataset}.py
│   ├── models/{__init__,ml}.py
│   ├── backtest/{__init__,metrics,engine,baselines}.py
│   ├── agents/{__init__,llm,state,prompts,roles,graph,desk,probe}.py
│   └── cli.py
└── tests/test_{indicators,metrics,engine,dataset,agents_backtest,probe,desk,skill_package}.py
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

`PROJECT_ROOT` (the parent of the caches and `results/`) resolves in this order:
`TRADINGLAB_HOME` when set; the source checkout when the package runs from one
(recognised by this project's layout: the package sits in `src/` next to a
`pyproject.toml`); otherwise `~/.tradinglab`. The last case is a regular `pip install`,
where the checkout-relative path would land inside the interpreter's `lib` directory.
The `src/` requirement keeps a copy vendored into another project (`pip install
--target`) from treating that project as its checkout.

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
- An empty download raises `PriceDataError(ValueError)` with `reason` in `rate_limited`,
  `not_found`, `unknown`. Yahoo answers a rate limit and an unknown symbol with the same
  empty frame, so the reason is recovered from what yfinance logs during the download
  (a temporary handler on the `yfinance` logger). Only Yahoo's own statements count as
  `not_found` ("Quote not found", "No data found, symbol may be delisted"). yfinance's
  catch-all "possibly delisted" texts do not: it logs those for timeouts, resets and 5xx
  responses on valid symbols, which must be retried, not blamed on the symbol.
- Cache writes are best-effort: an `OSError` from `mkdir` or `to_parquet` becomes a
  `RuntimeWarning` naming `TRADINGLAB_HOME`, and the downloaded frame is still returned.

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
def missing_ratio_fields(snapshot: dict) -> list[str]   # labels of ratios with no number
```
- Debt/equity is printed as `Debt/equity (%)`: Yahoo quotes it in percent, and an
  unlabelled 84.29 reads as 84x leverage.
- One table (`_RATIOS`: snapshot key → yfinance key, label) feeds both the fields that are
  fetched and the labels reported as missing, so the two cannot drift.
- Document clearly: yfinance fundamentals are CURRENT, not point-in-time; using them in a
  historical backtest leaks future data, so the agent backtest only injects fundamentals when
  running in live mode.

### 5.3 data/news.py
```python
def fetch_current_news(ticker: str, limit: int = 8) -> list[dict]  # [{"title","publisher","date"}]
```
- `yfinance.Ticker(ticker).news`; tolerate schema variations (keys may be nested under
  "content"); best-effort, return [] on failure. Live-mode only; same leak warning as above.
- Titles and publishers are third-party text bound for a prompt: whitespace is collapsed
  to single spaces and length is capped (200 / 60 characters), so a headline is always one
  short line and cannot pose as a section of the snapshot.

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
    data_notes: list[str] | None = None   # set only by the one-shot desk path (5.15)

    def context_block(self) -> str
```
- `context_block()` renders everything into one text block. When `anonymize=True` the
  header shows `"TICKER-X"` / `"Day T"` (the builders in graph.py already produce masked
  tables in that case), every ISO date in the block is scrubbed, and the real ticker is
  scrubbed from the third-party text only (fundamentals and headlines), case-sensitively
  against the ticker as typed and in upper case. It is not run over the builder's own
  tables: a ticker that is also a word or a label (`T`, `LOW`, `CCI`) would rewrite them.
- `data_notes`, when set, is rendered as a "Data notes" section directly under the header.
  Backtests never set it, so their prompts are unchanged.
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

### 5.15 agents/desk.py — one ticker, one snapshot, one decision
```python
@dataclass
class LatestPrices: df, downloaded_at (UTC), session: dict | None, refresh_failed: bool,
                    late_close: dict | None, gap: dict | None
def fetch_latest_prices(ticker, cache_dir=None, now=None) -> LatestPrices
def build_desk_snapshot(ticker, date=None, live=True, anonymize=False, lookback=60,
                        df=None, prices=None, now=None) -> DeskSnapshot
def snapshot_to_dict(desk) -> dict
def decision_to_dict(log, desk, usage=None, cost_usd=None) -> dict
def render_decision_card(log, desk) -> str
```
- `DeskSnapshot` = `MarketSnapshot` + `as_of`, `fetched_at`, `company`,
  `instrument_type`, `currency`, `last_close`, `history_rows`, `live`, `live_attempted`,
  `partial_session` (True / False / None), `stale`, `prices_behind`,
  `fundamentals_missing`, `notes`. Building one makes NO LLM call, so it
  serves both the `snapshot` command (and through it any external agent that runs the
  roles itself) and the `desk` command.
- **Governing rule: a data problem is never analysed silently.** Every gap, fallback and
  uncertainty adds a line to `notes`. The ones that change how the data must be read
  (unfinished session, unknown session state, old or stale bar, prices behind, a session
  with no usable bar, a close taken from the last regular-market price, a non-dollar
  currency, incomplete fundamentals) are also copied into
  `MarketSnapshot.data_notes` and rendered at the top
  of the context block, so the agents are told as well as the caller. Advice aimed at the
  caller ("re-run in a minute") stays out of the prompt. With no such notes the rendered
  block is byte-identical to the backtest path's (a test pins this), so cached responses
  and published transcripts stay reproducible.
- **Prices are never trusted from an old file.** `fetch_latest_prices` downloads on every
  call unless the previous download is younger than `_PRICE_REUSE_S` (15 min) and is not
  already known to be missing the newest session, or one behind an unfinished bar
  (otherwise the documented "wait and fetch again" could never clear the flag). A download
  stamped in the future is not
  reused either. An empty answer (Yahoo's response to rate limiting) is retried once
  after `_RETRY_WAIT_S`, except when Yahoo says the symbol does not exist; if it is still
  empty and an earlier download is on disk, that is returned with `refresh_failed=True`
  and the snapshot says the prices may be behind. Downloads go to `data/cache/desk/`,
  never the research cache: they can end in an intraday bar, which must not reach a
  backtest through a shared file.
- **The sidecar must describe its price file.** `*.meta.json` records the download time,
  a fingerprint of the frame (row count, newest bar's date, close and volume), the
  exchange facts and, when there is one, the late-close bar or the record of a missing
  session. It is written (atomically)
  right after the download and again once the
  metadata request returns, and a sidecar whose fingerprint does not match the parquet is
  ignored, so neither an interrupted run nor a failed cache write can leave prices judged
  by another download's time and calendar. A download whose session state came out
  undecided is not reused from the 15-minute window: a second look is what settles it.
  If that second look fails, the first is returned (minutes old, still undecided), not
  flagged as behind, with `recheck_failed` set so the caller is told the attempt failed.
  That note goes to the caller only: the data, and so the prompt, are unchanged. Cache writes are best-effort: an unwritable
  directory costs the cache, not the run.
- **Session state uses the exchange's own clock.** Yahoo dates a daily bar by the
  exchange's local day and includes the session in progress. `_fetch_session_info` reads
  the exchange time zone, instrument type, the regular session end and the last trade's
  time and price from Yahoo's chart metadata (one request per download). With it,
  `_last_bar_is_partial` covers Tokyo, crypto (today's bar is never final) and US
  half-days. The calendar is cross-checked, not trusted: it has been seen an hour early
  for a venue, with trades still printing. A trade stamped up to an hour (plus the settle
  window) after the calendar's close means "unknown" until the feed has been quiet for
  `_FEED_QUIET` (45 min); one stamped later than that is a late-reported print (London).
  A late stamp on an index is ignored, because Yahoo publishes index closes about an hour
  after the bell (cost: an index on a venue whose calendar is early is called a close).
  A last trade before the close keeps the bar open for `_MAX_FEED_DELAY` (30 min) past
  the settle window, for indices too, because Yahoo's quotes run 10 to 20 minutes behind
  on many venues. Known limit: where the calendar is an hour early, a trade stamped in
  the 15 minutes after its close is read as the closing print, so the bar is called final
  while the session still has most of an hour to run: for up to 15 minutes with a delayed
  feed, and for a thinly traded symbol as long as its latest trade sits in that window. Without a calendar,
  only plainly old bars (36 h rule) and US-style symbols (New York clock) are decided;
  anything else is `None` and reported as unknown, never guessed as a close. If Yahoo reports trading on a later day than the newest usable bar
  (it sometimes returns a row with volume and no prices, which the loader drops), the
  snapshot is flagged `prices_behind`, unless the late-close rule below completes the bar.
- **A late close is filled from Yahoo's last regular-market price, and said.** For London
  and Frankfurt shares Yahoo's daily row for a finished session has open, high, low and
  volume but no close for many hours, while the chart metadata already has the closing
  price. Through `fetch_prices` the row arrives with a volume only: yfinance's adjustment
  multiplies open, high and low by adjusted close over close, so a missing close empties
  them. `data/prices.py` is deliberately unchanged, and backtests drop the row as before.
  `_late_close_bar` (called by `fetch_latest_prices` only) builds the bar when ALL of
  this holds, and otherwise changes nothing (row dropped, `prices_behind`, the same note):
  the session facts carry a positive last price whose trade is dated, on the exchange's
  clock, after the newest usable bar; that session is over (`_last_bar_is_partial` says
  `False`; `True` and `None` both refuse); the instrument is not a cryptocurrency; in
  Yahoo's unadjusted daily rows from the newest usable bar on (`_fetch_raw_tail`, one
  extra request, made only once the checks so far have passed, with `keepna=True` so
  that yfinance does not leave out a row Yahoo sent empty) the newest row is dated
  that trade's day (an all-empty row after it, Yahoo's entry for a session not yet
  begun, is overlooked), follows the newest usable bar with no row in between, empty or
  not, has positive open, high, low and volume (yfinance writes a missing volume as 0)
  and no close; that usable bar's
  unadjusted close equals its adjusted close; and the price lies within the row's
  low-to-high range, give or take `_RANGE_TOLERANCE` for rounding. The bar is appended
  in memory and recorded in the sidecar (`late_close`), never written into the price
  file, so a reused download gives the same bar and note and no price file can hold a
  filled close without the record that says so. Sidecars without the field load as
  before. The snapshot then reports `prices_behind` false and `partial_session` false,
  and one note (in `notes` and in `data_notes`) names the date and the price; a masked
  snapshot leaves the price out. Known limits: the last regular-market price is not
  guaranteed to be the official close (London prints are reported up to two hours
  late), which the note says; the adjusted-close check also refuses the day a dividend
  takes effect and the day before, when the unadjusted row could not be placed
  in the adjusted series; a split is not seen by that check, because Yahoo restates
  close and adjusted close for it together, so on a split day the filled bar is right
  only once Yahoo has restated its history, as for a bar Yahoo delivers complete; and a
  day Yahoo leaves out of its answer altogether, unlike one it sends empty, cannot be
  told from a holiday.
- **A session missing behind the one in progress ends the view before it.** Sampled on
  SHEL.L and SAP.DE on 2026-11-11: for about half an hour after the open (08:24 and 08:34
  UTC) Yahoo's row for the session that had closed the evening before was empty while the
  row of the session in progress had values; from 08:44 it was complete. The loader drops
  the empty row, so the frame ran from the 9th straight to the unfinished 11th, the last
  trade day equalled the newest bar, and nothing was said: the one-day change and every
  indicator were computed across a day that was not there. `_gap_behind_open_session`
  (called by `fetch_latest_prices` only, and only when `_last_bar_is_partial` says `True`
  for the newest usable bar) reads Yahoo's unadjusted rows from the bar before it
  (`_fetch_raw_tail`) and takes every date listed strictly between the last two usable
  bars as a session these prices do not have, whatever its row holds (nothing, no close,
  or a whole bar that Yahoo completed between the two requests).
  - *Decision: the unfinished bar is dropped as well.* `LatestPrices.df` is cut back to
    the last complete session and `gap` records `{"missing": [dates], "left_out": date of
    the unfinished bar}`. Keeping the bar would leave a table whose last two rows read as
    consecutive sessions, and a note cannot repair that: the 1-day return, the true range
    and every moving figure in the block would still be measured across the hole.
    Ending at the last complete session gives a view that is right in every figure and
    merely old, which is what `prices_behind` already means. It is also the view the
    snapshot showed minutes earlier, while Yahoo's row for the new session was still empty
    (the "returned no usable bar" case above), so the view does not jump forward and back
    across the open. The snapshot reports `prices_behind` true, `partial_session` false
    and `as_of` the last complete session, with one note (in `notes` and `data_notes`)
    that names the missing date or dates and the bar left out. That note replaces the
    "Yahoo reports trading on ..." one, which would name the wrong day. A paid `desk` run
    therefore stops unless `--allow-degraded`. No key is added to `snapshot_to_dict`.
  - *The record.* It lives in the sidecar (`gap`). The price file stays exactly what Yahoo
    delivered, unfinished bar included, and a record that does not fit its price file
    discredits the sidecar. Such a download is never reused from the 15-minute window, so
    "wait a minute and fetch again" reaches Yahoo and the flag clears once the row is
    there. `data/prices.py` is unchanged and backtests read the same rows as before.
  - *What is not a missing session.* A date Yahoo does not list. Two years of unadjusted
    daily rows (`keepna=True`) for 22 shares, funds and indices in twelve markets, read on
    2026-11-11, hold no row for an exchange holiday or a weekend; the five empty rows in
    them (VOD.L 2026-07-30, BP.L 2025-02-27, VUSA.L 2025-10-24 and 2026-03-06, 005930.KS
    2025-09-19) are ordinary trading days Yahoo has no data for, to this day. For
    currencies and futures that reading fails: Yahoo lists a holiday as an empty row
    (EURUSD=X, GBPUSD=X and JPY=X on 2025-12-25 and 2026-01-01; ES=F, GC=F and CL=F on
    2025-05-26 and 2025-06-19), so `_NO_GAP_CHECK` leaves `CURRENCY` and `FUTURE` out.
    `CRYPTOCURRENCY` is left out as it is from the late-close fill. Without session facts
    nothing is looked for, because the instrument type is then unknown.
  - *Cost.* At most one extra request per download, and Yahoo's unadjusted rows are never
    read twice: the late-close fill reads them only when the newest usable bar is not an
    unfinished session, this check only when it is. The request is skipped when the last
    two usable bars are consecutive calendar days, since no session can lie between them:
    an ordinary Tuesday-to-Friday snapshot costs nothing more, the first session after a
    weekend or a holiday costs one request.
  - *An earlier fill is carried over.* If the previous download filled that very session
    (a `late_close` in its sidecar, made before the open while the row lacked only its
    close), `_carried_fill` puts the bar back between the last two bars instead of
    reporting a gap. Once the next session trades, Yahoo's last regular-market price is
    today's, so the bar cannot be built again and the record is the only copy. The guards
    are the fill's, read for a bar that is no longer the newest: the instrument is not
    left out above; the record is for the one date Yahoo lists between the two bars; it
    is a whole bar (positive open, high, low, close and volume) with its close inside its
    own low-to-high range; that session is over; Yahoo's row for it still has no close (a
    close Yahoo has is the one to show, and the next download brings it); the usable bar
    before it reads the same adjusted and unadjusted; and that bar is the one the record
    followed in the earlier download, with the same close, so nothing was restated since
    (a split, which the adjusted-against-unadjusted check cannot see). Any failure
    reports the gap as if there had been no earlier download. The carried bar is recorded
    in the new sidecar like any fill and never written to the price file; `prices_behind`
    is false, `partial_session` true, and the late-close note is given wherever the bar is
    in view, including a past `date` that ends on it. When Yahoo delivers the row, the
    frame holds Yahoo's bar and the record is dropped.
  - *Known limits.* The hole is looked for only behind an unfinished bar: behind a
    finished or undecided newest bar (a row Yahoo never fills, read after the close) it
    is still not reported, and a bar carried over during the session is gone then too. A
    download in between that cannot use the earlier fill (Yahoo's rows for both sessions
    empty, the "returned no usable bar" case) does not keep the record, so the next one
    reports the gap. A missing day behind a cryptocurrency's bar (seen on BTC-USD the
    same morning) is still not reported. A date Yahoo leaves out of its answer altogether
    cannot be told from a holiday. When the unadjusted rows cannot be read, nothing is
    reported. Where Yahoo never fills a row, the whole of the following session is flagged.
- **Lookahead guard**: fundamentals/news are injected only when the snapshot is for the
  newest bar. A past `date` never receives them (Yahoo serves current values only).
  `anonymize=True` also drops them, since company figures and headlines defeat masking.
  Session checks describe the newest bar and are skipped for past dates. `date` is
  validated: empty, NaT and future values raise instead of falling through to "latest".
- **Old versus stale.** A newest bar more than `_OLD_BAR_NOTE_DAYS` (5) old is noted; more
  than `_STALE_AFTER_DAYS` (12) sets `stale`, which stops the paid desk. Twelve days is
  longer than any scheduled exchange closure, so a holiday week is not mistaken for a
  halted symbol. The holiday / halted wording follows the bar's age when it was
  downloaded: after a failed refresh a bar can be old only because the download is.
- **Currencies.** The chart metadata's currency is what prices are quoted in; a note says
  so for anything but USD, for past dates as well as the newest bar, and spells out minor
  units (GBp is pence). Statements are often
  in another currency (`financialCurrency`): the quarterly lines carry it, and a note
  says when it differs from the share currency.
- **"available" is not trusted.** Yahoo answers a rate-limited fundamentals request with
  `available: True` and no figures. `missing_ratio_fields` counts the ratios that actually
  arrived; an all-empty answer is retried once; an answer with nothing in it yields
  `fundamentals=None` (never an error string dressed up as a block); a partial answer
  names what is missing.
- No prints (library code); the CLI does all output.

### 5.16 cli.py
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
- `snapshot --ticker AAPL [--date] [--no-live] [--anonymize] [--json]` — no API key; prints
  the exact context block the agents see plus metadata and data-quality notes (`--json`:
  one JSON document on stdout).
- `desk --ticker AAPL [--date] [--no-live] [--anonymize] [--json] [--debate-rounds N]
  [--allow-degraded]` — needs API key; one `pipeline.decide` on that snapshot; prints a
  decision card and the token usage/cost table; saves `results/desk/{ticker}_{as_of}.md`
  and `.json`. Kept out of `results/transcripts/`, which the dashboard replays as backtest
  decision days. With `--json`, stdout carries the JSON only and progress goes to stderr.
  **Spend gate**: exits 1 before any LLM call when the snapshot is stale, prices are
  behind, or live data was asked for (`live_attempted`, set by the snapshot builder, not
  inferred from the flags) and none came back; `--allow-degraded` overrides.
- Output discipline for both: JSON on stdout is ASCII-only (`ensure_ascii=True`) and
  `main()` sets `errors="backslashreplace"` on stdout/stderr, because headlines and LLM
  text hold arbitrary characters and a Windows pipe (cp1252) would otherwise abort the
  print.
- Write checks: for the commands whose product is a file (`baselines`, `train-ml`,
  `agents`, `probe`, `desk`) `main()` proves `results/` is writable by really creating a
  file there under a name of its own (`mkdir(exist_ok=True)` succeeds on a read-only
  folder; `tempfile.mkstemp` is avoided because older Pythons on Windows retry "access
  denied" about two billion times), and for the three
  that call the API the LLM cache too, and exits with one `error:` line naming
  `TRADINGLAB_HOME` otherwise, before any work or spend. `download` checks its cache
  folder the same way, since the cache is its whole product. `snapshot` and `report` need
  no writable folder; for them and for the backtests the price cache is best-effort.
- `report` — load saved CSVs and print a combined summary.
main() entry: `python -m tradinglab.cli <cmd>`, or the `tradinglab` console command.
`--version` prints `tradinglab <version>`; the skill's setup check compares it.

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
- **test_desk.py**: workspace resolution (env override, checkout, installed and vendored
  fallbacks, and that the path constants really follow the resolver); desk snapshot
  no-lookahead (perturb rows after the analysis date → identical context block, close and
  flags); `--date` validation; past dates and anonymized snapshots never fetch live data;
  empty / incomplete / retried fundamentals; headline sanitising; the anonymize scrub on
  word-like tickers; session state on the exchange clock (US boundary, Tokyo, crypto,
  half-day, unknown), the stale threshold, prices behind; the download policy (desk-only
  cache, 15-minute reuse, retry, fallback, sidecar); late closes (each fill condition,
  the sidecar record, reuse, and the unchanged backtest path); a session missing behind
  the one in progress (the London and Frankfurt shape, what stays quiet, the request
  budget, the carried-over fill and each of its guards); `snapshot` / `desk` CLI output,
  encoding safety, artifacts and the spend gate, with the pipeline stubbed.
- **test_skill_package.py**: skill frontmatter and packaging rules; every `tradinglab ...`
  line in the skill's docs is parsed with the real argparse parser, so renaming a
  subcommand or flag fails a test instead of a user's terminal; the install URL is pinned
  to the release tag that matches the package version; the skill's text keeps its
  promises (disclosure, no trading, no personal inputs).
- Tests must not hit the network — construct synthetic DataFrames (seeded RNG, ~400 rows,
  business-day index).

## 7. Cost model for the agent layer (documentation)

Per decision day ≈ 2 deep + ~2 quick calls minimum (no news/fundamentals in historical
mode): market analyst (deep), bull/bear ×n_rounds (deep), facilitator (deep), trader (deep),
risk trio (quick ×3), fund manager (deep). With defaults (2 debate rounds) ≈ 8 deep + 3
quick calls ≈ 15–25k input tokens/day. Use `--every 5` and `--max-days` to bound cost; the
disk cache makes repeat runs free.
