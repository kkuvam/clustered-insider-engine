"""
Reproduces the validation round in specs/decision_journal.md Section 10:
a chronological train/test split, a bootstrap confidence interval on trade
returns, a transaction-cost stress test, and a cash-constrained check on the
adopted house-money sizing mode. Uses the live 30-day configuration from
config.py. Writes validation_results.csv next to this script.

Run with: .venv/bin/python experiments/validation_suite.py
"""
from __future__ import annotations
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
from backtester import EventBacktester
from config import StrategyConfig
from exit_and_sizing_sweep import build_signals
from portfolio_simulator import PortfolioSimulator

RNG = np.random.default_rng(42)


def _trade_sharpe(returns: pd.Series, holding_period: int) -> float:
    std = returns.std()
    return float(returns.mean() / std * np.sqrt(252 / holding_period)) if std > 0 else 0.0


def chronological_split(trades: pd.DataFrame, holding_period: int, split_frac: float = 0.7) -> dict:
    trades = trades.sort_values("date")
    cutoff = trades["date"].quantile(split_frac)
    early = trades.loc[trades["date"] <= cutoff, "trade_return"]
    late = trades.loc[trades["date"] > cutoff, "trade_return"]
    return {
        "cutoff_date": str(pd.Timestamp(cutoff).date()),
        "early_n": len(early),
        "early_mean_return": float(early.mean()),
        "early_sharpe": _trade_sharpe(early, holding_period),
        "late_n": len(late),
        "late_mean_return": float(late.mean()),
        "late_sharpe": _trade_sharpe(late, holding_period),
    }


def bootstrap_ci(returns: pd.Series, n_resamples: int = 5000) -> dict:
    if returns.empty:
        return {"n": 0, "mean_return": 0.0, "ci_low": 0.0, "ci_high": 0.0}
    values = returns.to_numpy()
    means = np.array(
        [RNG.choice(values, size=len(values), replace=True).mean() for _ in range(n_resamples)]
    )
    return {
        "n": len(values),
        "mean_return": float(values.mean()),
        "ci_low": float(np.percentile(means, 2.5)),
        "ci_high": float(np.percentile(means, 97.5)),
    }


def cost_stress_test(
    signals: pd.DataFrame, config: StrategyConfig, stress_multiplier: float = 5.0
) -> dict:
    def run(cost_pct: float) -> dict:
        backtester = EventBacktester(
            holding_period_days=30,
            stop_loss_pct=config.STOP_LOSS_PCT,
            take_profit_pct=config.TAKE_PROFIT_PCT,
            trailing_stop=False,
            transaction_cost_pct=cost_pct,
            trail_activation_pct=config.TRAIL_ACTIVATION_PCT,
            trail_pct=config.TRAIL_PCT,
        )
        bt_df = backtester.execute_trades(signals)
        metrics = backtester.compute_performance(bt_df)
        portfolio = PortfolioSimulator(
            initial_capital=config.INITIAL_CAPITAL, position_size_pct=config.POSITION_SIZE_PCT
        )
        curve = portfolio.simulate_dynamic(
            bt_df, mode="house_money", aggressive_pct=config.HOUSE_MONEY_AGGRESSIVE_PCT
        )
        metrics.update(portfolio.compute_performance(curve))
        return metrics

    base_metrics = run(config.TRANSACTION_COST_PCT)
    stress_metrics = run(config.TRANSACTION_COST_PCT * stress_multiplier)
    return {
        "base_cost_pct": config.TRANSACTION_COST_PCT,
        "stress_cost_pct": config.TRANSACTION_COST_PCT * stress_multiplier,
        "base_sharpe": base_metrics["sharpe_ratio"],
        "stress_sharpe": stress_metrics["sharpe_ratio"],
        "base_mean_return": base_metrics["mean_return"],
        "stress_mean_return": stress_metrics["mean_return"],
        "base_portfolio_sharpe": base_metrics["portfolio_sharpe"],
        "stress_portfolio_sharpe": stress_metrics["portfolio_sharpe"],
        "base_total_return_pct": base_metrics["total_return_pct"],
        "stress_total_return_pct": stress_metrics["total_return_pct"],
    }


