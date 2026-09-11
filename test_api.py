"""
Diagnostic Script: Massive REST API Key and Endpoint Connectivity Check.
Tests basic authentication and endpoint validity with minimal query parameters.
"""
import os
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

API_KEY = os.getenv("MASSIVE_API_KEY")
BASE_URL = os.getenv("MASSIVE_BASE_URL", "https://api.massive.com").rstrip("/")

if not API_KEY:
    raise ValueError("MASSIVE_API_KEY is not set in environment or .env file.")

print("Testing Massive API authentication")
print(f"Base URL: {BASE_URL}\n" + "=" * 50)


def test_endpoint(name: str, url: str, params: dict):
    """Executes a simple GET request and prints status response."""
    params["apiKey"] = API_KEY
    try:
        response = requests.get(url, params=params, timeout=10)
        print(f"[{name}]")
        print(f"  URL: {response.url}")
        print(f"  Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            results_count = len(data.get("results", []))
            print(f"  Success! Returned {results_count} results.")
        else:
            print(f"  Failed! Response: {response.text}")
    except Exception as exc:
        print(f"  Exception occurred: {exc}")
    print("-" * 50)


# 1. Test Aggregates (OHLCV) Endpoint
test_endpoint(
    name="1. Daily Aggregates (OHLCV)",
    url=f"{BASE_URL}/v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-01-10",
    params={"adjusted": "true", "limit": 5},
)

# 2. Test Ratios Endpoint
test_endpoint(
    name="2. Financial Ratios",
    url=f"{BASE_URL}/stocks/financials/v1/ratios",
    params={"ticker": "AAPL", "limit": 1},
)

# 3. Test Form 4 Endpoint (v1 vs vX check)
test_endpoint(
    name="3. Form 4 Insider Filings (v1)",
    url=f"{BASE_URL}/stocks/filings/vX/form-4",
    params={"tickers": "AAPL", "limit": 1},
)