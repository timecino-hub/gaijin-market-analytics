# Production Operations

The production stack runs as the Compose project `gaijin-market-web-mvp`.
Runtime secrets belong only in the server-side `.env` file with mode `0600`.

## Validate and deploy

```sh
docker compose -p gaijin-market-web-mvp config --quiet
docker compose -p gaijin-market-web-mvp up -d --build
docker compose -p gaijin-market-web-mvp ps -a
./deploy/smoke-test.sh
```

The gateway exposes only HTTP/HTTPS. PostgreSQL is published on loopback and
API/Web ports remain inside the Compose network. Caddy certificate and account
state use named volumes so container recreation does not request a new
certificate unnecessarily. Container JSON logs rotate at 10 MB with three
files retained per service.

## Database backup

Run from the repository root on the server:

```sh
./deploy/backup-postgres.sh
```

Backups default to `/var/backups/gaijin-market-web-mvp/postgres`, use PostgreSQL
custom format, include a SHA-256 sidecar, and retain 14 days. Override with
`BACKUP_DIR` and `BACKUP_RETENTION_DAYS` when needed. The script reads the
database through `pg_dump`; it does not modify database rows.

## Restore boundary

Restore is intentionally not automated. Before a restore, stop public traffic,
take a fresh backup, select an exact dump, verify its SHA-256, and obtain an
explicit operator confirmation. Use a disposable database to rehearse the
restore before touching the production database.

## Reviewed production intake

Initialize the restricted server inbox with `./deploy/init-data-intake.sh`.
Use `import-item-catalog.sh` for reviewed item identities, then
`import-approved-orderbook.sh` for confirmed raw order-book captures. Both
commands default to dry-run and require `--write` for persistence. See
`docs/production-data-intake.md` for the complete boundary and workflow.
