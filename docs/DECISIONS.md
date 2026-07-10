# Decisions

## Candidate OCR remains non-production

`windows-ocr` remains the production price-cells backend. Item-title and order
quantity variants are explicit candidates and require reviewer attention. OCR
experiments must not silently change the production alias.

## Missing is safer than wrong

Quantity values are Review-only. Wrong values, false-confidence, and review
false-negatives are promotion blockers; deterministic, evidence-backed parsing
is preferred over aggressive character-to-digit guessing.

## Local recognition is a loopback boundary

The complete local-recognition namespace is loopback-only. Browser requests
with an Origin header must match configured local origins before upload or
database activity. This is a local, user-triggered workflow, not remote OCR.

## Confirmation and import are different actions

Confirmation preserves a reviewed candidate without changing market history.
Only an explicit import may persist a snapshot, providing a deliberate review
boundary and avoiding accidental writes.

## Existing item and audit-only quantities

Reviewed imports require an existing matching item; unknown items and inferred
categories are not created. Displayed screenshot quantities are audit data, not
CSV order-count data, so they are kept in `screen_review_imports` and not mapped
to `market_snapshots.bid_count` or `market_snapshots.ask_count`.

## Idempotency and preservation of history

A review id maps to one audit row and one snapshot. Repeated imports return the
same result, while an existing `(item_id, observed_at)` snapshot is rejected
rather than overwritten.
