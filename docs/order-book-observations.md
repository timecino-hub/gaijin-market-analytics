# Order Book Observations

## Purpose

`order_book_observations` is the normalized, queryable input layer for market
analysis that depends on quantities displayed in a reviewed market screenshot.
It is intentionally separate from both the immutable review audit and the CSV
snapshot quantity fields.

The three records serve different purposes:

- `market_snapshots` is the canonical price/time record. It stores
  `best_bid`, `best_ask`, and `observed_at`.
- `screen_review_imports` is the immutable audit record. It retains the
  candidate hash, reviewed candidate payload, safe source metadata, reviewer
  note, and the originally confirmed displayed quantities.
- `order_book_observations` is the normalized algorithm input. It links one
  reviewed import to one market snapshot and exposes explicitly named
  `observed_bid_quantity` and `observed_ask_quantity` fields.

## Quantity semantics

The current semantic value is:

```text
screenshot_display_quantity
```

This means the quantities are totals displayed by the reviewed screenshot. They
are not assumed to be order counts, traded volume, or guaranteed executable
liquidity. Algorithms must preserve and inspect `quantity_semantics` instead of
treating the values as a generic volume field.

The importer continues to leave these `market_snapshots` columns `NULL`:

```text
bid_count
ask_count
estimated_volume
source_import_job_id
```

No OCR quantity is mapped into a CSV field.

## Creation and idempotency

A confirmed review is still imported only after the explicit import action.
Within the same database transaction, the importer creates:

1. one `market_snapshots` row;
2. one `screen_review_imports` audit row;
3. one `order_book_observations` row.

Both `market_snapshot_id` and `screen_review_import_id` are unique in the
observation table. Repeating an import for the same review returns the existing
records and does not create another observation.

Migration `20260710_0003` backfills observations for all existing
`screen_review_imports` rows so previously imported Round 2 reviews become
available to the analysis layer.

## Read API

The read-only endpoint is:

```text
GET /api/v1/items/{item_id}/order-book-observations
```

Optional query parameters match the snapshot-history endpoint:

- `from`: timezone-aware ISO-8601 lower bound;
- `to`: timezone-aware ISO-8601 upper bound;
- `limit`: 1 to 2000, default 500;
- `order`: `asc` or `desc`, default `asc`.

Each response combines canonical price/time fields from `market_snapshots` with
normalized quantity and provenance fields from `order_book_observations`.
Prices remain decimal strings in JSON.

## Analysis boundary

This table does not claim that a displayed quantity will execute.
`OpportunityScoreV1` uses the values only as a documented liquidity proxy,
alongside freshness, price history, spread, and data confidence. API and UI
explanations must label that use as an estimate rather than actual transaction
volume. See `docs/opportunity-scoring-v1.md`.
