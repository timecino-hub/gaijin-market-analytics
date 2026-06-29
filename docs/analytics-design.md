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
observations, marketplace fee rate, maximum snapshot age, and minimum snapshot
count.

Contract validation rejects non-positive item IDs, naive datetimes,
observations later than `as_of`, fee rates outside `0 <= fee < 1`, non-positive
snapshot age/count settings, and non-Decimal money, ratio, or volume inputs.

All datetimes are normalized to UTC. Observations are copied into a tuple and
sorted by `observed_at`, optional `observation_key`, and normalized field values.
This makes equal-timestamp ordering deterministic without relying on caller
input order or ORM IDs.

## Output Contract

`AnalysisResult` is frozen and includes strategy metadata, status, reason codes,
observation timestamps and counts, current ask/bid, reference sell price, profit,
ROI, break-even price, spread, median prices, robust volatility, and scores.
Missing values use `None`, not zero. Decimal fields remain `Decimal`; the
analytics package does not perform JSON float conversion.

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

## Fee Math

The fee rate is supplied by the caller and must satisfy `0 <= fee < 1`.

- Sale proceeds: `sell_price * (1 - fee_rate)`
- Gross profit: `sell_price - buy_price`
- Net profit: `sell_price * (1 - fee_rate) - buy_price`
- Net ROI: `net_profit / buy_price`
- Break-even sell price: `buy_price / (1 - fee_rate)`

`buy_price` and `sell_price` must be finite Decimals greater than zero.

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

The current ask is used as the hypothetical buy price for fee math. The current
ask is not used as the reference sell price.

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
- `fee_rate`: required Decimal string satisfying `0 <= fee_rate < 1`.
- `as_of`: optional timezone-aware ISO-8601 datetime. When omitted, the API
  uses current UTC from an injectable clock helper.

The API checks that the item exists, queries only snapshots in the inclusive
database window `[as_of - horizon, as_of]`, and orders rows by
`observed_at asc, id asc`. The API adapter converts each ORM `MarketSnapshot`
into a plain `MarketObservation` and converts the database snapshot ID into a
string `observation_key`. Analytics code never receives an ORM object,
`AsyncSession`, SQLAlchemy state, or database primary-key type.

The application assembly layer explicitly registers `RuleBasedV1` as
`rule_based` `1.0.0` in `StrategyRegistry`. Clients cannot request arbitrary
Python modules or dynamic strategy names.

Analysis responses include item metadata, effective inputs, strategy metadata,
status, reason codes, and all `AnalysisResult` Decimal fields serialized as
strings or `null`. `reference_sell_price` must be described as a baseline
reference sell price, not a guaranteed future price. `confidence_score` must
not be described as a profit probability.

Normal data insufficiency returns HTTP 200 with analytics `status` and
`reason_codes`. HTTP errors are reserved for missing items, invalid query
parameters, contract-level invalid inputs, unavailable strategies, invalid
analytics configuration, or unexpected service failures.

## Running Tests

```sh
cd packages/analytics
python -m compileall src
uv --cache-dir ../../.uv-cache run pytest
uv --cache-dir ../../.uv-cache lock --check
```
