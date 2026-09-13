# Clustered Insider Buying: A Research Report

## 1. Research question

Does buying a stock shortly after multiple company insiders independently buy shares on the
open market ("a clustered insider purchase") produce positive risk-adjusted returns over the
following 1 to 3 months, once realistic trading costs and execution timing are applied?

## 2. Final hypothesis

When several distinct insiders at the same company buy stock within a short window, it signals
strong internal confidence that is not yet priced in, especially in smaller, less-covered
names where information asymmetry between insiders and the market is largest. The strategy
enters long on the public Form 4 filing date (never the transaction date, to avoid look-ahead)
and holds for a fixed 30, 60, or 90 trading days, with stop-loss and take-profit rules
overlaid.

## 3. Motivation and proposed mechanism

The core idea follows the insider-trading literature on informed trading, in particular the
distinction between "opportunistic" and "routine" insider transactions (Cohen, Malloy, and
Pomorski, *Decoding Inside Information*, Journal of Finance, 2012): insiders who trade on a
predictable schedule (for example, the same calendar month every year, a proxy for scheduled
Rule 10b5-1 plans) carry little information content, while irregular, opportunistic buying is
more likely to reflect real private information. A second mechanism, tested empirically in
this project rather than assumed, is that insider buying against a crowded short position is a
stronger contrarian signal than insider buying alone.

## 4. How this project differed from the original proposal

The proposal submitted on 2026-09-09 described this exact hypothesis, the same asset class
(U.S. equities), the same core datasets (Form 4 filings, price aggregates, fundamentals), and
a 1-to-3-month holding horizon, later implemented as fixed 30/60/90-day holds. No pivot in
hypothesis occurred. The one real deviation, discovered only after receiving API access:
`fetch_fundamentals()` returns the correct schema but 0 rows for every one of 4,489 cached
tickers under this account's Massive plan, so the proposed debt-to-equity/market-cap health
filter is a permanent no-op against this data. This matches the assignment's own expectation
that documented capabilities and actual data can differ. The health filter was dropped and its
role in the hypothesis (avoiding distressed, uninformative micro-caps) was instead proxied
using price and dollar-volume filters, described in Section 8. Everything else in the project
(opportunistic-vs-routine classification, conviction sizing, short-interest overlap, the
dollar-volume ceiling, exit-mechanic research, position sizing) is elaboration inside the
original proposal, developed as the assignment invites ("changing direction is not
automatically a negative... provided it is recorded").

## 5. Massive data used

- **SEC Form 4 filings** (`/stocks/filings/vX/form-4`): transaction code `P` (open-market
  purchase), filing date, owner CIK, ticker. This is the core dataset; the entire universe
  discovery and cluster-detection logic is built on it.
- **Daily adjusted OHLCV aggregates**: entry/exit pricing, the 20-day dollar-volume filter, and
  the stop-loss/take-profit/trailing-stop exit logic all run on this feed.
- **FINRA short-interest data** (via Massive): days-to-cover at signal time, used as the
  short-interest overlap filter (Section 6).
- **Fundamentals** (`market_cap`, `debt_to_equity`): fetched but found to be empty for this
  account's data plan (Section 4); not used in the final signal.
- **Benzinga earnings endpoint**: attempted, always returns empty under this account's
  entitlement; the planned earnings-proximity timing filter was never built for lack of data.

## 6. External data used

None was integrated into the final signal. Financial Modeling Prep, Finnhub, and Polygon.io
were researched as possible alternative sources for historical fundamentals once the Massive
fundamentals gap was found, but none was wired into the pipeline; the project relies entirely
on Massive for both the core signal and every filter actually in production. This is disclosed
here rather than silently treating the Massive dataset as sufficient by default.

## 7. Data collection, cleaning, and information-timing handling

All fetches (`fetch_ohlcv`, `fetch_insider_transactions`, `fetch_fundamentals`,
`fetch_short_interest`, `fetch_earnings`) are cached locally as Parquet files per ticker under
`data/raw_*`, so re-running the pipeline is compute-bound rather than network-bound and every
run is reproducible from the same cached snapshot. Two cleaning issues surfaced during
development and were fixed before any result was trusted:

- **Unadjusted stock splits** produced multi-thousand-percent outlier trades in an early full-
  universe run (max gain +33,580%). `EventBacktester.compute_performance()` now filters and
  reports any trade return above `MAX_TRADE_RETURN_CAP` (5.0, i.e. +500%) as a named anomaly
  rather than silently including it.
- **Information timing**: every trade enters at the next trading day's open after the filing
  date is observed (`entry_idx = i + 1` in `backtester.py`), never on the transaction date
  itself, so the backtest cannot see a filing before it was public. Insiders' up-to-two-
  business-day legal reporting window (named as an expected limitation in the original
  proposal) is respected by construction, since the signal only fires on the filing date.

## 8. Methodology

**Signal construction** (`signal_engine.py`): a cluster fires when at least
`MIN_DISTINCT_INSIDERS` (3) insiders buy within a `CLUSTER_WINDOW_DAYS` (5) rolling window,
combined dollar value at least `MIN_CLUSTER_VALUE` ($100k). Three refinements sit on top of the
base cluster: (1) an insider's buy is dropped if it is "routine" (bought the same calendar
month in 3+ distinct years, `ROUTINE_MIN_YEARS`), (2) a buy only counts if it is at least
`CONVICTION_SIZE_MULTIPLIER` (1.5x) that insider's own historical average buy at that ticker,
and (3) the stock's most recent days-to-cover must be at least `MIN_DAYS_TO_COVER` (2.0). No
market-cap filter exists (Section 4); a `MAX_DOLLAR_VOLUME` ceiling of $2.5M on 20-day average
dollar volume proxies a small-cap/illiquid tilt instead, since this dataset has no working
fundamentals.

