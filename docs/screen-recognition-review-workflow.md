# Screen Recognition Review Workflow

This workflow is a local-only alpha path for manually reviewed current market
screenshots. It does not access Gaijin Market, automate a browser, read cookies,
generate CSV files, or perform trading actions. A confirmed candidate can be
explicitly imported into the local database as a market snapshot for an
existing item; confirmation alone never writes the database.

## Scope

The first version handles one manually uploaded current screenshot at a time:

```text
upload PNG/JPEG
-> create in-memory review
-> run local Windows OCR in the background
-> show OCR values and evidence
-> manually confirm item identity, prices, quantities, and observed_at
-> generate reviewed candidate JSON
-> optionally click an explicit import action
-> create one audited market snapshot for an existing item
```

Deferred scope includes `_1` history screenshots, historical charts, tooltip
recognition, browser extension source code, automatic screenshots, community
submission, CSV generation, automatic item creation, and any account or trading
action. The in-repository Local Extension Bridge is documented separately in
`docs/local-extension-bridge.md`; it accepts authenticated future extension
uploads but does not implement the extension itself.

## Storage Boundary

Reviews are stored only in the API process memory. The store is intended for a
single-process local alpha environment.

- `max_reviews = 100`
- `ttl_seconds = 7200`
- `max_image_bytes = 10 MB`
- decoded image pixel limit: `40,000,000`
- PNG/JPEG only
- records are lost when the API process restarts
- terminal records also remain temporary
- successful imports persist independently in `market_snapshots` and
  `screen_review_imports`
- source screenshots are deleted in the OCR task `finally` path
- no screenshots, debug crops, overlays, or review data are written to the
  repository or a persistent data directory

Clearing all reviews requires:

```text
DELETE /api/v1/local-recognition/reviews?confirm=true
```

Without `confirm=true`, the API returns `clear_confirmation_required`.

## API

The review API lives under:

```text
/api/v1/local-recognition
```

Every route in this namespace rejects non-loopback clients. Manual browser
uploads also reject an `Origin` header that is not in `CORS_ALLOWED_ORIGINS`;
requests from local command-line tools may omit `Origin`. This prevents a
remote client or an unrelated website from using the local OCR service as a
resource-exhaustion target.

Endpoints:

```text
GET    /capabilities
POST   /reviews
GET    /reviews
DELETE /reviews?confirm=true
GET    /reviews/{review_id}
PATCH  /reviews/{review_id}
POST   /reviews/{review_id}/confirm
POST   /reviews/{review_id}/import
POST   /reviews/{review_id}/reject
POST   /reviews/{review_id}/unreadable
DELETE /reviews/{review_id}
POST   /pairing-codes
POST   /pair
GET    /extension-status
DELETE /pairings/{pairing_id}
POST   /extension-reviews
```

`POST /reviews` returns HTTP 202 with `status=processing`. The OCR task runs in
the background and updates the review to `pending_review`, `unreadable`, or
`failed`. OCR exceptions are captured as stable review errors and are not
exposed as stack traces.

`POST /extension-reviews` requires `Authorization: Bearer <token>` from a
successful local pairing. It reuses the same image validation, OCR background
task, Review Store, state machine, and confirm/reject/unreadable/candidate
contracts as manual upload.

## State Machine

Allowed transitions:

```text
processing -> pending_review
pending_review -> confirmed / confirmed_with_edits / rejected / unreadable
processing -> unreadable / failed
processing / pending_review -> expired
```

Only `pending_review` can be patched, confirmed, rejected, or marked unreadable.
Only `confirmed` and `confirmed_with_edits` reviews can be imported. Terminal
and expired reviews cannot be edited.

## Image Validation

The API does not trust browser MIME type. It validates:

- safe basename only
- extension is `.png`, `.jpg`, or `.jpeg`
- PNG/JPEG magic bytes
- extension and signature match
- compressed upload size is at most 10 MB
- Pillow can decode the image
- decoded width and height are valid
- decoded pixel count is at most 40 MP
- Pillow decompression-bomb warnings/errors are rejected

