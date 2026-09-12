"""
Portfolio-Level Equity Curve Simulation.

backtester.py reports per-trade metrics that average every trade as an
independent event. That hides real risk: a real account has one pool of
capital shared across every position open at the same time. This module
aggregates a backtest_df's individual trades into one account equity
curve, booking each trade's realized dollar P&L on its exit date, and
reports the portfolio-level Sharpe ratio and max drawdown from that curve.

Limitation: capital is unconstrained here -- every signal is sized and
taken regardless of how many other positions are already open, since
backtester.py does not track available cash. A real account with a fixed
capital base would decline some concurrent trades, so this overstates
achievable capacity. Position sizing is a fixed fraction of the starting
capital (position_size_pct), not reinvested/compounded.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


class PortfolioSimulator:
    def __init__(self, initial_capital: float = 100_000.0, position_size_pct: float = 0.02):
        """
        :param initial_capital: Starting account equity in dollars.
        :param position_size_pct: Fraction of initial_capital risked per trade.
        """
        self.initial_capital = initial_capital
        self.position_size_pct = position_size_pct

    def simulate(self, backtest_df: pd.DataFrame) -> pd.DataFrame:
        """Builds a daily account equity curve from realized trade P&L.

        Returns a DataFrame with one row per calendar date a trade exited,
        columns: date, daily_pnl, equity.
        """
        trades = backtest_df[backtest_df["entry_trade"]].dropna(subset=["exit_date", "trade_return"])
        if trades.empty:
            return pd.DataFrame(columns=["date", "daily_pnl", "equity"])

        position_size = self.initial_capital * self.position_size_pct
        trade_pnl = trades[["exit_date", "trade_return"]].copy()
        trade_pnl["dollar_pnl"] = trade_pnl["trade_return"] * position_size

        daily_pnl = (
            trade_pnl.groupby("exit_date")["dollar_pnl"]
            .sum()
            .reset_index()
            .rename(columns={"exit_date": "date", "dollar_pnl": "daily_pnl"})
            .sort_values("date")
        )
        daily_pnl["equity"] = self.initial_capital + daily_pnl["daily_pnl"].cumsum()
        return daily_pnl.reset_index(drop=True)

    def simulate_dynamic(
        self,
        backtest_df: pd.DataFrame,
        mode: str = "compound",
        drawdown_threshold: float = 0.10,
        throttle_factor: float = 0.5,
    ) -> pd.DataFrame:
        """Equity curve with position size tied to current account equity.

        mode="compound": position_size = current_equity * position_size_pct,
        so size grows as wins accumulate and shrinks as losses accumulate.
        mode="throttle": same as fixed sizing, but position_size_pct is cut
        by throttle_factor whenever current equity is drawdown_threshold or
        more below its running peak, and restored once equity recovers.
        """
        trades = backtest_df[backtest_df["entry_trade"]].dropna(subset=["exit_date", "trade_return"])
        if trades.empty:
            return pd.DataFrame(columns=["date", "daily_pnl", "equity"])

        grouped = (
            trades.sort_values("exit_date")
            .groupby("exit_date")["trade_return"]
            .apply(list)
            .reset_index()
        )

        equity = self.initial_capital
        peak = self.initial_capital
        rows = []
        for _, row in grouped.iterrows():
            if mode == "throttle":
                drawdown = (equity - peak) / peak if peak > 0 else 0.0
                pct = self.position_size_pct * throttle_factor if drawdown <= -drawdown_threshold \
                    else self.position_size_pct
            else:
                pct = self.position_size_pct
            position_size = equity * pct
            daily_pnl = sum(r * position_size for r in row["trade_return"])
            equity += daily_pnl
            peak = max(peak, equity)
            rows.append({"date": row["exit_date"], "daily_pnl": daily_pnl, "equity": equity})

        return pd.DataFrame(rows)

    def compute_performance(self, equity_curve: pd.DataFrame) -> dict:
        """Computes portfolio-level Sharpe ratio and max drawdown from the equity curve."""
        if equity_curve.empty:
            return {
                "final_equity": self.initial_capital,
                "total_return_pct": 0.0,
                "portfolio_sharpe": 0.0,
                "max_drawdown_pct": 0.0,
            }

        daily_returns = equity_curve["equity"].pct_change().dropna()
        mean_daily = daily_returns.mean()
        std_daily = daily_returns.std()
        sharpe = float(mean_daily / std_daily * np.sqrt(252)) if std_daily > 0 else 0.0

        peak = equity_curve["equity"].cummax()
        drawdown = (equity_curve["equity"] - peak) / peak
        max_drawdown = float(drawdown.min())

        final_equity = float(equity_curve["equity"].iloc[-1])
        total_return_pct = (final_equity / self.initial_capital - 1.0) * 100.0

        return {
            "final_equity": final_equity,
            "total_return_pct": total_return_pct,
            "portfolio_sharpe": sharpe,
            "max_drawdown_pct": max_drawdown * 100.0,
        }
