# Analytics Design

`packages/analytics` is a standalone Python package for deterministic analytics
on imported, manual, synthetic fixture, or explicitly authorized market data. It
does not access Gaijin Market, databases, FastAPI, SQLAlchemy models, HTTP
clients, files, or environment variables.

## Package Boundary

The API is responsible for persistence, authorization, imports, and converting
database rows into plain analytics contracts. The analytics package only accepts
explicit parameters and returns typed result objects. It must not accept
SQLAlchemy `MarketSnapshot` or `Item` objects directly.

Current structure:

```text
packages/analytics/
+-- pyproject.toml
+-- uv.lock
+-- src/gaijin_market_analytics/
|   +-- contracts.py
|   +-- enums.py
|   +-- exceptions.py
|   +-- fees.py
|   +-- market_rules.py
|   +-- horizons.py
|   +-- statistics.py
|   +-- registry.py
|   +-- strategies/
|       +-- base.py
|       +-- rule_based_v1.py
+-- tests/
```

## Input Contract

`MarketObservation` contains `observed_at`, `best_ask`, `best_bid`,
`ask_count`, `bid_count`, `estimated_volume`, and optional `observation_key`.
`AnalysisRequest` contains item ID, horizon, explicit `as_of`, immutable
observations, marketplace fee policy, fixed market rules, maximum snapshot age,
and minimum snapshot count.

Contract validation rejects non-positive item IDs, naive datetimes,
observations later than `as_of`, invalid fee policy objects, non-positive
snapshot age/count settings, and non-Decimal money, ratio, or volume inputs.

All datetimes are normalized to UTC. Observations are copied into a tuple and
sorted by `observed_at`, optional `observation_key`, and normalized field values.
This makes equal-timestamp ordering deterministic without relying on caller
input order or ORM IDs.

## Output Contract

`AnalysisResult` is frozen and includes strategy metadata, fee policy metadata,
market rules metadata, status, reason codes, observation timestamps and counts,
current ask/bid, reference sell price, sale proceeds, fee amount, profit, ROI,
break-even price, break-even reachability, maximum listing price, maximum seller
settlement proceeds, maximum net profit under the current ask, spread, median
prices, robust volatility, and scores. Missing values use `None`, not zero.
Decimal fields remain `Decimal`; the analytics package does not perform JSON
float conversion.

`AnalysisStatus.INVALID_INPUT` is reserved for future cases where a strategy can
recover from invalid non-contract inputs and return a result instead of raising.
Current contract-level invalid inputs raise stable domain exceptions.

## Decimal And UTC Rules

All monetary values, ratios, fees, profit values, ROI values, and scores use
`Decimal`. Floats are rejected at the contract boundary. Calculations do not
quantize or round intermediate values unless a future business rule explicitly
requires it.

All input datetimes must be timezone-aware. The contract normalizes them to UTC,
and no calculation reads current system time. The caller must pass `as_of`.

## Fee Policy And Math

The current fixed fee policy is `GAIJIN_MARKET_FEE_POLICY_V1`:

- name: `gaijin_market`
- version: `1.0.0`
- nominal fee rate: `Decimal("0.15")`
- currency quantum: `Decimal("0.01")`
- proceeds rounding: `seller_proceeds_round_down`

The policy is an immutable `FeePolicy` value defined inside
`packages/analytics`; it is not read from environment variables, HTTP requests,
headers, cookies, or user input.

Fee math:

- raw sale proceeds: `sell_price * (1 - Decimal("0.15"))`
- sale proceeds: raw sale proceeds rounded down to `0.01` GJN
- fee amount: `sell_price - sale_proceeds`
- gross profit: `sell_price - buy_price`
- net profit: `sale_proceeds - buy_price`
- net ROI: `net_profit / buy_price`

Example:

```text
sell_price = 1.99
raw_sale_proceeds = 1.6915
sale_proceeds = 1.69
fee_amount = 0.30
```

Break-even sell price is discrete: the smallest valid 0.01 GJN listed price
whose rounded seller proceeds are greater than or equal to `buy_price`. For
example, `buy_price = 1.69` breaks even at `1.99`, while `1.98` settles to
`1.68` and does not cover the buy price. The fixed market rules now bound this
calculation by the `2000.00` GJN maximum listing price: `buy_price = 1700.00`
breaks even at `2000.00`, while `buy_price = 1700.01` has no reachable
break-even price under the cap.

