"""
Pipeline Orchestrator for Full-Market Clustered Insider Buying Backtest Engine.
Dynamically discovers market-wide candidate tickers and executes multi-threaded backtests.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from backtester import EventBacktester
from config import StrategyConfig, DATA_DIR
from data_client import MassiveDataClient
from portfolio_simulator import PortfolioSimulator
from signal_engine import VectorizedSignalEngine
from universe_scanner import DynamicUniverseScanner


def process_single_ticker(
    ticker: str,
    client: MassiveDataClient,
    engine: VectorizedSignalEngine,
    start_date: str,
    end_date: str,
    config: StrategyConfig,
) -> pd.DataFrame:
    """Worker function executed concurrently per candidate ticker."""
    try:
        price_df, insider_df, fund_df, short_interest_df, _earnings_df = client.load_dataset(
            ticker, start_date=start_date, end_date=end_date
        )

        if price_df.empty or len(price_df) < config.MIN_HISTORY_DAYS:
            return pd.DataFrame()

        signal_df = engine.generate_signals(price_df, insider_df, fund_df, short_interest_df)
        signal_df["ticker"] = ticker
        return signal_df

    except Exception as exc:
        print(f"  [Warning] {ticker}: skipped due to {type(exc).__name__}: {exc}")
        return pd.DataFrame()


def main():
    start_date = "2021-01-01"
    end_date = "2026-01-01"

    config = StrategyConfig()
    client = MassiveDataClient()
    scanner = DynamicUniverseScanner(client=client, config=config)
    engine = VectorizedSignalEngine(config=config)

    # 1. Dynamically scan global market for all candidate tickers
    universe = scanner.discover_insider_candidate_tickers(
        start_date=start_date, end_date=end_date, min_purchases=config.MIN_DISTINCT_INSIDERS
    )

    if not universe:
        print("No market candidates identified.")
        return

    print(f"\n=== Executing Accelerated Engine across {len(universe)} Dynamic Tickers ===")

    all_signals = []
    max_workers = 20  # Increased worker threads for market-scale execution

    # 2. Multithreaded parallel signal generation across full market universe
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                process_single_ticker,
                ticker,
                client,
                engine,
                start_date,
                end_date,
                config,
            ): ticker
            for ticker in universe
        }

        completed_count = 0
        total_tickers = len(universe)

        for future in as_completed(futures):
            completed_count += 1
            ticker = futures[future]
            df_result = future.result()

            if not df_result.empty and df_result["signal_valid"].sum() > 0:
                valid_signals = df_result["signal_valid"].sum()
                print(f"  [{completed_count}/{total_tickers}] {ticker}: Triggered {valid_signals} signals")
                all_signals.append(df_result)

    if not all_signals:
        print("No valid strategy signals generated across the entire candidate universe.")
        return

    combined_signals_df = pd.concat(all_signals, ignore_index=True)

    # 3. Parameter Sweep Across Holding Periods (30, 60, 90 Days)
    results = []
    all_trade_logs = []

    print("\nExecuting Vectorized Full-Market Simulations...")

    for N in config.HOLDING_PERIODS:
        backtester = EventBacktester(
            holding_period_days=N,
            stop_loss_pct=config.STOP_LOSS_PCT,
            take_profit_pct=config.TAKE_PROFIT_PCT,
            trailing_stop=config.TRAILING_STOP,
            transaction_cost_pct=config.TRANSACTION_COST_PCT,
            trail_activation_pct=config.TRAIL_ACTIVATION_PCT,
            trail_pct=config.TRAIL_PCT,
        )
        bt_df = backtester.execute_trades(combined_signals_df)
        metrics = backtester.compute_performance(bt_df)

        portfolio = PortfolioSimulator(
            initial_capital=config.INITIAL_CAPITAL, position_size_pct=config.POSITION_SIZE_PCT
        )
        if config.SIZING_MODE == "house_money":
            equity_curve = portfolio.simulate_dynamic(
                bt_df, mode="house_money", aggressive_pct=config.HOUSE_MONEY_AGGRESSIVE_PCT
            )
        elif config.SIZING_MODE == "compound":
            equity_curve = portfolio.simulate_dynamic(bt_df, mode="compound")
        else:
            equity_curve = portfolio.simulate(bt_df)
        portfolio_metrics = portfolio.compute_performance(equity_curve)
        metrics.update(portfolio_metrics)
        results.append(metrics)

        executed_trades = bt_df[bt_df["entry_trade"]].copy()
        executed_trades["holding_period"] = N
        all_trade_logs.append(executed_trades)

    # Export trade execution logs to Parquet for inspection
    if all_trade_logs:
        full_trade_log = pd.concat(all_trade_logs, ignore_index=True)
        export_path = DATA_DIR / "market_trade_results.parquet"
        full_trade_log.to_parquet(export_path, index=False)
        print(f"\nExported {len(full_trade_log)} executed trade records to {export_path}")

    summary_df = pd.DataFrame(results)
    print("\n=== Full-Market Strategy Performance Summary ===")
    print(
        summary_df[
            [
                "holding_period",
                "total_trades",
                "win_rate",
                "mean_return",
                "sharpe_ratio",
                "weighted_mean_return",
                "weighted_sharpe_ratio",
                "max_loss",
                "max_gain",
                "final_equity",
                "total_return_pct",
                "portfolio_sharpe",
                "max_drawdown_pct",
            ]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()