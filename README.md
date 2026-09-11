# Clustered Insider Engine

A Python research pipeline for finding clusters of insider purchases and evaluating a rules-based trading strategy with historical market data.

## How it works

1. `universe_scanner.py` discovers tickers with multiple insider purchases.
2. `data_client.py` retrieves price, insider transaction, and fundamentals data from the Massive API and caches it locally as Parquet.
3. `signal_engine.py` combines insider-cluster, technical, and fundamental filters.
4. `backtester.py` simulates entries on the next open, trailing stops, and time-based exits.
5. `main.py` runs the full-market workflow and compares 30-, 60-, and 90-day holding periods.

## Setup

Use Python 3.9 or newer and create a local virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set `MASSIVE_API_KEY` in `.env`. Never commit that file.

## Run

Run the full pipeline from this directory:

```bash
python main.py
```

The pipeline requires network access and a valid Massive API key. It creates local files under `data/`, including ticker caches and `market_trade_results.parquet`. These generated files are intentionally excluded from Git and must be regenerated locally.

The API smoke test can be run with:

```bash
python test_api.py
```

## Project status

The repository currently contains the research engine and its diagnostic scripts. `test_api.py` is a live API check rather than a unit-test suite. Strategy results depend on the API data available at runtime and should not be treated as investment advice.
