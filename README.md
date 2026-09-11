# Clustered Insider Engine

A research-oriented Python pipeline for identifying clustered insider purchases and evaluating a rules-based trading strategy using historical market data.

This repository is intended for quantitative researchers and developers who want a reproducible, auditable pipeline for exploring how clustered insider transactions relate to subsequent stock performance.

Key goals:

- Discover and quantify clusters of insider purchases across public equities.
- Combine insider signals with technical and fundamental filters.
- Backtest a straightforward entry/exit rule set over historical market data.
- Produce reproducible artifacts (Parquet datasets, trade logs, and backtest summaries) for analysis.

---

Table of contents

- Features
- Design and architecture
- Quickstart
- Configuration
- Pipeline components
- Data inputs and caching
- Running the pipeline
- Output and analysis
- Tests and diagnostics
- Development notes
- Contributing
- License

---

Features

- Modular pipeline: scanning, data ingestion, signal generation, and backtesting are separated for clarity and reuse.
- Local Parquet caching for API data to enable fast repeated experiments.
- Configurable entry/exit rules with trailing-stop and time-based exits.
- Supports batch runs across the market universe and single-ticker investigations.

Design and architecture

The project is organized around a small set of Python modules that each have a single responsibility:

- universe_scanner.py — scan a list of tickers (or the entire available universe) to identify stocks with multiple insider purchases in a short time window (a "cluster").
- data_client.py — retrieves market prices, insider transactions, and fundamentals from the Massive API, and writes Parquet cache files under `data/`.
- signal_engine.py — implements the rules that combine insider-cluster detection with technical (e.g., moving averages, volume) and fundamental filters.
- backtester.py — simulates entries and exits, supports next-open entry, trailing stop logic, and time-based exits (e.g., 30/60/90 day holds).
- main.py — orchestrates a full-market run that glues the modules together and produces the final results.

Quickstart

Requirements

- Python 3.9+
- A Massive API key (set in the environment)

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# edit .env and set MASSIVE_API_KEY
```

Configuration

Copy `.env.example` to `.env` and add your API key. The `.env` file supports the following variables:

- MASSIVE_API_KEY — (required) API key for Massive.
- MASSIVE_BASE_URL — (optional) base URL for the Massive API (defaults to https://api.massive.com).

Important: do NOT commit `.env` or any secrets. `.gitignore` already excludes `data/` and `.env` files.

Pipeline components and recommended workflow

1. API smoke test

Use `test_api.py` to verify connectivity and credentials. Note: this is an integration/smoke check and not a unit test suite.

```bash
python test_api.py
```

2. Universe scanning

Run the scanner to identify candidate tickers with clustered insider purchases:

```bash
python universe_scanner.py
```

This produces a list of tickers (cached) that meet the cluster criteria.

3. Data collection

`data_client.py` downloads price history, insider filings/transactions, and fundamentals for selected tickers and writes Parquet files under `data/` for reproducible experiments.

4. Signal generation

`signal_engine.py` encodes the filters and signals to decide which clusters should produce trade entries.

5. Backtesting

`backtester.py` runs the strategy over historical data, supporting next-open entries, trailing stops, and time-based exits. Results are written to `data/market_trade_results.parquet` and auxiliary logs.

Running the full pipeline

To run the entire workflow end-to-end and produce comparative holding-period results (30/60/90 days):

```bash
python main.py
```

Outputs

- data/ — Parquet caches for tickers, market data, insider transactions, and the backtest trade results.
- market_trade_results.parquet — primary backtest output with one record per simulated trade.

Each Parquet includes timestamps and the source query metadata so runs are reproducible and auditable.

Data inputs and provenance

- Insider transactions, price history, and company fundamentals are retrieved from the Massive API. Query parameters and response timestamps are recorded in the local caches.
- The pipeline makes no attempt to normalize corporate actions beyond using the price history provided by the data source; users should be aware of possible delisted/merged tickers in historical windows.

Testing and diagnostics

- `test_api.py` — connectivity and credentials smoke test.
- The modules include logging statements; run with a higher log level to inspect step-by-step behavior.

Developer notes

- The project uses simple, well-documented Python code so it’s easy to adapt filters, add new features (e.g., risk sizing, portfolio-level simulation), or swap the data source.
- Parquet file layout is intentionally simple: one file per ticker per data type. This keeps read/update logic straightforward for iterative research.

Examples

- To run a single-ticker investigation, modify `main.py` or run the scanner and pass the cached ticker list to the downstream modules.
- To change the holding period or trailing-stop parameters, edit the constants in `backtester.py` or add a small config loader.

Contributing

Contributions are welcome. Please open issues for feature requests and bug reports. For larger changes, please open a pull request with tests or at least a short reproducible script demonstrating behavior.

License

This repository is provided for research purposes. Add your preferred license file if you plan to publish or share it publicly.

---

If you'd like, I can also:

- Add usage examples with expected CSV/Parquet schemas for downstream analysis tools (Pandas, DuckDB).
- Add a CI job to run the API smoke test (skipped by default) and basic linting.
- Create a CONTRIBUTING.md template and a LICENSE file.
