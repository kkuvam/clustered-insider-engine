"""
Reproduces the exit-mechanic and position-sizing sweeps described in
specs/decision_journal.md Sections 4, 5, 7 and 8 (fixed vs. trailing stop,
the profit-activated trail, the stop-loss width sweep, and the sizing-mode
comparison). Generates signals once by reusing main.py's per-ticker worker
and the cached universe list, then backtests every combination below at the
adopted 30-day holding period and writes one row per combination to
exit_and_sizing_results.csv next to this script.

Run with: .venv/bin/python experiments/exit_and_sizing_sweep.py
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from backtester import EventBacktester
from config import StrategyConfig
from data_client import MassiveDataClient
from main import process_single_ticker
from portfolio_simulator import PortfolioSimulator
from signal_engine import VectorizedSignalEngine
from universe_scanner import DynamicUniverseScanner

HOLDING_PERIOD = 30  # the adopted primary holding period; sweep runs at this period only
START_DATE, END_DATE = "2021-01-01", "2026-01-01"

# (stop_loss_pct, trail_activation_pct, trail_pct, take_profit_pct, label)
EXIT_COMBOS = [
    (0.15, None, None, 0.30, "fixed15_tp30_no_trail"),
    (0.15, 0.50, 0.15, 0.30, "act50_trail15_stop15_tp30"),
    (0.20, 0.50, 0.15, 0.30, "act50_trail15_stop20_tp30_ADOPTED"),
    (0.10, 0.50, 0.15, 0.30, "act50_trail15_stop10_tp30"),
    (0.25, 0.50, 0.15, 0.30, "act50_trail15_stop25_tp30"),
    (0.20, 0.30, 0.15, 0.30, "act30_trail15_stop20_tp30"),
    (0.20, 0.10, 0.15, 0.30, "act10_trail15_stop20_tp30"),
    (0.20, 0.80, 0.15, 0.30, "act80_trail15_stop20_tp30"),
    (0.20, None, None, None, "trail_only_no_tp_cap"),
]

SIZING_MODES = ["static", "compound", "house_money"]


def build_signals() -> pd.DataFrame:
    """Generates the same combined signal set main.py uses, from cached data."""
    config = StrategyConfig()
    client = MassiveDataClient()
    scanner = DynamicUniverseScanner(client=client, config=config)
    engine = VectorizedSignalEngine(config=config)

    universe = scanner.discover_insider_candidate_tickers(
        start_date=START_DATE, end_date=END_DATE, min_purchases=config.MIN_DISTINCT_INSIDERS
    )

    all_signals = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {
            executor.submit(
                process_single_ticker, ticker, client, engine, START_DATE, END_DATE, config
            ): ticker
            for ticker in universe
        }
        for future in as_completed(futures):
            df_result = future.result()
            if not df_result.empty and df_result["signal_valid"].sum() > 0:
                all_signals.append(df_result)

    return pd.concat(all_signals, ignore_index=True)


def run_one(signals: pd.DataFrame, stop, activation, trail, tp, sizing_mode: str) -> dict:
    config = StrategyConfig()
    backtester = EventBacktester(
        holding_period_days=HOLDING_PERIOD,
        stop_loss_pct=stop,
        take_profit_pct=tp,
        trailing_stop=False,
        transaction_cost_pct=config.TRANSACTION_COST_PCT,
        trail_activation_pct=activation,
        trail_pct=trail,
    )
    bt_df = backtester.execute_trades(signals)
    metrics = backtester.compute_performance(bt_df)

    portfolio = PortfolioSimulator(
        initial_capital=config.INITIAL_CAPITAL, position_size_pct=config.POSITION_SIZE_PCT
    )
    if sizing_mode == "house_money":
        curve = portfolio.simulate_dynamic(
            bt_df, mode="house_money", aggressive_pct=config.HOUSE_MONEY_AGGRESSIVE_PCT
        )
    elif sizing_mode == "compound":
        curve = portfolio.simulate_dynamic(bt_df, mode="compound")
    else:
        curve = portfolio.simulate(bt_df)

    metrics.update(portfolio.compute_performance(curve))
    metrics["sizing_mode"] = sizing_mode
    return metrics


def main():
    print("Building signals from cached market data (compute-bound, no network calls needed)...")
    signals = build_signals()
    print(f"Loaded {len(signals)} signal rows across the cached universe.\n")

    rows = []
    for stop, activation, trail, tp, label in EXIT_COMBOS:
        for sizing_mode in SIZING_MODES:
            metrics = run_one(signals, stop, activation, trail, tp, sizing_mode)
            metrics.update(
                combo_label=label,
                stop_loss_pct=stop,
                trail_activation_pct=activation,
                trail_pct=trail,
                take_profit_pct=tp,
            )
            rows.append(metrics)
            print(
                f"{label:35s} {sizing_mode:12s} trades={metrics['total_trades']:4d} "
                f"return={metrics['total_return_pct']:7.1f}% "
                f"sharpe={metrics['portfolio_sharpe']:.3f} "
                f"dd={metrics['max_drawdown_pct']:6.1f}%"
            )

    out_path = Path(__file__).resolve().parent / "exit_and_sizing_results.csv"
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"\nWrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