SVG, WebP, GIF, BMP, PDF, paths, and URLs are rejected.

## Item Identity

Confirmation requires exactly one identity mode.

Existing item mode submits `selected_item_id`. The backend rereads the item from
the database and uses the server-side `id`, `external_key`, and `name`. The
database is read only; no item is modified or created.

Manual mode requires administrator-provided `item_key` and `final_item_name`.
OCR names are only reference evidence and never create an item key.

## observed_at

Review creation sets:

```text
created_at
suggested_observed_at = created_at
```

The web form lets the reviewer edit `observed_at`. The API requires a
timezone-aware ISO-8601 value, normalizes it to UTC, and rejects values more than
five minutes in the future. It does not read EXIF timestamps or file modified
times.

Candidates record:

```text
observed_at
observed_at_source
```

`observed_at_source` is `review_created_default` when unchanged and
`user_edited` when the reviewer edits it.

## Candidate JSON

Confirmation returns a candidate object with:

- `candidate_version = screen_review_candidate_v1`
- Decimal prices serialized as strings
- `null` preserved as `null`
- import-state flags initially set to `false`
- database IDs and `imported_at` initially set to `null`
- `quantity_semantics = screenshot_display_quantity`
- `csv_quantity_mapping = not_mapped_to_ask_count_or_bid_count`

The candidate is not imported automatically. The web page can copy or download
the single candidate JSON, then explicitly call the import endpoint. A
successful import updates the in-memory candidate flags and IDs.

## Confirmed Review Import

`POST /reviews/{review_id}/import` is an explicit, idempotent database action.
It accepts only confirmed reviews and only an existing item whose id, key, and
name still match the confirmed candidate.

A successful import creates:

- one `market_snapshots` row containing `best_bid`, `best_ask`, and
  `observed_at`;
- one `screen_review_imports` audit row containing the immutable candidate
  payload hash, review status, displayed quantities, reviewer note, and safe
  source metadata.

The screenshot display quantities are not equivalent to the CSV fields
`ask_count` and `bid_count`. Therefore the created market snapshot leaves
`ask_count`, `bid_count`, `estimated_volume`, and `source_import_job_id` as
`NULL`; the displayed totals exist only in the audit row.

The import is idempotent by `review_id`. Repeating the same import returns the
existing audit and snapshot IDs. A different candidate under the same review id
is rejected, and an existing snapshot with the same `(item_id, observed_at)` is
never overwritten.

Candidate JSON remains minimal. It does not include pairing IDs, source URLs,
tab titles, extension client names, or other browsing metadata.

## Web Page

The page is available at:

```text
/screen-recognition
```

It provides:

- file selection and drag/drop upload
- screenshot preview with object URL cleanup
- pending and terminal in-memory review lists
- OCR/manual value comparison
- existing item search through `GET /api/v1/items?search=...`
- manual item key entry
- editable prices, quantities, and observed_at
- warnings/errors and collapsible OCR evidence
- `识别置信度：不可用`
- copy/download candidate JSON after confirmation
- explicit database import for a confirmed candidate linked to an existing item
- imported snapshot and audit IDs displayed after success
- local extension pairing controls that copy a versioned payload:

```json
{
  "version": "local_extension_pairing_v1",
  "pairing_code_id": "<id>",
  "pairing_code": "<one-time-code>"
}
```

The future browser extension parses `pairing_code_id` and `pairing_code` from
that payload, then submits them to `POST /api/v1/local-recognition/pair`.
- capabilities, layout, OCR, privacy, and diagnostic panels
- clear privacy text explaining that import is explicit and screenshot
  quantities remain audit-only

Polling uses HTTP polling, not WebSocket. The page avoids overlapping requests,
backs off when hidden or failing, and does not overwrite dirty form fields with
poll results.
