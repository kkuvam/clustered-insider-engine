"""
Event-Aligned Backtesting Engine with Stop-Loss and Take-Profit Execution.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


class EventBacktester:
    def __init__(
        self,
        holding_period_days: int = 30,
        stop_loss_pct: float | None = 0.15,
        take_profit_pct: float | None = None,
        trailing_stop: bool = True,
    ):
        """
        :param holding_period_days: Maximum holding period in trading days.
        :param stop_loss_pct: Drawdown percentage threshold that exits the trade.
        :param take_profit_pct: Gain percentage threshold that exits the trade. None disables it.
        :param trailing_stop: True ratchets the stop up from the post-entry high-water mark.
            False fixes the stop at stop_loss_pct below the entry price for the whole trade.
        """
        self.holding_period = holding_period_days
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.trailing_stop = trailing_stop

    def execute_trades(self, signal_df: pd.DataFrame) -> pd.DataFrame:
        if signal_df.empty:
            return signal_df

        df = signal_df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df.sort_values(["ticker", "date"]).reset_index(drop=True)

        n_rows = len(df)
        entry_trades = np.zeros(n_rows, dtype=bool)
        entry_prices = np.full(n_rows, np.nan)
        exit_prices = np.full(n_rows, np.nan)
        trade_returns = np.full(n_rows, np.nan)
        exit_reasons = [None] * n_rows

        for ticker, group in df.groupby("ticker", sort=False):
            indices = group.index.to_numpy()
            n = len(group)

            signals = group["signal_valid"].to_numpy()
            opens = group["open"].to_numpy()
            closes = group["close"].to_numpy()
            highs = group["high"].to_numpy() if "high" in group.columns else closes
            lows = group["low"].to_numpy() if "low" in group.columns else closes

            i = 0
            while i < n - 1:
                if signals[i]:
                    entry_idx = i + 1
                    if entry_idx >= n:
                        break

                    entry_p = opens[entry_idx]
                    if np.isnan(entry_p) or entry_p <= 0:
                        i += 1
                        continue

                    max_exit_idx = min(entry_idx + self.holding_period, n - 1)
                    actual_exit_idx = max_exit_idx
                    exit_p = closes[max_exit_idx]
                    reason = "TIME_EXIT"

                    # Stop-Loss and Take-Profit Execution Logic
                    if self.stop_loss_pct is not None or self.take_profit_pct is not None:
                        peak_price = max(entry_p, highs[entry_idx])
                        fixed_stop_price = (
                            entry_p * (1.0 - self.stop_loss_pct)
                            if self.stop_loss_pct is not None
                            else None
                        )
                        take_profit_price = (
                            entry_p * (1.0 + self.take_profit_pct)
                            if self.take_profit_pct is not None
                            else None
                        )

                        for day in range(entry_idx, max_exit_idx + 1):
                            # Update running peak price (High Water Mark)
                            current_high = highs[day]
                            if current_high > peak_price:
                                peak_price = current_high

                            # Stop price: ratchets up with the peak if trailing, else fixed at entry
                            if self.stop_loss_pct is not None:
                                stop_price = (
                                    peak_price * (1.0 - self.stop_loss_pct)
                                    if self.trailing_stop
                                    else fixed_stop_price
                                )
                                if lows[day] <= stop_price:
                                    actual_exit_idx = day
                                    exit_p = stop_price
                                    reason = "STOP_LOSS"
                                    break

                            # Take-profit checked after stop-loss on the same day (conservative)
                            if take_profit_price is not None and current_high >= take_profit_price:
                                actual_exit_idx = day
                                exit_p = take_profit_price
                                reason = "TAKE_PROFIT"
                                break

                    ret = (exit_p - entry_p) / entry_p

                    # Record trade state
                    global_entry_idx = indices[entry_idx]
                    entry_trades[global_entry_idx] = True
                    entry_prices[global_entry_idx] = entry_p
                    exit_prices[global_entry_idx] = exit_p
                    trade_returns[global_entry_idx] = ret
                    exit_reasons[global_entry_idx] = reason

                    # Fast-forward pointer to clear active trade window
                    i = actual_exit_idx
                else:
                    i += 1

        df["entry_trade"] = entry_trades
        df["entry_price"] = entry_prices
        df["exit_price"] = exit_prices
        df["trade_return"] = trade_returns
        df["exit_reason"] = exit_reasons

        return df

    def compute_performance(
        self, backtest_df: pd.DataFrame, max_return_threshold: float = 5.0
    ) -> dict:
        if "trade_return" not in backtest_df.columns:
            trades = pd.Series(dtype=float)
        else:
            trades = backtest_df["trade_return"].dropna()

        anomalies = trades[trades > max_return_threshold]
        if not anomalies.empty:
            print(
                f"[Warning] Filtered {len(anomalies)} split anomaly trade(s) exceeding {max_return_threshold * 100:.0f}% return."
            )
            trades = trades[trades <= max_return_threshold]

        if trades.empty:
            return {
                "holding_period": self.holding_period,
                "total_trades": 0,
                "win_rate": 0.0,
                "mean_return": 0.0,
                "std_return": 0.0,
                "sharpe_ratio": 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "max_gain": 0.0,
                "max_loss": 0.0,
            }

        win_rate = float((trades > 0).mean())
        mean_return = float(trades.mean())
        std_return = float(trades.std())

        sharpe = (
            float(mean_return / std_return * np.sqrt(252 / self.holding_period))
            if std_return > 0
            else 0.0
        )

        gross_gains = trades[trades > 0].sum()
        gross_losses = abs(trades[trades < 0].sum())
        profit_factor = (
            float(gross_gains / gross_losses) if gross_losses > 0 else np.nan
        )

        cum_returns = (1 + trades).cumprod()
        peak = cum_returns.cummax()
        drawdowns = (cum_returns - peak) / peak
        max_drawdown = float(drawdowns.min()) if not drawdowns.empty else 0.0

        return {
            "holding_period": self.holding_period,
            "total_trades": int(len(trades)),
            "win_rate": win_rate,
            "mean_return": mean_return,
            "std_return": std_return,
            "sharpe_ratio": sharpe,
            "profit_factor": profit_factor,
            "max_drawdown": max_drawdown,
            "max_gain": float(trades.max()),
            "max_loss": float(trades.min()),
        }