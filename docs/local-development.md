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

For the web app, copy the public browser environment example:

```sh
cp apps/web/.env.local.example apps/web/.env.local
```

`NEXT_PUBLIC_API_BASE_URL` tells the browser where the FastAPI service runs. The
local default is:

```sh
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

Never place passwords, database connection strings, cookies, tokens, or private
dataset paths in `NEXT_PUBLIC_*` variables.

FastAPI CORS is configured with `CORS_ALLOWED_ORIGINS`. Local development
defaults to `http://localhost:3000`, does not use `*`, and does not enable
browser credentials. The local CORS policy allows trusted browser `GET` and
`POST` requests, including CSV uploads from the configured web origin only.

Analytics API settings:

```sh
ANALYTICS_MAXIMUM_SNAPSHOT_AGE_HOURS=24
ANALYTICS_MINIMUM_SNAPSHOT_COUNT=3
```

Both values are validated at the Settings boundary and must be greater than
zero. If an invalid runtime override reaches the analysis route, the API returns
a stable `invalid_analytics_configuration` business error instead of exposing
internal details.

## Install Dependencies

```sh
make install
```

This installs the API dependencies with uv and the web dependencies with pnpm.

The analytics package is an independent uv project and can be installed without
starting the API, web app, PostgreSQL, or Docker:

```sh
make analytics-install
```

Equivalent direct command:

```sh
cd packages/analytics
uv --cache-dir ../../.uv-cache sync --dev
```

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
- CSV import page: http://localhost:3000/imports
- Item browser: http://localhost:3000/items
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

The browser upload flow is available at http://localhost:3000/imports. It
selects a local `.csv` file with a file input, displays only basic file metadata
such as name, size, browser-provided MIME, and selection status, and then sends
the file with `FormData`. It does not read the whole CSV into the UI, put file
contents in URLs, store the file in browser storage, or set the multipart
boundary manually.

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

Import the repository's clearly labeled synthetic fixture:

```sh
curl -F "file=@tests/fixtures/synthetic/valid_market_data.csv" http://localhost:8000/api/v1/imports/csv
```

Or open http://localhost:3000/imports and choose
`tests/fixtures/synthetic/valid_market_data.csv` in the file picker.

Web status meanings:

- `completed`: the file was accepted and valid rows were imported. The page
  shows `job_id`, filename, status, row counts, shortened checksum, warning and
  error counts, and a button to view `/items`.
- `duplicate`: the checksum matches an earlier successful import. The page
  shows the current duplicate job and `duplicate_of_job_id`; no additional data
  write is implied.
- `failed`: the import job failed and the page shows the structured error
  report. Errors and warnings are separated and initially capped to keep the
  page usable.

After a completed or duplicate import, open http://localhost:3000/items to view
the imported synthetic items. The browser uses only imported data; it does not
call Gaijin Market, calculate returns, or display prediction results.

## Read-Only Item Queries

The local API exposes read-only queries for market data already imported into
PostgreSQL:

- `GET /api/v1/items`
- `GET /api/v1/items/{item_id}`
- `GET /api/v1/items/{item_id}/snapshots`
- `GET /api/v1/items/{item_id}/analysis`

These endpoints do not write, edit, delete, backfill, interpolate, or calculate
returns. They are intended for browsing imported or explicitly authorized data.

List items:

```sh
curl "http://localhost:8000/api/v1/items?page=1&page_size=50&search=synthetic&category=vehicle&sort=name&order=asc"
```

Supported list parameters:

- `page`: default `1`, minimum `1`.
- `page_size`: default `50`, minimum `1`, maximum `100`.
- `search`: optional case-insensitive partial match on `name` and
  `external_key`.
- `category`, `rarity`, `is_active`: optional exact filters.
- `sort`: `name`, `created_at`, or `updated_at`.
- `order`: `asc` or `desc`.

The default list order is `name asc`, with `id asc` added as a stable secondary
sort. Each item includes at most one `latest_snapshot`; items without snapshots
return `"latest_snapshot": null`.

Item detail:

```sh
curl "http://localhost:8000/api/v1/items/1"
```

