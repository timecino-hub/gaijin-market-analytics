# Local Development

## Prerequisites

- Python 3.12
- uv
- Node.js 24.18.0
- pnpm 11.7.0
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

The API process binds to `127.0.0.1` by default when run directly. If an API
container is added later, it may listen on `0.0.0.0` inside the container, but
the host port should be published only on loopback, for example
`127.0.0.1:8000:8000`.

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
call Gaijin Market, contact marketplace pages, or perform account or trading
actions.

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
curl "http://localhost:8000/api/v1/items/1/analysis?horizon=30&as_of=2026-06-29T00:00:00Z"
```

PowerShell:

```powershell
Invoke-RestMethod "http://localhost:8000/api/v1/items/1/analysis?horizon=30&as_of=2026-06-29T00:00:00Z"
```

Supported parameters:

- `horizon`: required, one of `7`, `30`, `90`, or `180`.
- `as_of`: optional ISO-8601 datetime with timezone. When omitted, the API uses
  current UTC from a testable clock dependency.

The route uses the fixed Gaijin Market fee policy from analytics code:
`gaijin_market` `1.0.0`, nominal fee rate `0.15`, currency quantum `0.01`, and
seller proceeds rounded down after the nominal fee is applied. The API does not
accept configurable `fee_rate`; old URLs containing that parameter return
HTTP 400 with `fee_rate_not_configurable`.

The analysis route uses `packages/analytics` through the API project's local uv
path dependency. It maps ORM `MarketSnapshot` rows into plain
`MarketObservation` values, runs the explicit `rule_based` `1.0.0` strategy
from `StrategyRegistry`, and returns the computed result immediately without
writing analysis records. The database query is bounded to the inclusive
window `[as_of - horizon, as_of]` and sorted by `observed_at asc, id asc`.

`reference_sell_price` is a baseline reference sell price rounded down to the
0.01 GJN price quantum, not a guaranteed future price. `sale_proceeds` is the
seller settlement amount after nominal fee and round-down; `fee_amount` is
`reference_sell_price - sale_proceeds`. `confidence_score` is not a profit
probability. Decimal fields, including fee policy values, prices, proceeds,
profits, ROI, spreads, medians, volatility, and scores, are serialized as
strings or `null`, never JSON floats.

Settlement example:

```text
listed sell price: 1.99 GJN
raw seller proceeds: 1.6915 GJN
settled seller proceeds: 1.69 GJN
actual fee amount: 0.30 GJN
```

Missing items and invalid query parameters return stable HTTP business errors.
Normal analysis limitations such as empty windows, insufficient snapshot count,
insufficient time coverage, stale latest snapshots, or no valid bid/ask return
HTTP 200 with analysis `status` and `reason_codes`.

The web item detail route, `http://localhost:3000/items/{item_id}`, includes a
read-only analysis panel. Use it as follows:

1. Choose 7, 30, 90, or 180 days. Each window is computed independently from
   the imported snapshots in that exact horizon.
2. Review the fixed fee policy shown by the panel: 15% nominal fee, 0.01 GJN
   settlement quantum, and seller proceeds rounded down. There is no fee input.
3. Optionally choose an `as_of` local date/time for historical reproduction.
   Empty `as_of` is not sent; non-empty values are converted to timezone-aware
   ISO-8601 before the request. The field is not a prediction endpoint.
4. Click "Run analysis". Editing fields alone does not update the URL or call
   the API.

The submitted analysis state is reflected in the URL as `horizon` and optional
`as_of`. A refresh with a valid `horizon` restores the form and may run one
analysis request. Legacy `fee_rate` parameters are ignored and removed on the
next URL update.
Snapshot query parameters (`from`, `to`, `limit`, `order`) and unknown query
parameters are preserved when the analysis panel updates its own parameters.

The panel separates HTTP request failures from successful HTTP 200 analysis
results. A successful response with `status != "ok"` is shown as data
insufficiency or market-state limitation, not as a system outage. Missing
Decimal values are displayed as `—`, never as zero. `reference_sell_price` is a
baseline reference sell price, `sale_proceeds` is seller settlement, and
`confidence_score` is a data/rule confidence score; none of these are a profit
guarantee, profit probability, or trading advice.

## Backtesting CLI

The local backtesting entry point is a developer/admin CLI, not a public API or
web route:

```sh
cd apps/api
uv run python -m api.backtesting_cli \
  --item-id 123 \
  --lookback-horizon 30 \
  --forward-horizon 30 \
  --start 2026-01-01T00:00:00Z \
  --end 2026-06-01T00:00:00Z \
  --cadence-days 7 \
  --pretty
```

`--start` and `--end` must include `Z` or an explicit UTC offset. The CLI reads
only the requested `item_id`, from `start_at - lookback_horizon` through
`end_at + forward_horizon`, ordered by `observed_at asc, id asc`. Results are
printed to stdout as JSON and are not written to the database. Decimal values
are strings or `null`.

Backtest outputs are based on future snapshots, primarily future legal
`best_bid`, and are not realized trade returns, profit promises, or automated
trading instructions.

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
