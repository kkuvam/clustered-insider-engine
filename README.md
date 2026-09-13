# Clustered Insider Engine

A research-oriented Python pipeline for identifying clustered insider purchases and evaluating a rules-based trading strategy using historical market data.

This repository is intended for quantitative researchers and developers who want a reproducible, auditable pipeline for exploring how clustered insider transactions relate to subsequent stock performance.

See `research_report.md` for the full research report, `decision_log.csv` for the dated record of decisions made during the project, and `experiment_record.csv` for every experiment tested, including the ones that were rejected. Reproducible sweep and validation code lives under `experiments/`.

## Key Goals

- Discover and quantify clusters of insider purchases across public equities.
- Combine insider signals with conviction, short-interest, and liquidity filters (see `research_report.md` for why fundamental and technical-trend filters were tried and dropped).
- Backtest a straightforward entry/exit rule set over historical market data.
- Produce reproducible artifacts (Parquet datasets, trade logs, and backtest summaries) for analysis.

---

## Table of Contents

- [Features](#features)
- [Design and Architecture](#design-and-architecture)
- [Quickstart](#quickstart)
- [Configuration](#configuration)
- [Pipeline Components and Recommended Workflow](#pipeline-components-and-recommended-workflow)
- [Running the Full Pipeline](#running-the-full-pipeline)
- [Output and Analysis](#output-and-analysis)
- [Testing and Diagnostics](#testing-and-diagnostics)
- [Experiments](#experiments)
- [Development Notes](#development-notes)
- [Contributing](#contributing)
- [Assistance and Source Disclosure](#assistance-and-source-disclosure)
- [License](#license)

---

## Features

- Modular pipeline: scanning, data ingestion, signal generation, and backtesting are separated for clarity and reuse.
- Local Parquet caching for API data to enable fast repeated experiments.
- Configurable entry/exit rules with trailing-stop and time-based exits.
- Supports batch runs across the market universe and single-ticker investigations.

## Design and Architecture

The project is organized around a small set of Python modules that each have a single responsibility:

- universe_scanner.py — scan a list of tickers (or the entire available universe) to identify stocks with multiple insider purchases in a short time window (a "cluster").
- data_client.py — retrieves market prices, insider transactions, and fundamentals from the Massive API, and writes Parquet cache files under `data/`.
- signal_engine.py — implements the rules that combine insider-cluster detection with opportunistic-vs-routine classification, conviction sizing, short-interest overlap, and a dollar-volume liquidity ceiling (see the module docstring for filters that were tried and dropped, including fundamentals and a moving-average trend filter).
- backtester.py — simulates entries and exits, supports next-open entry, trailing stop logic, and time-based exits (e.g., 30/60/90 day holds).
- main.py — orchestrates a full-market run that glues the modules together and produces the final results.

## Quickstart

### Requirements

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

## Configuration

Copy `.env.example` to `.env` and add your API key. The `.env` file supports the following variables:

- MASSIVE_API_KEY — (required) API key for Massive.
- MASSIVE_BASE_URL — (optional) base URL for the Massive API (defaults to https://api.massive.com).

Important: do NOT commit `.env` or any secrets. `.gitignore` already excludes `data/` and `.env` files.

## Pipeline Components and Recommended Workflow

### 1. API Smoke Test

Use `test_api.py` to verify connectivity and credentials. Note: this is an integration/smoke check and not a unit test suite.

```bash
python test_api.py
```

### 2. Universe Scanning

Run the scanner to identify candidate tickers with clustered insider purchases:

```bash
python universe_scanner.py
```

This produces a list of tickers (cached) that meet the cluster criteria.

### 3. Data Collection

`data_client.py` downloads price history, insider filings/transactions, and fundamentals for selected tickers and writes Parquet files under `data/` for reproducible experiments.

### 4. Signal Generation

`signal_engine.py` encodes the filters and signals to decide which clusters should produce trade entries.

### 5. Backtesting

`backtester.py` runs the strategy over historical data, supporting next-open entries, trailing stops, and time-based exits. Results are written to `data/market_trade_results.parquet` and auxiliary logs.

## Running the Full Pipeline

To run the entire workflow end-to-end and produce comparative holding-period results (30/60/90 days):

```bash
python main.py
```

## Output and Analysis

### Outputs

- data/ — Parquet caches for tickers, market data, insider transactions, and the backtest trade results.
- market_trade_results.parquet — primary backtest output with one record per simulated trade.

Each Parquet includes timestamps and the source query metadata so runs are reproducible and auditable.

### Data Inputs and Provenance

- Insider transactions, price history, and company fundamentals are retrieved from the Massive API. Query parameters and response timestamps are recorded in the local caches.
- The pipeline makes no attempt to normalize corporate actions beyond using the price history provided by the data source; users should be aware of possible delisted/merged tickers in historical windows.

## Testing and Diagnostics

- `test_api.py` — connectivity and credentials smoke test.
- The modules include logging statements; run with a higher log level to inspect step-by-step behavior.

## Experiments

`experiments/` holds reproducible scripts for the sweeps and validation checks described in `research_report.md`, built on the same cached data and the same production classes as `main.py`:

```bash
python experiments/exit_and_sizing_sweep.py   # exit-mechanic and sizing-mode sweep
python experiments/validation_suite.py        # chronological split, bootstrap CI, cost stress test, cash check
```

Each script writes its own results CSV next to itself (`exit_and_sizing_results.csv`, `validation_results.csv`).

## Development Notes

- The project uses simple, well-documented Python code so it’s easy to adapt filters, add new features (e.g., risk sizing, portfolio-level simulation), or swap the data source.
- Parquet file layout is intentionally simple: one file per ticker per data type. This keeps read/update logic straightforward for iterative research.

### Examples

- To run a single-ticker investigation, modify `main.py` or run the scanner and pass the cached ticker list to the downstream modules.
- To change the holding period or trailing-stop parameters, edit the constants in `backtester.py` or add a small config loader.

## Contributing

Contributions are welcome. Please open issues for feature requests and bug reports. For larger changes, please open a pull request with tests or at least a short reproducible script demonstrating behavior.

## Assistance and Source Disclosure

- Author's own work: the author owns every hypothesis, every parameter and risk-management choice, and the decision to accept or reject each experiment's result throughout the project.
- Academic paper consulted: Cohen, Malloy, and Pomorski, "Decoding Inside Information," Journal of Finance, 2012, for the opportunistic-vs-routine insider classification used in `signal_engine.py`.
- External datasets: none integrated. Massive is the sole data source for every filter and signal in production. Financial Modeling Prep, Finnhub, and Polygon.io were researched as alternative fundamentals providers once the Massive fundamentals endpoint returned empty for every ticker, but none was wired into the pipeline.
- Existing code or tutorials consulted: none beyond the Massive API's own documentation.
- AI tools: Google Gemini was used with the author to generate and edit the original baseline pipeline (`universe_scanner.py`, `data_client.py`, `signal_engine.py`, `backtester.py`, `main.py`, and the first version of this README). From that baseline onward, Claude Code (Anthropic) was used to implement the Tier-1/Tier-2 signal filters and every later filter, to run the exit-mechanic, position-sizing, and validation experiments the author directed, to find and fix the look-ahead sizing bug described in `research_report.md`, and to draft `research_report.md`, `decision_log.csv`, and `experiment_record.csv` from the author's decisions and working notes. The author directed and is responsible for every part of this submission.
- Other human assistance: none.

## License

This repository is provided for research purposes. Add your preferred license file if you plan to publish or share it publicly.