`buy_price` and `sell_price` must be finite Decimals greater than zero.

## Market Rules

The fixed market rules are `GAIJIN_MARKET_RULES_V1`:

- name: `gaijin_market`
- version: `1.0.0`
- maximum listing price: `Decimal("2000.00")`
- currency quantum: derived from `fee_policy.currency_quantum`
- maximum seller settlement proceeds: derived by applying the fee policy to
  `Decimal("2000.00")`, currently `Decimal("1700.00")`

`MarketRules` is immutable. It validates that `maximum_listing_price` is a
finite `Decimal`, greater than zero, and aligned to the fee policy currency
quantum. The API and frontend cannot choose a different market cap or market
rules version.

## Horizon Windows

Supported horizons are 7, 30, 90, and 180 days. A horizon window is inclusive:

```text
[as_of - horizon, as_of]
```

Each horizon is selected independently. A 7-day result is not derived from a
30-day, 90-day, or 180-day result.

Coverage ratio is:

```text
(last_observed_at - first_observed_at) / horizon_duration
```

The value is a Decimal clamped to `0..1`.

## Statistics

Statistics ignore `None`. Non-positive prices are invalid for price statistics
and are filtered out. Empty collections return `None`.

Implemented functions include Decimal median, Decimal mean, median absolute
deviation, spread absolute, spread ratio, valid price filtering, latest valid
ask, and latest valid bid.

## RuleBasedV1

`RuleBasedV1` is a transparent baseline, not a promise of profit and not a
future price predictor.

Metadata:

- `strategy_name = "rule_based"`
- `strategy_version = "1.0.0"`
- `feature_version = "market_features_v1"`

Reference sell price:

```text
median(valid best_bid values inside the selected horizon)
```

The raw statistical reference is the median of valid bid values inside the
selected horizon. RuleBasedV1 first filters `None`, non-positive prices, and
bids above the `2000.00` GJN market cap. It then calculates the valid bid
median, rounds the result down to the `0.01` GJN market quantum, and validates
the final `reference_sell_price` as a legal market price. It does not calculate
the median with capped-out bids and then clamp the result to `2000.00`. The
current ask is used as the hypothetical buy price for fee math. The current ask
is not used as the reference sell price.

Default `RuleBasedV1Config` thresholds:

- minimum coverage ratio: `0.50`
- low liquidity count threshold: `5`
- large spread ratio threshold: `0.15`
- full liquidity score count: `20`
- full spread penalty ratio: `0.25`
- full volatility penalty ratio: `0.30`
- confidence weights: coverage `35`, liquidity `35`, inverse risk `30`

Scores are Decimals clamped to `0..100`:

- Liquidity score: median `(ask_count + bid_count)` divided by full-score count.
- Risk score: spread penalty plus volatility penalty, each capped at 50.
- Confidence score: weighted coverage, liquidity, and inverse risk components.

Scores are not probabilities and must not be described as profit probability.

## Data Insufficiency

Normal data insufficiency is returned as `AnalysisResult` with status and reason
codes. Examples include empty observations, insufficient window snapshots,
insufficient time coverage, no valid ask, no valid bid, stale latest snapshot,
low liquidity, large spread, and invalid non-positive prices in market data.

Contract violations such as naive datetimes, future observations, invalid fee
rates, and invalid item IDs raise stable analytics exceptions.

## Registry

`StrategyRegistry` is explicit and test-isolated. It does not use import side
effects or dynamic execution of user-provided modules.

- `register(strategy)` stores a strategy by `(name, version)`.
- duplicate `(name, version)` registration raises `DuplicateStrategyError`.
- `get(name, version)` returns the exact strategy or raises `StrategyNotFoundError`.
- `list_strategies()` returns strategies in stable name/version order.

New algorithms should add a new strategy class and register it explicitly. API
routes should not need to change to swap strategies once an adapter is added.

## API Integration

The FastAPI service depends on `packages/analytics` through a local uv path
dependency. The API does not copy analytics source files and does not inject
runtime paths with `sys.path`.

The read-only endpoint is:

```text
GET /api/v1/items/{item_id}/analysis
```

Query parameters:

- `horizon`: required, one of `7`, `30`, `90`, or `180`, mapped to
  `AnalysisHorizon`.
- `as_of`: optional timezone-aware ISO-8601 datetime. When omitted, the API
  uses current UTC from an injectable clock helper.

