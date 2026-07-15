# Manual Order-Book JSON Validator Contract

## Scope

Round 6D-I1 validates user-confirmed round6_manual_order_book_capture_v1
documents offline. It is a strict parser and validate-only CLI. It is not a
database importer, a database-import dry run, a network client, or a price
conversion layer.

Implementation:

- apps/api/src/api/importers/manual_order_book_json.py
- python -m api.importers.manual_order_book_json <explicit-local-file>

The module uses only the Python standard library. It imports no database model,
router, SQLAlchemy, HTTP client, or socket module.

## Input boundary

The validator reads one user-specified local file. It reads at most 2 MiB plus
one byte and rejects larger input. Bytes must be strict UTF-8. Replacement
characters, malformed JSON, duplicate object keys, excessive nesting, unknown
fields, and missing fields are rejected with stable error codes. Validation
errors never include the full input document.

JSON cannot name another file for the validator to read. The validator performs
no network access.

## Exact document contract

The top-level keys are exactly schema_version, capture_id, captured_at,
timezone_offset_minutes, page, request, order_book, source, and review.

schema_version is exactly round6_manual_order_book_capture_v1. capture_id is a
canonical lowercase UUIDv4. captured_at is canonical UTC with milliseconds and
Z. The timezone offset is an integer from -840 through 840. Every nested object
also has an exact-key contract.

## Page and request identity

The page origin is exactly https://trade.gaijin.net. The path is one literal
segment below /market/1067/, without query or fragment. The exported
external_key must exactly equal that literal path segment.

The parser performs one strict percent-decoding pass. It rejects malformed
escapes, invalid UTF-8, and decoded slash, backslash, query, fragment, NUL, or
control characters. The typed result preserves both the literal and decoded
external key.

The request identity is exactly POST https://market-proxy.gaijin.net/web.

The decoded external key is not mapped to a database item in I1. No item-name
fallback exists.

## Order-book semantics

Both sides contain 1 through 500 levels. A level is exactly a two-element
price_raw and quantity array; both values are positive JavaScript-safe
integers. Reported BUY/SELL depth values are nonnegative JavaScript-safe
integers.

The parser independently recomputes side counts, maximum-price best buy, and
minimum-price best sell, including the matching quantity. It preserves input
level order and duplicate same-price levels. It does not sort, merge, or delete
levels. A level count is not reported depth, and a best-level quantity is not
an aggregate quantity.

price_raw remains an integer. Its display-price scale is unresolved. I1 does
not divide it by 100, 1,000, 10,000, or any other value and does not create a
Decimal display price.

## Source hashes and fingerprint

The capture method is exactly passive_page_response_intercept. Both hashes are
lowercase 64-character SHA-256 hex strings.

The validator independently implements the extension canonical JSON and
recomputes normalized_capture_fingerprint. The fingerprint input excludes the
fingerprint field itself and includes capture identity, time, page/request
identity, normalized book, capture method, raw-response hash, and review state.

The export does not contain original response bytes. Therefore
raw_response_sha256 is a provenance claim whose syntax and inclusion in the
normalized fingerprint are checked; it cannot be independently recomputed or
proven by this file alone.

## Importable document state

The validator accepts only confirmed_by_user, fill_claim false,
requires_manual_review false, and best_buy.price_raw no greater than
best_sell.price_raw.

Pending, fill-claim, manual-review-required, and crossed-book documents fail
validation. Equal best bid and ask are allowed.

## CLI output

Successful output is a narrow JSON summary containing schema/capture/time
identity, literal and decoded external key, side counts, reported depth, best
raw prices, normalized fingerprint, and validation status. It never prints
full levels, the full source document, headers, cookies, authorization, request
payloads, inferred display prices, or a database-import claim.

Invalid input returns exit code 2 and a JSON error containing only
validation_status and a stable error_code.

## Deferred to Round 6D-I2

I1 has no database behavior, migration, item mapping, user/account ownership,
idempotent persistence, or transaction. I2 must separately design and review
those boundaries before any database import is implemented.
