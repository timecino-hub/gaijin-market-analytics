# Opportunity Ranking V1

Round 4 turns the existing single-item `OpportunityScoreV1` result into a
cross-item, read-only leaderboard. It does not introduce a second scoring
formula. Every item is analyzed with the same `as_of`, horizon, fixed market
rules, fee policy, snapshot-age limit, and minimum snapshot count before the
pure analytics ranking function orders the results.

## Endpoint

```text
GET /api/v1/opportunities
```

Required query parameter:

- `horizon`: one of `7`, `30`, `90`, or `180` days.

Optional query parameters:

- `as_of`: timezone-aware ISO-8601 datetime. Omission uses the injected UTC
  clock for one shared ranking timestamp.
- `page`: positive integer, default `1`.
- `page_size`: `1..100`, default `25`.
- `eligible_only`: `true` by default. Set to `false` to include diagnostic
  entries that failed the V1 eligibility gate.
- `min_score`: Decimal in `0..100`, default `0`.
- `search`: case-insensitive item name or external-key match.
- `category`: exact category match.
- `rarity`: exact rarity match.
- `include_inactive`: `false` by default.

`fee_rate` is rejected. The ranking always uses the immutable
`GAIJIN_MARKET_RULES_V1` fee policy and market limits.

## Ranking Contract

The analytics package receives already-scored, unique item results and orders
them using this deterministic key:

1. eligible entries before ineligible diagnostics;
2. total score descending;
3. profitability score descending;
4. liquidity score descending;
5. freshness score descending;
6. data-confidence score descending;
7. item ID ascending.

Ranks are assigned before pagination, so page two continues the global rank
rather than restarting from one. `eligible_only` and `min_score` are applied
before ranks are assigned.

The response includes:

- global rank and item identity;
- total and component scores;
- eligibility and explanation codes;
- current prices, reference exit price, after-fee profit, and ROI;
- liquidity source and reviewed screenshot quantities when available;
- shared effective inputs and immutable strategy/feature versions;
- evaluated, eligible, filtered, and pagination counts.

Scores remain Decimals and are serialized as strings. They are not
probabilities, forecasts, or guarantees.

## Query Shape

The API intentionally avoids one database query per item:

1. one candidate-item query selects items with a snapshot inside the shared
   time window and applies item filters;
2. one snapshot/observation query loads all selected item histories;
3. rows are grouped in memory;
4. the existing `ItemAnalysisService.analyze_loaded_rows` and
   `OpportunityScoreV1` calculate each item;
5. the pure `rank_opportunity_scores` function orders and filters results.

`ANALYTICS_OPPORTUNITY_MAX_CANDIDATES` defaults to `2000`. A request exceeding
that bound receives a stable `opportunity_scope_too_large` error and must be
narrowed with item filters. This bound protects a local deployment from
unbounded memory and CPU work; it is not a market-data limit.

## Web Page

The read-only page is available at:

```text
/opportunities
```

It supports the same reproducible query parameters, renders the component
scores and evidence provenance, and links each result to the existing item
detail page. Server rendering uses `no-store` API requests so the URL completely
captures the selected filters while the API remains the source of truth.

## Safety And Scope

Opportunity Ranking V1:

- reads only imported, manually reviewed, synthetic fixture, or explicitly
  authorized data;
- never accesses Gaijin Market directly;
- never places, cancels, or recommends executable orders;
- never interprets screenshot quantities as proven executable depth or actual
  traded volume;
- never uses observations after `as_of`;
- does not persist scores;
- does not use machine learning.

Changes to score semantics, weights, tie-breakers, or eligibility rules require
an explicit new strategy or feature version. Pagination, UI presentation, or
candidate-filter changes must not silently alter `OpportunityScoreV1`.
