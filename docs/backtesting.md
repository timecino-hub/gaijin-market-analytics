# Backtesting

The backtesting foundation runs walk-forward evaluations on already imported or
explicitly authorized snapshots. It does not contact Gaijin Market, automate an
account, place orders, persist results, expose a public HTTP endpoint, or add a
web page.

## Horizons

Backtests use two separate horizons:

- `lookback_horizon_days`: historical snapshots available to `RuleBasedV1` at a
  cutoff.
- `forward_horizon_days`: future snapshot window used only to evaluate the
  cutoff result.

For a cutoff `as_of`, the analysis input window is:

```text
as_of - lookback_horizon <= observed_at <= as_of
```

The future evaluation window is:

```text
as_of < observed_at <= as_of + forward_horizon
```

The cutoff snapshot may be used for analysis. It is never part of the future
window. Supported lookback and forward values are `7`, `30`, `90`, and `180`.

## Walk-Forward Cases

Cutoffs start at `start_at`, advance by `cadence_days`, and stop at `end_at`.
`cadence_days` is explicit and positive. Adjacent forward windows may overlap,
so cases are not guaranteed to be statistically independent.

The engine sorts snapshots once by `observed_at`, `observation_key`, and input
sequence fallback, then uses indexed boundaries for lookback and future slices.
It does not scan the entire history for every cutoff.

## Future Snapshot Evaluation

Future evaluation uses legal future `best_bid` values only:

```text
0 < best_bid <= 2000.00
```

`terminal_bid` is the last legal bid in the future window, not necessarily the
bid on the last snapshot. `maximum_future_bid` is the highest legal bid observed
in the window. These are snapshot-based evaluation proxies, not proof of
executed trades or guaranteed achievable prices.

`reference_reached` is evaluable only when `reference_sell_price` exists. It is
true if the cutoff `current_bid` already reaches the reference price, or if the
first legal future bid reaches it. A cutoff reach has time `0`.

`break_even_reached` is evaluable only when the cutoff entry ask exists. It uses
the fixed fee policy directly:

```text
calculate_sale_proceeds(candidate_bid) >= entry_ask
```

## Forward Completeness

By default, a case is formally evaluable only when the item dataset covers the
entire future window:

```text
dataset_max_observed_at >= cutoff + forward_horizon
```

If the dataset does not cover the endpoint, the case is retained with
`future_window_incomplete` and excluded from formal summary denominators. If the
dataset covers the endpoint but no snapshots exist in the future interval, the
case uses `no_future_observations`, not `future_window_incomplete`.

## Summary Denominators

Rates return `null` when their denominator is zero.

- `reference_evaluable_count`: complete-window cases with
  `reference_sell_price`.
- `break_even_evaluable_count`: complete-window cases with a legal entry ask.
- `terminal_return_evaluable_count`: complete-window cases with legal entry ask
  and `terminal_bid`.

`positive_terminal_return` means `terminal_net_profit > 0`; zero profit is not a
positive return.

## CLI

The backtest CLI is intended for administrator or developer use:

```sh
cd apps/api
uv run python -m api.backtesting_cli \
  --item-id 123 \
  --lookback-horizon 30 \
  --forward-horizon 30 \
  --start 2026-01-01T00:00:00Z \
  --end 2026-06-01T00:00:00Z \
  --cadence-days 7 \
  --pretty
```

The CLI reads one item from PostgreSQL in the bounded range
`start_at - lookback_horizon` through `end_at + forward_horizon`, ordered by
`observed_at asc, id asc`. It writes stable JSON to stdout and errors to stderr.
Decimal values are JSON strings, datetimes are UTC ISO-8601 strings, booleans
remain booleans, and missing values are `null`.