The API rejects any `fee_rate` query parameter with
`fee_rate_not_configurable`. The service layer always constructs
`AnalysisRequest(fee_policy=GAIJIN_MARKET_FEE_POLICY_V1)`.

The API checks that the item exists, queries only snapshots in the inclusive
database window `[as_of - horizon, as_of]`, and orders rows by
`observed_at asc, id asc`. The API adapter converts each ORM `MarketSnapshot`
into a plain `MarketObservation` and converts the database snapshot ID into a
string `observation_key`. Analytics code never receives an ORM object,
`AsyncSession`, SQLAlchemy state, or database primary-key type.

The application assembly layer explicitly registers `RuleBasedV1` as
`rule_based` `1.0.0` in `StrategyRegistry`. Clients cannot request arbitrary
Python modules or dynamic strategy names.

Analysis responses include item metadata, effective inputs, fee policy metadata,
strategy metadata, status, reason codes, and all `AnalysisResult` Decimal
fields serialized as strings or `null`. `reference_sell_price` must be
described as a baseline reference sell price, not a guaranteed future price.
`sale_proceeds` must be described as seller settlement, `fee_amount` as the
actual fee difference, and `confidence_score` must not be described as a profit
probability.

Normal data insufficiency returns HTTP 200 with analytics `status` and
`reason_codes`. HTTP errors are reserved for missing items, invalid query
parameters, contract-level invalid inputs, unavailable strategies, invalid
analytics configuration, or unexpected service failures.

## Backtesting Foundation

The pure analytics backtesting package lives under
`gaijin_market_analytics.backtesting`. It contains frozen dataclasses and domain
enums only; JSON formatting, stdout behavior, CLI errors, and pretty printing
belong to the API-side CLI adapter.

Backtests distinguish `lookback_horizon_days` from `forward_horizon_days`.
`RuleBasedV1` receives only observations where
`as_of - lookback_horizon <= observed_at <= as_of`. Future evaluation receives
only observations where `as_of < observed_at <= as_of + forward_horizon`.

The engine accepts an already resolved strategy, or an explicit
`StrategyRegistry` plus strategy name/version. It does not use hidden global
registry state. The production CLI resolves `rule_based` `1.0.0` through the
current explicit registry and uses fixed Gaijin Market fee and market rules.

Future evaluation uses legal future `best_bid` values only. It does not use
future asks as substitutes and does not describe maximum future bid as a
guaranteed achievable trade price. `reference_reached` and
`break_even_reached` can be true at time zero when the cutoff current bid already
meets the relevant condition. Break-even reach checks seller proceeds directly
with the fixed fee policy.

By default, a forward window is complete only when the item dataset's maximum
`observed_at` is at least `cutoff + forward_horizon`. Incomplete cases are kept
with a skip reason but excluded from formal summary denominators. Overlapping
forward windows are allowed and mean cases may not be independent.

## Web Presentation

The item detail page presents the immediate API result without persisting it.
The browser keeps Decimal response fields as strings or `null`; display helpers
may trim trailing zeros or move a decimal point for percentages, but they must
not recompute profit, ROI, break-even price, or scores. API results remain the
source of truth.

The analysis form accepts only the current horizons and optional `as_of`. It
displays the fixed Gaijin Market fee policy as read-only information and does
not provide a fee input. Empty `as_of` is omitted. Non-empty browser
`datetime-local` values are validated and converted through the user's local
timezone into timezone-aware ISO-8601.

URL query parameters represent submitted analysis state, not draft form edits.
`horizon` and optional `as_of` are preserved alongside snapshot filters such as
`from`, `to`, `limit`, and `order`. A valid URL can restore the form and run
one analysis request after refresh. Legacy `fee_rate` parameters are ignored by
the frontend and removed on the next URL update.

Status and reason-code labels are centralized in the web layer and based on the
serialized analytics values:

- statuses: `ok`, `insufficient_data`, `invalid_input`, `no_recent_market`,
  `no_valid_price`
- reason codes: `insufficient_snapshots`, `insufficient_time_coverage`,
  `no_current_ask`, `no_current_bid`, `invalid_price`, `invalid_fee_rate`,
  `stale_latest_snapshot`, `low_liquidity`, `large_spread`,
  `analysis_completed`

Unknown status or reason-code values must fall back to safe plain text and must
not break rendering.

## Running Tests

```sh
cd packages/analytics
python -m compileall src
uv --cache-dir ../../.uv-cache run pytest
uv --cache-dir ../../.uv-cache lock --check
```
