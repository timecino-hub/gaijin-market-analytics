# Manual order-book JSON import

## Scope

Round 6D-I2-B persists a confirmed, strictly validated passive order-book capture as raw provenance and ordered levels. It does not create or update `market_snapshots`, does not write the screenshot-only `order_book_observations` table, does not feed Analytics, and does not infer a display-price scale.

The persistence service accepts the original file bytes and source filename. At its public boundary it computes the file SHA-256, runs `api.importers.manual_order_book_json`, and constructs the stored JSON payload from those same bytes. Callers cannot independently supply a capture object, payload, or checksum. The importer does not weaken the validator contract.

## Command

```powershell
python -m api.manual_order_book_import_cli <confirmed-capture.json>
```

The default is a database-read-only dry run. It validates the file, resolves the existing item, checks whether the capture already exists, and returns a narrow JSON plan.

Writing requires an explicit flag:

```powershell
python -m api.manual_order_book_import_cli <confirmed-capture.json> --write
```

Neither mode performs network access. The command uses the configured local database. `--write` records an audited `ImportJob`; dry run creates no rows.

Invalid documents and invalid audit basenames return exit code `2` and an `invalid_document` error type. Import identity conflicts return exit code `3` and `import_conflict`. Database operational failures return exit code `4` and `database_operational_error`. Error JSON contains only stable codes and never includes the source path, filename, or document body.

Audit basenames are split consistently on both POSIX and Windows separators. They must be strictly UTF-8 encodable, at most 255 characters, and contain no Unicode control, format, or surrogate characters. The importer rejects unsafe names instead of replacing, escaping, or silently deleting characters.

## Item identity

The validator derives both the literal and strict UTF-8-decoded external key from the fixed Gaijin Market page path `/market/1067/<key>`. The persistence service requires one existing `items.external_key` equal to the decoded key.

The importer:

- does not match by item name;
- does not create items;
- does not accept an unrelated target item id;
- does not support another origin or market id;
- relies on the current database invariant that `items.external_key` is globally unique.

Unknown keys fail with `existing_item_required`. Future multi-market support requires a separate item-identity migration because the current item model has no market or game columns.

## Persistence model

`manual_order_book_imports` stores one successful capture:

- its completed `import_jobs` row and existing item;
- capture UUID, schema version, capture method, and `manual_response_json` source type;
- source filename basename and source file SHA-256;
- raw-response SHA-256 provenance claim and recomputed normalized fingerprint;
- captured time, timezone offset, page identity, and request identity;
- confirmed review status;
- reported level counts, depths, and best raw levels;
- the validated JSON payload and database import time.

`manual_order_book_levels` stores every level with `BUY` or `SELL`, a zero-based side-local index, raw integer price, and quantity. Duplicate prices and source order are preserved. Deleting an import cascades to its levels.

Reported BUY and SELL depth values are preserved independently as non-negative JavaScript-safe integers. They are not interpreted as level counts, quantity sums, best-level quantities, or order counts, and may be zero or smaller than the number of stored levels.

## Price and time semantics

`price_raw` remains the positive integer supplied by the approved extension. The fixed semantics label is:

```text
gaijin_market_response_price_raw_unscaled
```

No `price_scale` or converted Decimal price is stored. The `10000` scale independently established for historical trade buckets is not assumed for the current order-book response.

`captured_at` is the observation time of the passive response and is stored as UTC. `imported_at` records database persistence time. The importer does not allow user editing of either value.

## Idempotency and concurrency

The successful capture table has independent uniqueness constraints for:

- capture id;
- normalized capture fingerprint;
- source file SHA-256.

The service also takes transaction-scoped PostgreSQL advisory locks for all three identities in deterministic order. Exact retries and semantically identical reformatted files create a `duplicate` import job and reuse the existing capture and levels. Reuse of one identity with different validated content fails with a stable conflict code; existing data is never overwritten.

The capture, all levels, and completed audit update are one transaction. A level or database failure rolls back the processing job and all capture rows, then records a narrow failed audit in a separate transaction when the database remains available.

## Provenance and authentication boundary

The confirmed extension export does not contain a local pairing id, Gaijin account id, user id, Cookie, Authorization header, or cryptographic signer identity. Manual file import therefore records no pairing or account claim. Local operator access to the configured database is the authority boundary for this CLI.

`raw_response_sha256` remains an extension provenance claim because the export omits the original raw response bytes. The normalized fingerprint is recomputed by the validator and protects the normalized capture fields, but it does not prove signer or account identity.

An HTTP upload or extension-pairing workflow is outside I2-B and requires separate authentication, rate-limit, and ownership review.

## Analytics promotion

Raw captures are intentionally isolated from current market-history export and Analytics contracts, which accept only confirmed screenshot `screen_review` observations with Decimal prices. Promotion requires a later reviewed contract that independently establishes current-order-book price scaling and updates source-type consumers without weakening screenshot provenance checks.
