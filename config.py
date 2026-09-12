"""
Configuration module for Clustered Insider Buying Backtest Engine.
Loads environment variables and holds global strategy hyperparameters.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
from dotenv import load_dotenv

# Base Directory Setup
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Global Paths
DATA_DIR = BASE_DIR / "data"

# API Configuration
MASSIVE_API_KEY = os.getenv("MASSIVE_API_KEY")
MASSIVE_BASE_URL = os.getenv("MASSIVE_BASE_URL", "https://api.massive.com")

if not MASSIVE_API_KEY:
    raise ValueError(
        "MASSIVE_API_KEY is not set. Ensure you have a valid .env file in the project root."
    )


@dataclass(frozen=True)
class StrategyConfig:
    # Insider Cluster Signal Settings
    MIN_DISTINCT_INSIDERS: int = 3
    CLUSTER_WINDOW_DAYS: int = 5
    MIN_CLUSTER_VALUE: float = 100_000.0  # $100k combined insider purchase volume floor

    # Data Sufficiency
    MIN_HISTORY_DAYS: int = 50  # minimum trading days of price history required

    # Tier-1: Opportunistic vs. Routine Insider Classification (Cohen/Malloy/Pomorski)
    ROUTINE_MIN_YEARS: int = 3  # same-calendar-month buys in >=N distinct years => routine, excluded

    # Tier-2: Self-Relative Conviction Sizing
    CONVICTION_SIZE_MULTIPLIER: float = 1.5  # buy must be >= N x insider's own prior avg buy at this ticker

    # Tier-2: Short Interest Overlap
    MIN_DAYS_TO_COVER: float = 2.0  # signal requires short-side days-to-cover >= N at time of signal

    # Execution Parameters
    HOLDING_PERIODS: Tuple[int, ...] = (30, 60, 90)
    MAX_TRADE_RETURN_CAP: float = 5.0
    STOP_LOSS_PCT: Optional[float] = 0.15  # fixed 15% stop-loss from entry price
    TAKE_PROFIT_PCT: Optional[float] = 0.30  # fixed 30% take-profit from entry price
    TRAILING_STOP: bool = False  # fixed stop beat trailing stop in the exit-strategy sweep
    TRANSACTION_COST_PCT: float = 0.002  # 20 bps round-trip commission + slippage estimate