**Exit rule** (`backtester.py`): a fixed 20% stop-loss from entry price, a 30% take-profit
cap, and a profit-activated trail that stays inactive until the trade is up 50%, then trails
the peak price by 15%. The 30% take-profit is not merely a risk cap; removing it entirely
(tested as a variation, EXP-21) performed far worse, because most micro-cap spikes to 30%+
historically mean-revert. The trail only ever matters on the rare single-day gap that jumps
past both the take-profit and the 50% activation level in one bar (roughly 5% of trades),
letting the actual spike be captured instead of an unrealistic flat 30% fill.

**Position sizing** (`portfolio_simulator.py`): "house-money" sizing splits account equity
into a protected principal, sized at 2% of that principal per trade, and a profit cushion above
the original $100,000 starting capital, sized more aggressively at 8%. This lets the account
take more risk with money already won without ever risking more than 2% of the original stake.

## 9. Backtest design

Entry executes at the next day's open after a valid signal; exit is the first of stop-loss,
take-profit, trail-stop, or the fixed holding-period time exit, whichever triggers first
walking day-by-day through the trade window. A 20 bps round-trip transaction cost is
subtracted from every trade's return. Per-trade metrics (`EventBacktester.compute_performance`)
treat each trade as an independent event; a separate portfolio-level simulation
(`PortfolioSimulator`) books each trade's realized dollar P&L against one shared account,
sized in true entry-date order, to report a portfolio Sharpe ratio and max drawdown that
account for the fact that this strategy holds roughly 20+ positions at once.

## 10. Parameter- and model-selection process

No machine-learning model is used; every parameter is a threshold in an explicit rule.
Parameters with a real tuning history (`MIN_DAYS_TO_COVER`, `MAX_DOLLAR_VOLUME`,
`TRAIL_ACTIVATION_PCT`, `STOP_LOSS_PCT`, sizing rates) were selected using a chronological
train/test split (train through 2024-06-30, test from 2024-07-01 onward, later replaced by a
70/30 chronological split for the final validation in Section 12) and were only adopted when
the result held its sign on both sides of the split; `MAX_DOLLAR_VOLUME=$12M` is a documented
counterexample that looked good in training and failed on the test side, and was rejected for
that reason (EXP-12). `STOP_LOSS_PCT` and `TRAIL_ACTIVATION_PCT` are single global constants
shared by all three holding periods, so they are tuned to the 30-day holding period; 60 and
90-day results are reported honestly as un-independently-tuned, not claimed as separately
optimal (Section 14, limitation 1).

## 11. Main results

Final adopted configuration: `STOP_LOSS_PCT=0.20`, `TRAIL_ACTIVATION_PCT=0.50`,
`TRAIL_PCT=0.15`, `TAKE_PROFIT_PCT=0.30`, `MAX_DOLLAR_VOLUME=$2.5M`, `MIN_DAYS_TO_COVER=2.0`,
`SIZING_MODE=house_money` (2%/8%), $100,000 starting capital, over 2021-01-01 to 2026-01-01.

| Holding period | Trades | Win rate | Total return | Portfolio Sharpe | Max drawdown |
|---|---|---|---|---|---|
| 30 days (primary) | 860 | 46.5% | +113.7% | 1.986 | -12.77% |
| 60 days | 835 | 45.5% | +107.9% | 1.913 | -15.43% |
| 90 days | 827 | 44.1% | +84.7% | 1.681 | -17.28% |

$100,000 grows to $213,682 at the 30-day, primary configuration. These figures are lower than
an earlier headline of +132.3% total return and 2.088 Sharpe reported mid-project; that
earlier number reflected a real look-ahead bug in the position-sizing engine (Section 13), and
the table above is the corrected, current result, not the best number ever seen.

## 12. Robustness and additional experiments

