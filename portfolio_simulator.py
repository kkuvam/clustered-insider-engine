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

    def _run_entry_ordered(self, backtest_df: pd.DataFrame, size_fn) -> pd.DataFrame:
        """Shared engine for every equity-tied sizing mode.

        Processes trades in entry-date order (not exit-date order) so a
        trade's position size only ever reflects pnl already realized
        before that trade's own entry. Sizing by exit-date order let a
        trade's size be inflated by a different trade that entered later
        but happened to close first, a look-ahead bug found and fixed
        after a capital-constrained validation run disagreed with this
        module's numbers: 30-day total return dropped from 132.3% to
        113.7% and portfolio Sharpe from 2.088 to 1.986 once fixed, the
        gap between the buggy and correct sizing schedule.

        size_fn(equity, peak_equity, num_open_positions) -> dollar size
        for the trade about to be entered.
        """
        trades = backtest_df[backtest_df["entry_trade"]].dropna(subset=["date", "exit_date", "trade_return"])
        if trades.empty:
            return pd.DataFrame(columns=["date", "daily_pnl", "equity"])

        trades = trades.sort_values("date")
        equity = self.initial_capital
        peak = self.initial_capital
        open_positions = []  # (exit_date, dollar_size, trade_return)
        daily_pnl = {}  # exit_date -> summed pnl, one row per calendar date

        for _, trade in trades.iterrows():
            still_open = []
            for exit_date, size, ret in open_positions:
                if exit_date <= trade["date"]:
                    pnl = ret * size
                    equity += pnl
                    peak = max(peak, equity)
                    daily_pnl[exit_date] = daily_pnl.get(exit_date, 0.0) + pnl
                else:
                    still_open.append((exit_date, size, ret))
            open_positions = still_open

            position_size = size_fn(equity, peak, len(open_positions))
            open_positions.append((trade["exit_date"], position_size, trade["trade_return"]))

        for exit_date, size, ret in open_positions:
            pnl = ret * size
            daily_pnl[exit_date] = daily_pnl.get(exit_date, 0.0) + pnl

        curve = pd.DataFrame(sorted(daily_pnl.items()), columns=["date", "daily_pnl"])
        if curve.empty:
            return pd.DataFrame(columns=["date", "daily_pnl", "equity"])
        curve["equity"] = self.initial_capital + curve["daily_pnl"].cumsum()
        return curve

    def simulate_dynamic(
        self,
        backtest_df: pd.DataFrame,
        *,
        mode: str = "compound",
        drawdown_threshold: float = 0.10,
        throttle_factor: float = 0.5,
        aggressive_pct: float = 0.08,
    ) -> pd.DataFrame:
        """Equity curve with position size tied to current account equity.

        mode="compound": position_size = current_equity * position_size_pct,
        so size grows as wins accumulate and shrinks as losses accumulate.
        mode="throttle": same as fixed sizing, but position_size_pct is cut
        by throttle_factor whenever current equity is drawdown_threshold or
        more below its running peak, and restored once equity recovers.
        mode="house_money": splits equity into protected principal (up to
        initial_capital, sized at position_size_pct, shrinking like normal
        compounding if it takes losses) and a profit cushion above that
        (sized at the higher aggressive_pct). Lets deeper sizing act on
        gains without ever risking more than position_size_pct of the
        original stake, and self-corrects back to the base rate as soon
        as a losing streak erases the cushion.
        """

        def size_fn(equity, peak, _num_open):
            if mode == "throttle":
                drawdown = (equity - peak) / peak if peak > 0 else 0.0
                pct = self.position_size_pct * throttle_factor if drawdown <= -drawdown_threshold \
                    else self.position_size_pct
                return equity * pct
            if mode == "house_money":
                return self._house_money_size(equity, aggressive_pct)
            return equity * self.position_size_pct

        return self._run_entry_ordered(backtest_df, size_fn)

    def _house_money_size(self, equity: float, aggressive_pct: float) -> float:
        safe_base = min(equity, self.initial_capital)
        cushion = max(equity - self.initial_capital, 0.0)
        return safe_base * self.position_size_pct + cushion * aggressive_pct

    def simulate_concurrency_scaled(
        self,
        backtest_df: pd.DataFrame,
        *,
        decay_rate: float = 0.15,
        aggressive_pct: float = 0.08,
    ) -> pd.DataFrame:
        """Equity curve where new-trade size shrinks with how many positions are already open.

        Uses house-money sizing as the base rate, then divides it by
        (1 + decay_rate * num_open_positions) at the moment of entry, so
        capital spreads thinner during signal-dense stretches instead of
        assuming unlimited concurrent capacity, unlike every mode above.
        """

        def size_fn(equity, _peak, num_open):
            base_size = self._house_money_size(equity, aggressive_pct)
            return base_size / (1 + decay_rate * num_open)

        return self._run_entry_ordered(backtest_df, size_fn)

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
