"""Central configuration for trading-agents-lab.

All modules import path constants and config dataclasses from here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_CACHE_DIR = PROJECT_ROOT / "data" / "cache"
LLM_CACHE_DIR = PROJECT_ROOT / "cache" / "llm"
RESULTS_DIR = PROJECT_ROOT / "results"

DEFAULT_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "META", "TSLA", "JPM", "XOM", "JNJ",
]


@dataclass
class DataConfig:
    tickers: list[str] = field(default_factory=lambda: list(DEFAULT_TICKERS))
    start: str = "2015-01-01"
    end: str | None = None  # None = up to latest available
    cache_dir: Path = DATA_CACHE_DIR


@dataclass
class BacktestConfig:
    cost_bps: float = 10.0          # per unit turnover
    risk_free_rate: float = 0.04    # annualized, for Sharpe/Sortino
    long_only: bool = False


@dataclass
class MLConfig:
    horizon: int = 1                # forward-return horizon in trading days
    test_start: str = "2021-01-01"  # walk-forward evaluation begins here
    retrain_step: int = 63          # refit every ~quarter of trading days
    embargo_days: int = 5           # gap between train end and test chunk
    signal_threshold: float = 0.0   # predicted-return threshold for taking a position
    random_state: int = 42


@dataclass
class AgentConfig:
    # quick: cheap summarization/side tasks; deep: reasoning-heavy decisions
    quick_model: str = os.environ.get("TRADINGLAB_QUICK_MODEL", "claude-haiku-4-5")
    deep_model: str = os.environ.get("TRADINGLAB_DEEP_MODEL", "claude-opus-4-8")
    max_tokens: int = 1200
    debate_rounds: int = 2          # bull/bear exchanges
    risk_rounds: int = 1            # risky/safe/neutral comment rounds
    lookback_days: int = 60         # history window for snapshots


@dataclass
class LabConfig:
    data: DataConfig = field(default_factory=DataConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    ml: MLConfig = field(default_factory=MLConfig)
    agents: AgentConfig = field(default_factory=AgentConfig)
