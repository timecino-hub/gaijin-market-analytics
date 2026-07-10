# Opportunity Scoring V1

## Purpose

`OpportunityScoreV1` is a deterministic and explainable baseline for deciding
whether one item is eligible for an opportunity ranking and for comparing the
strength of its current evidence. It is not a price predictor, execution model,
or profit guarantee.

The scorer consumes the existing `RuleBasedV1` result and the exact immutable
`MarketObservation` tuple used to produce that result. It does not query a
database, read the clock, call Gaijin Market, or use observations later than the
explicit `as_of` value.

Metadata:

```text
strategy_name = opportunity_score
strategy_version = 1.0.0
feature_version = opportunity_features_v1
```

## Eligibility

A score and eligibility are separate concepts. Every valid analysis can receive
component scores, but an item is eligible for ranking only when all of the
following are true:

- the underlying analysis status is `ok`;
- no invalid-price or above-market-cap reason is present;
- a current ask and reference sell price are available;
- seller proceeds after the fixed Gaijin Market fee produce positive net profit;
- net ROI is positive;
- reviewed screenshot quantities or legacy snapshot counts provide an explicit
  liquidity proxy.

An ineligible result must not be presented as a recommended trade even when some
individual components are high.

## Weighted score

Each component is a Decimal score in the inclusive range `0..100`. The default
weighted score is:

```text
raw_score =
    profitability_score * 0.35
  + liquidity_score * 0.25
  + stability_score * 0.15
  + data_confidence_score * 0.15
  + freshness_score * 0.10

score = clamp(raw_score - risk_penalty, 0, 100)
```

The public result is quantized to two decimal places. Intermediate monetary and
ratio calculations remain Decimal values.

### Profitability: 35%

Profitability uses `RuleBasedV1.net_roi`, which already applies the fixed 15%
fee and seller-proceeds rounding. A 20% positive net ROI receives the full
component score; larger values are capped at 100. Missing, zero, or negative net
ROI receives zero.

The hypothetical entry price is the current valid ask. The reference exit is
the median valid historical bid inside the selected horizon. This is a
mean-reversion reference, not a forecast or guaranteed sale price.

### Liquidity proxy: 25%

The preferred source is reviewed screenshot display quantities from
`order_book_observations`. For each confirmed observation, the scorer adds the
bid-side and ask-side displayed quantities, takes the median total, and grants a
full component score at a median total of 40.

If reviewed quantities are unavailable, the scorer may fall back to the legacy
CSV snapshot `(ask_count + bid_count)` liquidity score. If neither source is
available, the component is zero.

Both sources are proxies. They are not traded volume, execution probability, or
proof that an order can be filled. The response exposes `liquidity_source`,
quantity coverage, and the latest reviewed quantities so the UI can label the
evidence accurately.

### Stability: 15%

Stability is based on robust bid-price dispersion:

```text
volatility_ratio = median_absolute_deviation(valid bids) / median(valid bids)
```

A zero ratio receives 100. A ratio of 30% or more receives zero, with linear
interpolation between those values. Missing statistics receive zero.

### Data confidence: 15%

The confidence component combines:

- 40% snapshot-count sufficiency relative to the configured minimum;
- 30% quantity-source coverage across the observations;
- 30% the existing transparent `RuleBasedV1.confidence_score`.

This component measures evidence completeness. It is not a probability that a
trade will be profitable.

### Freshness: 10%

The latest observation receives 100 when it is at `as_of`. The component falls
linearly to zero at the configured maximum snapshot age and remains zero after
that boundary. The underlying analysis status also rejects a stale latest
snapshot from eligibility.

### Risk penalty

The existing `RuleBasedV1.risk_score` is multiplied by 15%, producing a maximum
15-point subtraction. That risk score is currently driven by spread and robust
bid-price volatility. Missing risk evidence does not invent a penalty and is
reported through an explanation code.

## Explanation and provenance

The result includes stable explanation codes, including whether liquidity came
from reviewed screenshot quantities, legacy snapshot counts, or no source;
whether after-fee profit is positive; whether data is stale or insufficient;
and whether the result is eligible.

The scorer verifies that:

- no supplied observation is later than `analysis.as_of`;
- the supplied observation count and first/last bounds match the analysis
  result;
- maximum age and minimum count settings are valid.

The API service passes the same immutable observation tuple to analysis and
scoring. These defensive checks reject future leakage and common window
mismatches; they are not a cryptographic proof of observation identity.

## API

The read-only endpoint is:

```text
GET /api/v1/items/{item_id}/opportunity?horizon=7|30|90|180&as_of=<ISO-8601>
```

`as_of` is optional and otherwise comes from the injectable UTC clock. The API
uses the same fixed fee policy, market rules, data window, snapshot-age setting,
and minimum snapshot count as the existing analysis endpoint. Decimal values
are serialized as strings.

V1 scores one item at a time. A cross-item ranking endpoint and web leaderboard
belong to a later round after this contract and its database inputs are locally
validated.

## Versioning rule

Changing a weight, full-score threshold, eligibility rule, reference price,
liquidity source preference, or risk formula changes algorithm behavior. Such a
change requires a new strategy or feature version and corresponding regression
and walk-forward backtests; it must not silently replace V1.
