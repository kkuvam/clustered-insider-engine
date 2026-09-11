"""
Dynamic Universe Scanner for SEC Form 4 Filings.

Scans market-wide Form 4 filings via Massive REST API without hardcoding tickers.
Identifies candidate tickers with open-market purchases (transaction_code='P')
to construct a dynamic universe across thousands of stocks.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional
import pandas as pd

from config import DATA_DIR, StrategyConfig
from data_client import MassiveDataClient


class DynamicUniverseScanner:
    """Scans market-wide Form 4 filings to dynamically construct target universes."""

    def __init__(self, client: Optional[MassiveDataClient] = None, config: StrategyConfig = StrategyConfig()):
        self.client = client or MassiveDataClient()
        self.config = config
        self.cache_dir = DATA_DIR / "universe_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def discover_insider_candidate_tickers(
        self, start_date: str, end_date: str, min_purchases: int = 2
    ) -> List[str]:
        """
        Queries global SEC Form 4 filings across the entire market.
        Returns a list of unique tickers meeting the minimum insider purchase threshold.
        """
        cache_file = self.cache_dir / f"market_candidates_{start_date}_{end_date}.parquet"
        if cache_file.exists():
            print(f"Loading candidate tickers from local cache: {cache_file}")
            df_cached = pd.read_parquet(cache_file)
            return df_cached["ticker"].tolist()

        endpoint_url = f"{self.client.base_url}/stocks/filings/vX/form-4"
        params = {
            "filing_date.gte": start_date,
            "filing_date.lte": end_date,
            "transaction_code": "P",
            "limit": 10000,
            "sort": "filing_date.asc",
        }

        all_results = []
        next_url = endpoint_url
        current_params = params

        print(f"Scanning market-wide Form 4 filings ({start_date} to {end_date})...")

        while next_url:
            json_data = self.client._get_raw(next_url, params=current_params)
            results = json_data.get("results", [])
            all_results.extend(results)

            next_url = json_data.get("next_url")
            current_params = None

        if not all_results:
            print("No market-wide Form 4 transactions returned.")
            return []

        df = pd.DataFrame(all_results)

        # Handle list explosion if tickers column contains array of symbols
        if "tickers" in df.columns:
            df = df.explode("tickers").rename(columns={"tickers": "ticker"})

        if "ticker" not in df.columns:
            return []

        # Filter out empty or null tickers
        df = df.dropna(subset=["ticker"])
        df["ticker"] = df["ticker"].str.upper().str.strip()

        # Count distinct insider CIKs per ticker
        ticker_counts = (
            df.groupby("ticker")["owner_cik"]
            .nunique()
            .reset_index()
            .rename(columns={"owner_cik": "distinct_insiders"})
        )

        candidate_df = ticker_counts[ticker_counts["distinct_insiders"] >= min_purchases]
        candidate_tickers = candidate_df["ticker"].tolist()

        # Cache candidate list locally
        candidate_df.to_parquet(cache_file, index=False)
        print(f"Discovered {len(candidate_tickers)} active candidate tickers across the entire market.")

        return candidate_tickers