def cash_constrained_house_money(bt_df: pd.DataFrame, config: StrategyConfig) -> dict:
    """Tracks true available cash (not just mark-to-market equity) under the
    adopted house-money sizing mode, to check whether that mode ever wants
    to spend dollars still locked up in other open positions. This is the
    check that originally found the look-ahead sizing bug, now fixed in
    portfolio_simulator.py; kept here as a standing regression check.
    """
    trades = bt_df[bt_df["entry_trade"]].dropna(subset=["date", "exit_date", "trade_return"])
    trades = trades.sort_values("date")

    cash = config.INITIAL_CAPITAL
    equity = config.INITIAL_CAPITAL
    open_positions = []  # (exit_date, size, trade_return)
    capped_trades = 0

    for _, trade in trades.iterrows():
        still_open = []
        for exit_date, size, ret in open_positions:
            if exit_date <= trade["date"]:
                pnl = ret * size
                cash += size + pnl
                equity += pnl
            else:
                still_open.append((exit_date, size, ret))
        open_positions = still_open

        safe_base = min(equity, config.INITIAL_CAPITAL)
        cushion = max(equity - config.INITIAL_CAPITAL, 0.0)
        wanted_size = (
            safe_base * config.POSITION_SIZE_PCT + cushion * config.HOUSE_MONEY_AGGRESSIVE_PCT
        )
        size = min(wanted_size, max(cash, 0.0))
        if size < wanted_size:
            capped_trades += 1
        if size <= 0:
            continue
        cash -= size
        open_positions.append((trade["exit_date"], size, trade["trade_return"]))

    return {
        "total_trades": len(trades),
        "trades_needing_cash_cap": capped_trades,
        "pct_needing_cash_cap": (
            round(100.0 * capped_trades / len(trades), 2) if len(trades) else 0.0
        ),
    }


def main():
    config = StrategyConfig()
    print("Building signals from cached market data...")
    signals = build_signals()

    backtester = EventBacktester(
        holding_period_days=30,
        stop_loss_pct=config.STOP_LOSS_PCT,
        take_profit_pct=config.TAKE_PROFIT_PCT,
        trailing_stop=False,
        transaction_cost_pct=config.TRANSACTION_COST_PCT,
        trail_activation_pct=config.TRAIL_ACTIVATION_PCT,
        trail_pct=config.TRAIL_PCT,
    )
    bt_df = backtester.execute_trades(signals)
    trades = bt_df[bt_df["entry_trade"]].dropna(subset=["date", "trade_return", "exit_reason"])

    split_result = chronological_split(trades, holding_period=30)
    bootstrap_all = bootstrap_ci(trades["trade_return"])
    trail_returns = trades.loc[trades["exit_reason"] == "TRAIL_STOP", "trade_return"]
    bootstrap_trail = bootstrap_ci(trail_returns)
    stress_result = cost_stress_test(signals, config)
    cash_result = cash_constrained_house_money(bt_df, config)

    print("\n=== Chronological split (early 70% vs. late 30% by entry date) ===")
    print(split_result)
    print("\n=== Bootstrap 95% CI, all trades ===")
    print(bootstrap_all)
    print("\n=== Bootstrap 95% CI, TRAIL_STOP trades only ===")
    print(bootstrap_trail)
    print("\n=== Transaction cost stress test (5x assumed cost) ===")
    print(stress_result)
    print("\n=== Cash-constrained check on house-money sizing ===")
    print(cash_result)

    checks = {
        "chronological_split": split_result,
        "bootstrap_all_trades": bootstrap_all,
        "bootstrap_trail_stop_only": bootstrap_trail,
        "cost_stress_test": stress_result,
        "cash_constrained_check": cash_result,
    }
    rows = [
        {"check": check_name, "metric": metric, "value": value}
        for check_name, metrics in checks.items()
        for metric, value in metrics.items()
    ]
    out_path = Path(__file__).resolve().parent / "validation_results.csv"
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"\nWrote validation results to {out_path}")


if __name__ == "__main__":
    main()
