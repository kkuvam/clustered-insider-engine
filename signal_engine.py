"""
Vectorized Signal Engine with Cluster Dollar Volume Filtering.
"""
from __future__ import annotations
import pandas as pd
from config import StrategyConfig


class VectorizedSignalEngine:
    def __init__(self, config: StrategyConfig = StrategyConfig()):
        self.config = config

    def generate_signals(
        self,
        price_df: pd.DataFrame,
        insider_df: pd.DataFrame,
        fund_df: pd.DataFrame,
    ) -> pd.DataFrame:
        df = price_df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df.sort_values("date").reset_index(drop=True)

        # 1. Technical Filter (SMA50 + Price Floor)
        df["sma_50"] = df["close"].rolling(window=self.config.SMA_PERIOD).mean()
        df["pass_technical"] = (df["close"] > df["sma_50"]) & (df["close"] >= self.config.MIN_PRICE)

        # 2. Insider Buying Aggregation (Unique CIKs + Combined Dollar Volume)
        insider = insider_df.copy()
        if not insider.empty:
            insider["filing_date"] = pd.to_datetime(insider["filing_date"]).dt.tz_localize(None)

            if "transaction_code" in insider.columns:
                insider = insider[insider["transaction_code"] == "P"].copy()

            # Ensure value calculation exists (shares * price per share)
            if "value" not in insider.columns or insider["value"].isna().all():
                insider["value"] = insider["shares"] * insider["price"]
            else:
                insider["value"] = insider["value"].fillna(insider["shares"] * insider["price"])

            trading_days = df[["date"]].drop_duplicates().sort_values("date")
            insider = pd.merge_asof(
                insider.sort_values("filing_date"),
                trading_days.rename(columns={"date": "trading_date"}),
                left_on="filing_date",
                right_on="trading_date",
                direction="forward",
            )

            insider = insider.dropna(subset=["trading_date"])

            # Aggregate both distinct insider count AND total purchase dollar volume per day
            daily_insider_stats = (
                insider.groupby("trading_date")
                .agg(
                    daily_unique_buyers=("insider_id", "nunique"),
                    daily_total_value=("value", "sum"),
                )
                .reset_index()
                .rename(columns={"trading_date": "date"})
            )

            df = df.merge(daily_insider_stats, on="date", how="left")
            df["daily_unique_buyers"] = df["daily_unique_buyers"].fillna(0)
            df["daily_total_value"] = df["daily_total_value"].fillna(0.0)
        else:
            df["daily_unique_buyers"] = 0
            df["daily_total_value"] = 0.0

        # Vectorized Rolling Sums over Cluster Window
        df["rolling_insider_cluster"] = (
            df["daily_unique_buyers"]
            .rolling(window=self.config.CLUSTER_WINDOW_DAYS, min_periods=1)
            .sum()
        )
        df["rolling_cluster_value"] = (
            df["daily_total_value"]
            .rolling(window=self.config.CLUSTER_WINDOW_DAYS, min_periods=1)
            .sum()
        )

        # Cluster Valid: Unique Insiders >= Threshold AND Combined Volume >= Min Cluster Value
        df["pass_insider_cluster"] = (
            (df["rolling_insider_cluster"] >= self.config.MIN_DISTINCT_INSIDERS)
            & (df["rolling_cluster_value"] >= self.config.MIN_CLUSTER_VALUE)
        )

        # 3. Fundamental Filter
        if not fund_df.empty:
            fund = fund_df.copy()
            fund["date"] = pd.to_datetime(fund["date"]).dt.tz_localize(None)
            df = df.merge(fund, on="date", how="left").ffill()

            de_pass = df["debt_to_equity"].isna() | (df["debt_to_equity"] < self.config.MAX_DEBT_TO_EQUITY)
            mcap_pass = df["market_cap"].isna() | (df["market_cap"] > self.config.MIN_MARKET_CAP)

            df["pass_fundamental"] = de_pass & mcap_pass
        else:
            df["pass_fundamental"] = True

        # 4. Composite Validation
        df["signal_valid"] = (
            df["pass_insider_cluster"]
            & df["pass_technical"]
            & df["pass_fundamental"]
        )

        return df