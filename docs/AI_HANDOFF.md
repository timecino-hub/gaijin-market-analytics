# AI Project Handoff

## Current implementation

This repository is a compliant Gaijin Market analytics application. It accepts
manual, imported, or otherwise authorized data; it does not scrape the
marketplace, automate browser activity, reuse cookies, trade, or promise profit.

The API is FastAPI, the web app is Next.js/TypeScript, and analytics are pure
Python. The local screen-recognition flow accepts a user-selected screenshot,
produces a review candidate, and requires a human decision before any optional
database import.

## Verified feature state

- Production `windows-ocr` remains the price-cells backend.
- `candidate-order-quantities-v2` is Review-only. It is not the production
  default and quantity output must not bypass human review.
- The exact-25 Windows OCR baseline is bid/ask/both exact `20/23/19`, with
  wrong, false-confident, and quantity-review false-negative values all zero.
- Round 1 makes every local-recognition route loopback-only and rejects
  disallowed browser Origins before image decoding or database work.
- Round 2 keeps confirmation and persistence separate. Only an explicit import
  of a confirmed review can create one `market_snapshots` row and one
  `screen_review_imports` audit row for an existing item.

## Validation baseline

The validated automated baseline is API `315 passed`, analytics `89 passed`,
and web `72 passed` with lint, typecheck, and build successful. Round 2
migration/import tests validate idempotency, no overwrite, existing-item-only
behavior, and audit-only screenshot quantities.

The real browser confirm-to-import smoke flow remains a follow-up manual
acceptance item. Earlier local process diagnostics did not establish a browser
smoke result; do not represent it as passed.

## Next developer checklist

1. Read `AGENTS.md`, `CURRENT_STATUS.md`, `DECISIONS.md`, and `NEXT_ACTIONS.md`.
2. Preserve the working tree before editing and keep private fixtures, logs,
   screenshots, environment files, and temporary diagnostics out of Git.
3. Keep candidate OCR backends Review-only unless a separately reviewed change
   explicitly promotes them.
4. Use an isolated PostgreSQL database for migration and import checks.
5. Keep confirmation separate from explicit reviewed-snapshot import.
