# Market Catalog API

`GET /api/v1/items` is the read-only catalog query used by the public market
browser. In addition to item identity and the legacy snapshot summary, each
item includes the latest approved current order-book summary.

## Current order-book status

- `available`: the latest confirmed manual capture passed the approved price
  contract and `current_order_book` contains canonical GJN display strings.
- `no_capture`: no confirmed manual capture exists for the item;
  `current_order_book` is `null`.
- `contract_error`: the latest capture did not pass the approved contract;
  `current_order_book` is `null`. The service does not fall back to an older
  capture or expose a partially interpreted price.

The summary schema is `web_catalog_order_book_v1` and includes:

- capture timestamp and `fresh`/`stale` status;
- best BUY and SELL `price_raw`, canonical display text, and quantity;
- backend-computed decimal spread display text;
- currency and price-contract identity;
- confirmed source/review status;
- request action fixed to `UNKNOWN`.

The list query uses one count query and one paginated query with windowed
latest-snapshot and latest-order-book subqueries. It does not issue per-item
database queries and performs no writes.

The public gateway permits this GET endpoint but rejects write methods and
non-approved API paths.
