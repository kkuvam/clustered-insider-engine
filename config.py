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

    # Multi-Factor Filtering Criteria
    SMA_PERIOD: int = 50
    MIN_PRICE: float = 5.00
    MAX_DEBT_TO_EQUITY: float = 2.0
    MIN_MARKET_CAP: float = 500_000_000.0

    # Execution Parameters
    HOLDING_PERIODS: Tuple[int, ...] = (30, 60, 90)
    MAX_TRADE_RETURN_CAP: float = 5.0
    TRAILING_STOP_PCT: Optional[float] = 0.15  # 15% trailing stop from high-water mark