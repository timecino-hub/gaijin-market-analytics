# Production Data Intake

Production intake is an operator-controlled, offline workflow. The server does
not browse, scrape, authenticate to, or automatically contact Gaijin Market.
Only explicitly reviewed files supplied through SSH/SFTP are accepted.

## Boundaries

- The public website remains read-only.
- No public upload endpoint is exposed by Caddy.
- Inbox directories use mode `0700`; processed files use mode `0600`.
- Import commands default to database-read-only dry runs.
- `--write` is required for persistence.
- Successful writes are audited in `import_jobs` and archived by timestamp and
  SHA-256 prefix.
- Failed files remain in the inbox for operator review and are never silently
  retried.

## 1. Initialize the server inbox

From `/opt/gaijin-market-web-mvp`:

```sh
./deploy/init-data-intake.sh
```

The default root is `/var/lib/gaijin-market-web-mvp/intake` with separate
`catalog`, `order-books`, `processed`, and `rejected` directories.

## 2. Import reviewed item identities

The catalog CSV contract is exact:

```csv
external_key,name,category,rarity,is_active
reviewed-key,Reviewed name,vehicle,rare,true
```

It creates missing item identities only. Existing exact metadata is reused;
any mismatch fails the whole import and existing rows are never updated.

```sh
./deploy/import-item-catalog.sh /var/lib/gaijin-market-web-mvp/intake/catalog/catalog.csv
./deploy/import-item-catalog.sh /var/lib/gaijin-market-web-mvp/intake/catalog/catalog.csv --write
```

## 3. Import confirmed order-book captures

Upload only a confirmed `round6_manual_order_book_capture_v1` JSON document.
The decoded external key must already exist from the catalog step.

```sh
./deploy/import-approved-orderbook.sh /var/lib/gaijin-market-web-mvp/intake/order-books/capture.json
./deploy/import-approved-orderbook.sh /var/lib/gaijin-market-web-mvp/intake/order-books/capture.json --write
```

The importer preserves the raw capture and ordered levels, uses the existing
idempotency locks, and does not create historical snapshots or Analytics data.

## Transfer example

Use SFTP or SCP to the server inbox. Do not place credentials, cookies,
authorization headers, private account data, or unreviewed response files in
the upload.
