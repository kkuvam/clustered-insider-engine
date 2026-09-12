"""
Data Engineering Client updated to extract SEC Form 4 Rule 10b5-1 plan metadata.

Also adds two new cache-first endpoints, wired into load_dataset() but not yet
consumed by signal_engine.py:
  - fetch_short_interest(): GET /stocks/v1/short-interest (bi-weekly FINRA data)
  - fetch_earnings(): GET /benzinga/v1/earnings (partner add-on; requires Benzinga
    entitlement on your Massive plan -- returns an empty frame if not entitled,
    since _get_raw treats 401/402/403/404 as non-retryable)
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import pandas as pd
import requests

pd.set_option("future.no_silent_downcasting", True)

from config import DATA_DIR, MASSIVE_API_KEY, MASSIVE_BASE_URL


class MassiveDataClient:
    """Production REST API Client for Massive API with Parquet persistence."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        data_dir: Path = DATA_DIR,
    ):
        self.api_key = api_key or MASSIVE_API_KEY
        if not self.api_key:
            raise ValueError("MASSIVE_API_KEY is missing. Set it in your .env file or config.py.")

        self.base_url = (base_url or MASSIVE_BASE_URL).rstrip("/")
        self.data_dir = data_dir

        self.raw_prices_dir = self.data_dir / "raw_prices"
        self.raw_insider_dir = self.data_dir / "raw_insider"
        self.raw_fundamentals_dir = self.data_dir / "raw_fundamentals"
        self.raw_short_interest_dir = self.data_dir / "raw_short_interest"
        self.raw_earnings_dir = self.data_dir / "raw_earnings"

        for directory in [
            self.raw_prices_dir,
            self.raw_insider_dir,
            self.raw_fundamentals_dir,
            self.raw_short_interest_dir,
            self.raw_earnings_dir,
        ]:
            directory.mkdir(parents=True, exist_ok=True)

    def _get_raw(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        max_retries: int = 5,
        backoff_factor: float = 2.0,
    ) -> Dict[str, Any]:
        """Executes GET requests with exponential backoff for HTTP 429 rate limits."""
        query_params = dict(params or {})

        parsed_url = urlparse(url)
        url_params = parse_qs(parsed_url.query)
        if "apiKey" not in query_params and "apiKey" not in url_params:
            query_params["apiKey"] = self.api_key

        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=query_params, timeout=20)

                if response.status_code == 429:
                    time.sleep(backoff_factor**attempt)
                    continue

                if response.status_code in (401, 402, 403, 404):
                    # Auth/entitlement/not-found errors won't resolve on retry
                    # (e.g. a partner add-on endpoint your plan doesn't include)
                    return {}

                response.raise_for_status()
                return response.json()

            except requests.exceptions.RequestException as exc:
                if attempt == max_retries - 1:
                    return {}
                time.sleep(backoff_factor**attempt)

        return {}

    def fetch_ohlcv(self, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch daily split-adjusted OHLCV price history."""
        cache_file = self.raw_prices_dir / f"{ticker}_prices.parquet"
        if cache_file.exists():
            return pd.read_parquet(cache_file)

        endpoint_url = (
            f"{self.base_url}/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}"
        )
        params = {"adjusted": "true", "sort": "asc", "limit": 5000}

        all_results: List[Dict[str, Any]] = []
        next_url: Optional[str] = endpoint_url
        current_params: Optional[Dict[str, Any]] = params

        while next_url:
            json_data = self._get_raw(next_url, params=current_params)
            results = json_data.get("results", [])
            all_results.extend(results)

            next_url = json_data.get("next_url")
            current_params = None

        schema = ["date", "open", "high", "low", "close", "volume", "vwap"]
        if not all_results:
            empty_df = pd.DataFrame(columns=schema)
            empty_df.to_parquet(cache_file, index=False)
            return empty_df

        df = pd.DataFrame(all_results)
        field_mapping = {
            "t": "date", "o": "open", "h": "high",
            "l": "low", "c": "close", "v": "volume", "vw": "vwap",
        }
        df = df.rename(columns={k: v for k, v in field_mapping.items() if k in df.columns})

        df["date"] = pd.to_datetime(df["date"], unit="ms", errors="coerce").dt.tz_localize(None)
        df = df.dropna(subset=["date"]).reindex(columns=schema).sort_values("date").reset_index(drop=True)

        df.to_parquet(cache_file, index=False)
        return df

    def fetch_benchmark_ohlcv(self, start_date: str, end_date: str, ticker: str = "SPY") -> pd.DataFrame:
        """Fetch broad market index (SPY) for macro regime filtering."""
        return self.fetch_ohlcv(ticker, start_date, end_date)

    def fetch_insider_transactions(
        self, ticker: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Fetch SEC Form 4 filings including Rule 10b5-1 automated trading plan flags."""
        cache_file = self.raw_insider_dir / f"{ticker}_insider.parquet"
        if cache_file.exists():
            return pd.read_parquet(cache_file)

        endpoint_url = f"{self.base_url}/stocks/filings/vX/form-4"
        params = {
            "tickers": ticker,
            "filing_date.gte": start_date,
            "filing_date.lte": end_date,
            "transaction_code": "P",
            "limit": 1000,
            "sort": "filing_date.asc",
        }

        all_results: List[Dict[str, Any]] = []
        next_url: Optional[str] = endpoint_url
        current_params: Optional[Dict[str, Any]] = params

        while next_url:
            json_data = self._get_raw(next_url, params=current_params)
            results = json_data.get("results", [])
            all_results.extend(results)

            next_url = json_data.get("next_url")
            current_params = None

        schema = [
            "filing_date", "transaction_date", "insider_id",
            "transaction_code", "shares", "price", "value",
            "is_officer", "is_director", "is_ten_percent_owner", "officer_title",
            "is_10b5_1",
        ]
        if not all_results:
            empty_df = pd.DataFrame(columns=schema)
            empty_df.to_parquet(cache_file, index=False)
            return empty_df

        df = pd.DataFrame(all_results)
        field_mapping = {
            "filing_date": "filing_date",
            "transaction_date": "transaction_date",
            "owner_cik": "insider_id",
            "transaction_code": "transaction_code",
            "transaction_shares": "shares",
            "transaction_price_per_share": "price",
            "transaction_value": "value",
            "is_officer": "is_officer",
            "is_director": "is_director",
            "is_ten_percent_owner": "is_ten_percent_owner",
            "officer_title": "officer_title",
            "aff_10b5_one": "is_10b5_1",  # real Massive field name for the 10b5-1 plan flag
        }
        df = df.rename(columns={k: v for k, v in field_mapping.items() if k in df.columns})

        df["filing_date"] = pd.to_datetime(df["filing_date"], errors="coerce").dt.tz_localize(None)
        if "transaction_date" in df.columns:
            df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce").dt.tz_localize(None)

        df = df.dropna(subset=["filing_date"])

        if "transaction_code" in df.columns:
            df = df[df["transaction_code"] == "P"].copy()

        # Sanitize boolean metadata flags
        for col in ["is_officer", "is_director", "is_ten_percent_owner", "is_10b5_1"]:
            if col not in df.columns:
                df[col] = False
            else:
                df[col] = df[col].fillna(False).astype(bool)

        if "officer_title" not in df.columns:
            df["officer_title"] = ""

        df = df.reindex(columns=schema).sort_values("filing_date").reset_index(drop=True)
        df.to_parquet(cache_file, index=False)
        return df

    def fetch_fundamentals(
        self, ticker: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Fetch daily financial ratios via GET /stocks/financials/v1/ratios."""
        cache_file = self.raw_fundamentals_dir / f"{ticker}_fundamentals.parquet"
        if cache_file.exists():
            return pd.read_parquet(cache_file)

        endpoint_url = f"{self.base_url}/stocks/financials/v1/ratios"
        params = {"ticker": ticker, "limit": 1000}

        all_results: List[Dict[str, Any]] = []
        next_url: Optional[str] = endpoint_url
        current_params: Optional[Dict[str, Any]] = params

        while next_url:
            json_data = self._get_raw(next_url, params=current_params)
            results = json_data.get("results", [])
            all_results.extend(results)

            next_url = json_data.get("next_url")
            current_params = None

        schema = ["date", "market_cap", "debt_to_equity"]
        if not all_results:
            empty_df = pd.DataFrame(columns=schema)
            empty_df.to_parquet(cache_file, index=False)
            return empty_df

        df = pd.DataFrame(all_results)
        field_mapping = {
            "date": "date",
            "market_cap": "market_cap",
            "debt_to_equity": "debt_to_equity",
        }
        df = df.rename(columns={k: v for k, v in field_mapping.items() if k in df.columns})

        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.tz_localize(None)
            df = df.dropna(subset=["date"])
            df = df[
                (df["date"] >= pd.to_datetime(start_date))
                & (df["date"] <= pd.to_datetime(end_date))
            ]

        df = df.reindex(columns=schema).sort_values("date").reset_index(drop=True)
        df.to_parquet(cache_file, index=False)
        return df

    def fetch_short_interest(
        self, ticker: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Fetch bi-weekly FINRA short interest via GET /stocks/v1/short-interest."""
        cache_file = self.raw_short_interest_dir / f"{ticker}_short_interest.parquet"
        if cache_file.exists():
            return pd.read_parquet(cache_file)

        endpoint_url = f"{self.base_url}/stocks/v1/short-interest"
        params = {
            "ticker": ticker,
            "settlement_date.gte": start_date,
            "settlement_date.lte": end_date,
            "limit": 1000,
            "sort": "settlement_date.asc",
        }

        all_results: List[Dict[str, Any]] = []
        next_url: Optional[str] = endpoint_url
        current_params: Optional[Dict[str, Any]] = params

        while next_url:
            json_data = self._get_raw(next_url, params=current_params)
            results = json_data.get("results", [])
            all_results.extend(results)

            next_url = json_data.get("next_url")
            current_params = None

        schema = ["ticker", "settlement_date", "short_interest", "avg_daily_volume", "days_to_cover"]
        if not all_results:
            empty_df = pd.DataFrame(columns=schema)
            empty_df.to_parquet(cache_file, index=False)
            return empty_df

        df = pd.DataFrame(all_results).reindex(columns=schema)
        df["settlement_date"] = pd.to_datetime(df["settlement_date"], errors="coerce").dt.tz_localize(None)
        df = df.dropna(subset=["settlement_date"]).sort_values("settlement_date").reset_index(drop=True)

        df.to_parquet(cache_file, index=False)
        return df

    def fetch_earnings(self, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch historical earnings events via GET /benzinga/v1/earnings.

        Partner add-on endpoint -- if your Massive plan lacks Benzinga
        entitlement this returns an empty frame rather than raising.
        """
        cache_file = self.raw_earnings_dir / f"{ticker}_earnings.parquet"
        if cache_file.exists():
            return pd.read_parquet(cache_file)

        endpoint_url = f"{self.base_url}/benzinga/v1/earnings"
        params = {
            "ticker": ticker,
            "date.gte": start_date,
            "date.lte": end_date,
            "limit": 1000,
            "sort": "date.asc",
        }

        all_results: List[Dict[str, Any]] = []
        next_url: Optional[str] = endpoint_url
        current_params: Optional[Dict[str, Any]] = params

        while next_url:
            json_data = self._get_raw(next_url, params=current_params)
            results = json_data.get("results", [])
            all_results.extend(results)

            next_url = json_data.get("next_url")
            current_params = None

        schema = [
            "ticker", "date", "fiscal_period", "fiscal_year", "date_status",
            "actual_eps", "estimated_eps", "eps_surprise_percent",
            "actual_revenue", "estimated_revenue", "revenue_surprise_percent",
        ]
        if not all_results:
            empty_df = pd.DataFrame(columns=schema)
            empty_df.to_parquet(cache_file, index=False)
            return empty_df

        df = pd.DataFrame(all_results).reindex(columns=schema)
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.tz_localize(None)
        df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

        df.to_parquet(cache_file, index=False)
        return df

    def load_dataset(
        self, ticker: str, start_date: str = "2021-01-01", end_date: str = "2026-01-01"
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        price_df = self.fetch_ohlcv(ticker, start_date, end_date)
        insider_df = self.fetch_insider_transactions(ticker, start_date, end_date)
        fund_df = self.fetch_fundamentals(ticker, start_date, end_date)
        short_interest_df = self.fetch_short_interest(ticker, start_date, end_date)
        earnings_df = self.fetch_earnings(ticker, start_date, end_date)
        return price_df, insider_df, fund_df, short_interest_df, earnings_df