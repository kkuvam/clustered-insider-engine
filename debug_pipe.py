"""
Diagnostic Script to Audit Signal Drop-off Across the Pipeline.
"""
from __future__ import annotations
import pandas as pd
from config import StrategyConfig
from data_client import MassiveDataClient
from signal_engine import VectorizedSignalEngine
from universe_scanner import DynamicUniverseScanner


def audit_signal_waterfall():
    config = StrategyConfig()
    client = MassiveDataClient()
    scanner = DynamicUniverseScanner(client=client, config=config)
    engine = VectorizedSignalEngine(config=config)

    start_date = "2021-01-01"
    end_date = "2026-01-01"

    print("Fetching SPY benchmark...")
    spy_df = client.fetch_benchmark_ohlcv(start_date, end_date, ticker="SPY")

    print("Scanning dynamic ticker universe...")
    universe = scanner.discover_insider_candidate_tickers(
        start_date=start_date, end_date=end_date, min_purchases=2
    )

    if not universe:
        print("No candidates discovered by scanner.")
        return

    sample_size = min(50, len(universe))
    sample_tickers = universe[:sample_size]
    print(f"\nRunning Filter Waterfall Audit across sample of {sample_size} tickers...\n")

    stats = {
        "total_rows": 0,
        "pass_insider_cluster": 0,
        "pass_technical": 0,
        "pass_fundamental": 0,
        "spy_bull_regime": 0,
        "signal_valid": 0,
    }

    for ticker in sample_tickers:
        price_df, insider_df, fund_df = client.load_dataset(ticker, start_date, end_date)
        if price_df.empty or len(price_df) < config.SMA_PERIOD:
            continue

        df = engine.generate_signals(price_df, insider_df, fund_df, spy_df=spy_df)

        stats["total_rows"] += len(df)
        stats["pass_insider_cluster"] += df["pass_insider_cluster"].sum()
        stats["pass_technical"] += df["pass_technical"].sum()
        stats["pass_fundamental"] += df["pass_fundamental"].sum()
        stats["spy_bull_regime"] += df["spy_bull_regime"].sum()
        stats["signal_valid"] += df["signal_valid"].sum()

    print("=== FILTER WATERFALL AUDIT RESULTS ===")
    print(f"Total Evaluated Trading Days:  {stats['total_rows']:,}")
    print(f"1. Passed Insider Cluster Gate: {stats['pass_insider_cluster']:,}")
    print(f"2. Passed Technical/RSI Gate:   {stats['pass_technical']:,}")
    print(f"3. Passed Fundamental Gate:     {stats['pass_fundamental']:,}")
    print(f"4. Passed SPY Macro Regime:     {stats['pass_spy_bull'] if 'pass_spy_bull' in stats else stats['spy_bull_regime']:,}")
    print(f"----------------------------------------")
    print(f"FINAL COMPOSITE VALID SIGNALS: {stats['signal_valid']:,}")


if __name__ == "__main__":
    audit_signal_waterfall()