The detail response includes item metadata, `latest_snapshot`, `snapshot_count`,
`first_snapshot_at`, and `last_snapshot_at`. It does not include full snapshot
history.

Snapshot history:

```sh
curl "http://localhost:8000/api/v1/items/1/snapshots?from=2026-06-27T00:00:00Z&to=2026-06-28T00:00:00Z&limit=500&order=asc"
```

Supported history parameters:

- `from`, `to`: optional ISO-8601 datetimes with timezone. Values are converted
  to UTC before querying, and `from` must not be later than `to`.
- `limit`: default `500`, minimum `1`, maximum `2000`.
- `order`: `asc` or `desc`, default `asc`.

History responses are sorted by `observed_at` and `id`. Empty history returns
`[]`; a missing item returns:

```json
{
  "detail": {
    "code": "item_not_found",
    "message": "The requested item was not found."
  }
}
```

Decimal/NUMERIC fields are serialized as strings or `null`, never floats:

```json
{
  "id": 1,
  "item_id": 1,
  "observed_at": "2026-06-27T00:00:00Z",
  "best_ask": "12.340000",
  "best_bid": "11.100000",
  "ask_count": 3,
  "bid_count": 2,
  "estimated_volume": "44.500000",
  "source_import_job_id": 1,
  "created_at": "2026-06-27T00:00:01Z"
}
```

The Next.js frontend mirrors this contract. Price and volume fields are typed as
`string` or `string | null`; formatting such as `"42.500000"` to `"42.50"` is
display-only and does not convert monetary values to JavaScript numbers for
financial calculations.

Analysis:

```sh
curl "http://localhost:8000/api/v1/items/1/analysis?horizon=30&fee_rate=0.10&as_of=2026-06-29T00:00:00Z"
```

PowerShell:

```powershell
Invoke-RestMethod "http://localhost:8000/api/v1/items/1/analysis?horizon=30&fee_rate=0.10&as_of=2026-06-29T00:00:00Z"
```

Supported parameters:

- `horizon`: required, one of `7`, `30`, `90`, or `180`.
- `fee_rate`: required Decimal string satisfying `0 <= fee_rate < 1`.
- `as_of`: optional ISO-8601 datetime with timezone. When omitted, the API uses
  current UTC from a testable clock dependency.

The analysis route uses `packages/analytics` through the API project's local uv
path dependency. It maps ORM `MarketSnapshot` rows into plain
`MarketObservation` values, runs the explicit `rule_based` `1.0.0` strategy
from `StrategyRegistry`, and returns the computed result immediately without
writing analysis records. The database query is bounded to the inclusive
window `[as_of - horizon, as_of]` and sorted by `observed_at asc, id asc`.

`reference_sell_price` is a baseline reference sell price, not a guaranteed
future price. `confidence_score` is not a profit probability. Decimal fields,
including `fee_rate`, prices, profits, ROI, spreads, medians, volatility, and
scores, are serialized as strings or `null`, never JSON floats.

Missing items and invalid query parameters return stable HTTP business errors.
Normal analysis limitations such as empty windows, insufficient snapshot count,
insufficient time coverage, stale latest snapshots, or no valid bid/ask return
HTTP 200 with analysis `status` and `reason_codes`.

## Verify

```sh
make analytics-test
make analytics-lock-check
make test-api
pnpm web:test
make web-lint
pnpm --filter @gaijin-market-analytics/web typecheck
make web-build
make compose-config
```

Analytics checks can also be run directly:

```sh
cd packages/analytics
python -m compileall src
uv --cache-dir ../../.uv-cache run pytest
uv --cache-dir ../../.uv-cache lock --check
```

API and analytics have separate uv projects and separate lock files:

- `packages/analytics/uv.lock`
- `apps/api/uv.lock`

Use `uv lock --check` in each project directory when validating lock-file
consistency. The current repository intentionally does not have a root Python uv
workspace.

## Compliance Boundary

Local development must use CSV, JSON, manual, fixture, or explicitly authorized
data only. This scaffold does not include marketplace scraping, login
automation, internal endpoint calls, or automated buy, sell, cancel, account, or
payment actions.