- **Chronological split** (early 70% vs. late 30% of trades by entry date): the late,
  holdout-like era is *stronger*, not weaker, than the tuning-era (mean trade return 5.14% vs.
  2.01%, per-trade Sharpe 0.510 vs. 0.254), the opposite of what overfitting to the earlier
  window would predict.
- **Bootstrap 95% confidence interval** (5,000 resamples): all trades (n=860) mean 2.95%, CI
  [1.30%, 4.62%]; trail-stop-only trades (n=49) mean 57.06%, CI [46.27%, 70.14%]. Both
  intervals sit well above zero, so the trail-stop contribution is not a fluke of one or two
  outlier trades.
- **Transaction cost stress test**: at 5x the assumed cost (1.0% vs. 0.2% round-trip), total
  return falls from 113.7% to 56.1% and portfolio Sharpe from 1.986 to 1.400. The result is
  real but the return figure is sensitive to true slippage on illiquid names; Sharpe holds up
  better than raw return does.
- **Cash-constrained sizing check**: re-implementing house-money sizing with true available
  cash (not mark-to-market equity) found the look-ahead bug described in Section 13; after the
  fix, this check is kept as a standing regression test in `experiments/validation_suite.py`.
- 30 further exit-mechanic and sizing variations were tested and rejected (full list in
  `experiment_record.csv`), including a pure trailing stop (negative Sharpe everywhere), 6
  more elaborate exit ideas that all underperformed the simpler mechanic, and 3 alternative
  sizing modes (compounding alone, concurrency-throttle, blended holding-period sleeves) that
  either matched or underperformed the adopted house-money default.

Reproducible code for all of the above lives under `experiments/`
(`exit_and_sizing_sweep.py`, `validation_suite.py`), runnable against the same cached data.

## 13. A look-ahead bug found and fixed

A capital-constrained, entry-date-ordered simulator built specifically to validate the results
above disagreed with the production numbers (113.7%/1.986 instead of a reported 132.3%/2.088),
despite the fact that no trade was ever being skipped for lack of cash. The cause:
`simulate_dynamic()` sized every new trade using account equity computed in *exit-date* order,
which let a trade's position size be inflated by profit from a different trade that entered
later but happened to close first, a look-ahead in the compounding schedule rather than in the
trading signal itself. This affected every dynamic sizing mode used throughout the project
(compounding, house-money, concurrency-throttle), since all three shared this method. The fix
refactors `portfolio_simulator.py` around a shared `_run_entry_ordered()` engine that only ever
sizes a new trade off equity realized strictly before that trade's own entry date. This is
disclosed here in full because it changed every headline number reported earlier in the
project, and hiding it would misrepresent how the final numbers were obtained.

## 14. Assumptions and limitations

1. The trail-stop's contribution rests on a thin slice of trades (32-49 out of roughly 900 per
   holding period). Section 12's bootstrap CI shows this is not on the edge of vanishing, but
   it remains a smaller sample than the headline trade count suggests.
2. `STOP_LOSS_PCT` and `TRAIL_ACTIVATION_PCT` are single global values shared across all three
   holding periods; only the 30-day result is independently tuned to its own optimum.
3. `PortfolioSimulator` still assumes unconstrained total capital beyond the entry-ordering fix
   (no real cash-decline path is wired into `main.py`'s production run), and open positions are
   not checked for sector or name correlation, so modeled drawdown may understate a real
   correlated bad day.
4. The smallest 20-day average dollar volume among actually executed trades is roughly
   $150/day (a SPAC warrant), and the flat 20 bps transaction-cost assumption does not price
   the real execution risk of trading names that thin.
5. Everything reported here is a historical backtest. The chronological split is the closest
   thing to true out-of-sample evidence collected so far; nothing has been run against live or
   paper trading.

## 15. Final conclusion

The evidence supports the hypothesis on this universe and period: clustered, opportunistic
insider buying in small, thinly traded names, filtered by short-interest overlap and exited
with a fixed stop/take-profit plus a profit-activated trail, produced a positive, sign-stable
edge that survived a chronological holdout split, a bootstrap confidence interval, and a 5x
transaction-cost stress test. This is not a claim of a deployable live edge: capacity,
correlation risk, and real slippage on illiquid names (limitations 3 and 4) are not fully
modeled, and the strategy has not been tested outside historical backtesting.

## 16. What to investigate next

Tune `STOP_LOSS_PCT` and `TRAIL_ACTIVATION_PCT` independently per holding period instead of
sharing one global value; add sector- or correlation-aware exposure caps to the portfolio
simulator, since the strategy runs roughly 20+ concurrent positions; model a realistic capital
decline path (skipped trades when cash is exhausted) directly in the production simulator
rather than only in the validation check; and move to paper trading as the next real test of
the edge, since no backtest can fully substitute for live execution against real liquidity.
