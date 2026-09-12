"""
Vectorized Signal Engine with Cluster Dollar Volume Filtering.

Tier-1 refinements to the core clustered-insider-buying signal:
  - Opportunistic vs. routine insider classification (Cohen/Malloy/Pomorski):
    an insider is "routine" for a calendar month if they've bought in that
    same month in >= ROUTINE_MIN_YEARS distinct years; routine buys carry no
    signal and are dropped before clustering. This also proxies for excluding
    Rule 10b5-1 scheduled-plan trades, since no plan-type flag exists in the
    local dataset.

Tier-2 refinement applied:
  - Self-relative conviction sizing (proxy for size-relative-to-comp/net-worth,
    which needs compensation data this dataset doesn't have): a buy only
    counts toward the cluster if its dollar value is >= CONVICTION_SIZE_MULTIPLIER
    times that same insider's own historical average buy at this ticker. An
    insider buying far more than their own norm is a stronger conviction
    signal than an arbitrary flat dollar threshold. Insiders with no prior
    purchase history (nothing to compare against) pass by default.

Tier-2 refinement applied:
  - Short-interest overlap: a signal only counts if the stock's most recent
    FINRA short-interest reading (days-to-cover) at signal time is >=
    MIN_DAYS_TO_COVER. Insider buying against a crowded short is a stronger
    contrarian signal than insider buying alone.

Tier-2 NOT implemented: earnings-proximity timing has no supporting data,
since the account's Massive plan lacks the Benzinga earnings entitlement
(fetch_earnings() always returns empty).
Not implemented:
  - Small-cap / low-coverage tilt — every local raw_fundamentals/*.parquet
    file has the right schema but 0 rows (confirmed across all 4,489 files),
    so any market_cap-based filter is a permanent no-op against this dataset.
    Dropped rather than kept as dead weight.
  - Role-weighted conviction (CEO/CFO vs. director) — the local insider
    dataset has no officer/director/title field to weight by.

Removed: the old SMA-50 technical trend filter and debt-to-equity filter
(neither was part of the Tier-1 set). Core cluster-window mechanic (rolling
distinct-insider count + rolling dollar volume vs. threshold) is unchanged.
"""
from __future__ import annotations
import pandas as pd
from config import StrategyConfig


class VectorizedSignalEngine:
    def __init__(self, config: StrategyConfig = StrategyConfig()):
        self.config = config

    def _flag_opportunistic(self, insider: pd.DataFrame) -> pd.DataFrame:
        """Marks each purchase opportunistic (unscheduled) vs. routine."""
        df = insider.copy()
        df["_year"] = df["filing_date"].dt.year
        df["_month"] = df["filing_date"].dt.month

        year_counts = df.groupby(["insider_id", "_month"])["_year"].nunique()
        routine_keys = set(year_counts[year_counts >= self.config.ROUTINE_MIN_YEARS].index)

        df["is_opportunistic"] = ~df.set_index(["insider_id", "_month"]).index.isin(routine_keys)
        return df.drop(columns=["_year", "_month"])

    def _flag_high_conviction(self, insider: pd.DataFrame) -> pd.DataFrame:
        """Marks each purchase high-conviction if it dwarfs the insider's own norm.

        Compares each buy's dollar value to that insider's own prior average
        buy value at this ticker (expanding mean, excluding the current
        trade, so no lookahead). Insiders with no prior purchase pass by
        default since there's no baseline to compare against.
        """
        df = insider.sort_values(["insider_id", "filing_date"]).reset_index(drop=True)
        prior_avg_value = df.groupby("insider_id")["value"].transform(
            lambda s: s.shift(1).expanding().mean()
        )
        df["is_high_conviction"] = (
            prior_avg_value.isna()
            | (df["value"] >= self.config.CONVICTION_SIZE_MULTIPLIER * prior_avg_value)
        )
        return df

    def _flag_short_interest_overlap(self, df: pd.DataFrame, short_interest_df: pd.DataFrame) -> pd.DataFrame:
        """Attaches each day's most recent known days-to-cover reading.

        FINRA short interest is reported bi-weekly, so this carries the last
        known settlement_date reading forward to every trading day, avoiding
        lookahead by only looking backward in time (direction="backward").
        """
        if short_interest_df.empty:
            df["days_to_cover"] = pd.NA
            df["pass_short_interest"] = True
            return df

        short_interest = short_interest_df.copy()
        short_interest["settlement_date"] = pd.to_datetime(
            short_interest["settlement_date"]
        ).dt.tz_localize(None)
        short_interest = short_interest.sort_values("settlement_date")

        df = pd.merge_asof(
            df.sort_values("date"),
            short_interest[["settlement_date", "days_to_cover"]],
            left_on="date",
            right_on="settlement_date",
            direction="backward",
        ).drop(columns=["settlement_date"])

        df["pass_short_interest"] = (
            df["days_to_cover"].isna() | (df["days_to_cover"] >= self.config.MIN_DAYS_TO_COVER)
        )
        return df

    def generate_signals(
        self,
        price_df: pd.DataFrame,
        insider_df: pd.DataFrame,
        fund_df: pd.DataFrame,
        short_interest_df: pd.DataFrame,
    ) -> pd.DataFrame:
        df = price_df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df.sort_values("date").reset_index(drop=True)

        # 1. Insider Buying Aggregation (Opportunistic Unique CIKs + Combined $ Volume)
        insider = insider_df.copy()
        if not insider.empty:
            insider["filing_date"] = pd.to_datetime(insider["filing_date"]).dt.tz_localize(None)

            if "transaction_code" in insider.columns:
                insider = insider[insider["transaction_code"] == "P"].copy()

            if "value" not in insider.columns or insider["value"].isna().all():
                insider["value"] = insider["shares"] * insider["price"]
            else:
                insider["value"] = insider["value"].fillna(insider["shares"] * insider["price"])

            # Tier-1: drop routine/scheduled insiders before clustering
            insider = self._flag_opportunistic(insider)
            insider = insider[insider["is_opportunistic"]].copy()

            # Tier-2: drop buys that aren't large relative to the insider's own norm
            insider = self._flag_high_conviction(insider)
            insider = insider[insider["is_high_conviction"]].copy()

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

        # Vectorized Rolling Sums over Cluster Window (core mechanic, unchanged)
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

        # Cluster Valid: Opportunistic Unique Insiders >= Threshold AND Combined Volume >= Min
        df["pass_insider_cluster"] = (
            (df["rolling_insider_cluster"] >= self.config.MIN_DISTINCT_INSIDERS)
            & (df["rolling_cluster_value"] >= self.config.MIN_CLUSTER_VALUE)
        )

        # 2. Tier-2: Short-Interest Overlap
        df = self._flag_short_interest_overlap(df, short_interest_df)

        # 3. Composite Validation
        # fund_df is accepted for interface compatibility but unused: every local
        # raw_fundamentals file is schema-correct but empty (0 rows), so any
        # market-cap-based filter would be a permanent no-op against this dataset.
        df["signal_valid"] = df["pass_insider_cluster"] & df["pass_short_interest"]

        return df
