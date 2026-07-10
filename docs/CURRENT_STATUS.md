# Current Status

## Completed and verified

- Order quantity OCR v2 is implemented as the Review-only
  `candidate-order-quantities-v2` backend.
- Round 1 local-recognition boundary hardening is implemented and validated.
- Round 2 reviewed-snapshot import is implemented and covered by automated
  migration and database integration tests.
- The production `windows-ocr` alias remains unchanged.

## Automated baseline

- API: `315 passed`.
- Analytics: `89 passed`.
- Web: `72 passed`; lint, typecheck, and production build succeeded.
- Exact-25 Windows OCR: processed `25/25`; bid/ask/both exact `20/23/19`;
  wrong `0/0`; false-confident `0/0`; quantity-review false-negative `0`;
  `20` attempts per fixture.

## Import behavior

Confirmation only creates a reviewed in-memory candidate. An explicit import
requires a confirmed review and an existing matching item. It is idempotent by
review id and refuses to overwrite an existing item/time snapshot. Screenshot
bid/ask totals are stored in the audit record only; CSV-semantic `bid_count`
and `ask_count` remain null on the created market snapshot.

## Remaining acceptance item

The real browser UI smoke flow—upload, confirm, explicit import, repeat import,
and item snapshot visibility—remains a manual acceptance task. Process-identity
diagnostics did not demonstrate a product failure, but they also do not prove
the browser flow passed.
