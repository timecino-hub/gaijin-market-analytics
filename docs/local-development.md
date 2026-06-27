# Local Development

## Prerequisites

- Python 3.12
- uv
- Node.js 22 LTS
- pnpm
- Docker with Docker Compose

## Environment

Create a local environment file from the root example:

```sh
cp .env.example .env
```

The example values are local-only defaults. Do not commit real credentials,
cookies, tokens, private datasets, or user secrets.

## Install Dependencies

```sh
make install
```

This installs the API dependencies with uv and the web dependencies with pnpm.

## Run Services

Start PostgreSQL:

```sh
make db-up
```

Apply database migrations:

```sh
make db-migrate
```

Start the API:

```sh
make api-dev
```

Start the web app:

```sh
make web-dev
```

Default local URLs:

- Web: http://localhost:3000
- API health: http://localhost:8000/health
- API readiness: http://localhost:8000/ready
- API OpenAPI JSON: http://localhost:8000/openapi.json
- PostgreSQL: localhost:5432

## Database

Local API runs should use a `DATABASE_URL` with `localhost`, for example:

```sh
postgresql+psycopg://gaijin_market:gaijin_market_dev@localhost:5432/gaijin_market_analytics
```

Migration commands:

```sh
make db-migrate
make db-current
make db-downgrade
```

API tests use `TEST_DATABASE_URL` or derive an isolated database whose name ends
with `_test`. The test fixture creates and drops only that isolated test
database; it does not remove the normal development database or Docker volume.

## CSV Import

The local API exposes:

- `POST /api/v1/imports/csv`
- `GET /api/v1/imports/{job_id}`

`POST /api/v1/imports/csv` expects a multipart file field named `file`. Only
`.csv` filenames are accepted. MIME type is checked as an auxiliary signal, but
the server still validates the extension, bounded file size, UTF-8 decoding,
CSV header, and every data row. The default maximum upload size is 10 MB.

CSV v1 required fields:

```text
item_key,item_name,category,observed_at,best_ask,best_bid,ask_count,bid_count,estimated_volume
```

Rules:

- `item_key`, `item_name`, and `category` must be non-empty strings.
- `observed_at` must be ISO-8601 with a timezone and is stored in UTC.
- `best_ask` is a required Decimal greater than 0.
- `best_bid` and `estimated_volume` are optional Decimals greater than or equal
  to 0 when present.
- `ask_count` and `bid_count` are optional integers greater than or equal to 0.
- Empty strings become `NULL` only for optional fields; required fields produce
  row errors.

The importer calculates SHA-256 from the original uploaded bytes. Re-uploading
the same file creates a `duplicate` import job and does not write snapshots
again. `items.external_key` is reused for `item_key`; existing item
name/category values are not overwritten and generate warnings. Snapshot writes
are idempotent through the existing `(item_id, observed_at)` uniqueness
constraint.

Error reports are stored on `import_jobs.error_report` and returned by the job
detail endpoint. Each row error or warning includes:

```json
{
  "row_number": 2,
  "field": "best_ask",
  "error_code": "must_be_positive",
  "message": "best_ask must be greater than 0."
}
```

## Verify

```sh
make test-api
make web-lint
make web-build
make compose-config
```

## Compliance Boundary

Local development must use CSV, JSON, manual, fixture, or explicitly authorized
data only. This scaffold does not include marketplace scraping, login
automation, internal endpoint calls, or automated buy, sell, cancel, account, or
payment actions.
