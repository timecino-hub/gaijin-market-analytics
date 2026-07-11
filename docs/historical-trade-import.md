# Historical trade import v1

## Confirmed source contract

The Gaijin Market history response contains both granularities in one response:

```json
{
  "response": {
    "1h": [[unix_seconds, price_raw, reported_trade_volume]],
    "1d": [[unix_seconds, price_raw, reported_trade_volume]],
    "success": true
  }
}
```

Confirmed semantics:

- `price_gjn = price_raw / 10000`
- `1h` timestamps are UTC hour bucket starts
- `1d` timestamps are UTC day bucket starts
- empty buckets are omitted
- the daily volume equals the sum of overlapping hourly volumes
- the daily price is the hourly price weighted by hourly volume

Therefore the price semantic is:

`bucket_volume_weighted_average_trade_price`

The third field is an additive reported trade-volume measure. Whether one unit means an
item or a matched transaction remains unconfirmed, so its semantic is deliberately:

`reported_trade_volume_unknown_unit`

## User-triggered flow

1. A production content script is loaded only on
   `https://trade.gaijin.net/market/1067/*`.
2. The page-world bridge observes an already-loaded JSON response.
3. It publishes only the sanitized `1h` and `1d` arrays, current item key, safe page URL,
   schema version, and capture time.
4. It never publishes request bodies, headers, tokens, cookies, account state, or orders.
5. The isolated content script keeps the latest sanitized payload in memory.
6. Nothing is sent to localhost until the user clicks **导入历史成交**.
7. The paired extension sends the payload to
   `POST /api/v1/local-recognition/extension-history-imports`.
8. The API requires loopback access and a valid pairing token, verifies the page/item
   identity, validates all points, hashes the canonical series, and writes an immutable
   import audit plus idempotent buckets.

## Database model

`historical_trade_imports` records:

- item and pairing identity
- sanitized source URL
- schema and canonical series SHA-256
- point counts
- inserted, updated, and unchanged counts
- overlapping 1h/1d consistency counts
- import time

`historical_trade_buckets` records:

- item
- `1h` or `1d`
- UTC bucket start and fixed duration
- raw VWAP integer and scale `10000`
- decimal VWAP
- reported trade volume
- explicit price and volume semantics
- source schema
- first/last seen timestamps

The natural key is `(item_id, granularity, bucket_start_utc)`.

## Safety and limitations

- Unknown items are not created automatically.
- Query strings and fragments are removed from the source URL.
- Reimporting the same canonical series is deduplicated. The retry response preserves the
  original import audit identity but reports current-request mutation counters as zero.
- Reimporting newer data updates an existing bucket only when the source value changed.
- The first UTC day of a rolling `1h` series is excluded from 1h/1d overlap checks when
  its first point is after midnight, because that day may be only partially covered.
- A bucket VWAP does not prove that a particular target price traded inside that bucket.
- Historical trade data is not yet used to change OpportunityScoreV1.
- Round 5C should add explicitly named VWAP-based validation metrics rather than treating
  VWAP as exact execution evidence.
