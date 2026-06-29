# Gaijin Market Analytics

Minimal engineering scaffold for a compliant Gaijin Market analytics website.
This repository is limited to imported, manual, or explicitly authorized data.
It does not implement marketplace automation, account automation, trading, or
profit promises.

Allowed future data sources are CSV files, JSON files, manual entry, or data
sources with explicit written authorization.

## Stack

- Python 3.12 managed with uv
- FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, psycopg 3
- pytest for API tests
- Node.js 22 LTS with pnpm
- Next.js and TypeScript
- PostgreSQL through Docker Compose

## Analytics Package

`packages/analytics` is an independent uv project for pure Python analytics
contracts, Decimal statistics, fee calculations, horizon selection, strategy
registration, and the first transparent baseline strategy. It does not import
FastAPI, SQLAlchemy ORM models, database sessions, HTTP clients, filesystem
read/write helpers, or environment variables. All inputs are passed explicitly
through immutable contracts and all outputs are returned as typed values.

Install and test it separately from the API:

```sh
cd packages/analytics
uv --cache-dir ../../.uv-cache sync --dev
uv --cache-dir ../../.uv-cache run pytest
uv --cache-dir ../../.uv-cache lock --check
```

Or from the repository root:

```sh
make analytics-install
make analytics-test
make analytics-lock-check
```

The package uses Python `Decimal` for money, ratios, scores, fees, profit, ROI,
and break-even calculations. It does not convert analytics values to JSON
floats. Timestamps must be timezone-aware and are normalized to UTC at the
contract boundary. Contract errors such as naive datetimes, future observations,
invalid item IDs, and invalid fee rates raise stable domain exceptions.

The supported analysis horizons are 7, 30, 90, and 180 days. Each window is
selected independently as `[as_of - horizon, as_of]`; shorter windows are never
derived from longer-window results.

`RuleBasedV1` is a deterministic baseline used to exercise the analytics
contract and replacement mechanism. Its `reference_sell_price` is the median
valid bid inside the selected window. This is an explainable timing reference,
not a future price prediction, profit guarantee, or trading recommendation.
The result always includes strategy, strategy version, and feature version so
future backtests can reproduce which implementation generated an output.

See `docs/analytics-design.md` for the input/output contracts, fee math,
scoring formulas, data insufficiency behavior, and registry design.

## Install

```sh
make install
```

Or install each side independently:

```sh
make api-install
make web-install
```

## Development

Copy the example environment file before running services:

```sh
cp .env.example .env
```

Start PostgreSQL:

```sh
make db-up
```

Run database migrations:

```sh
make db-migrate
```

Start the API on port 8000:

```sh
make api-dev
```

Start the web app on port 3000:

```sh
make web-dev
```

The web app reads the API base URL from `NEXT_PUBLIC_API_BASE_URL`. For local
development, copy `apps/web/.env.local.example` to `apps/web/.env.local` and
keep the default value:

```sh
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

Only public browser-safe values belong in `NEXT_PUBLIC_*` variables. Do not put
database URLs, passwords, tokens, cookies, or private datasets in them.

The API exposes `GET /health` as a database-independent liveness check and
`GET /ready` as a PostgreSQL readiness check. FastAPI's OpenAPI schema remains
available at the default `/openapi.json`.

The API enables CORS for configured origins only. Local development defaults to
`CORS_ALLOWED_ORIGINS=http://localhost:3000`; it does not use a wildcard origin
or browser credentials.

CSV import endpoints:

- `POST /api/v1/imports/csv`
- `GET /api/v1/imports/{job_id}`

CSV imports accept only user-uploaded `.csv` files, default to a 10 MB upload
limit, compute a SHA-256 checksum from the original bytes, and reject duplicate
files by checksum without inserting duplicate snapshots. The CSV v1 fields are:
`item_key`, `item_name`, `category`, `observed_at`, `best_ask`, `best_bid`,
`ask_count`, `bid_count`, and `estimated_volume`. Prices and volume are parsed
with Python `Decimal`; JSON responses return import metadata and error reports,
not the uploaded file contents.

The web app exposes the CSV upload flow at `http://localhost:3000/imports`.
Choose a local `.csv` file, review the filename, size, browser-provided MIME
type, and selection state, then upload it with the "上传 CSV" button. The
browser applies the same default 10 MB size limit as a user-experience
precheck, but the API remains the trusted validator.

Import statuses shown by the web page:

- `completed`: the CSV was accepted and valid rows were imported into the
  local database. The result shows row counts, a shortened checksum, warning
  count, error count, and a link to `/items`.
- `duplicate`: the checksum matches an earlier successful import. The result
  shows both the current duplicate `job_id` and `duplicate_of_job_id`; it does
  not imply that rows were written again.
- `failed`: the API created a failed job and returned a structured error
  report. Errors and warnings are displayed separately, with long lists capped
  on initial render.

Read-only market query endpoints:

- `GET /api/v1/items`
- `GET /api/v1/items/{item_id}`
- `GET /api/v1/items/{item_id}/snapshots`
- `GET /api/v1/items/{item_id}/analysis`

These endpoints expose only items and snapshots already imported into
PostgreSQL from allowed sources. They do not create, edit, delete, enrich,
backfill, interpolate, or calculate returns from market data.

`GET /api/v1/items` lists items with pagination and optional filters:

```sh
curl "http://localhost:8000/api/v1/items?page=1&page_size=50&search=synthetic&sort=name&order=asc"
```

Supported query parameters are `page`, `page_size`, `search`, `category`,
`rarity`, `is_active`, `sort`, and `order`. Sorting allows `name`,
`created_at`, and `updated_at`; results always add `id asc` as a stable
secondary sort. Each item includes at most one `latest_snapshot` value.

`GET /api/v1/items/{item_id}` returns item metadata, its latest snapshot, and
snapshot summary timestamps/counts. It does not return full history.

`GET /api/v1/items/{item_id}/snapshots` returns bounded historical snapshots:

```sh
curl "http://localhost:8000/api/v1/items/1/snapshots?from=2026-06-27T00:00:00Z&to=2026-06-28T00:00:00Z&limit=500&order=asc"
```

The `from` and `to` filters must be ISO-8601 datetimes with a timezone and are
normalized to UTC. Snapshot history is sorted by `observed_at` and `id`.
Decimal/NUMERIC fields such as `best_ask`, `best_bid`, and
`estimated_volume` are returned as JSON strings or `null`, never floats.
The web UI keeps those values as strings and only formats them for display, so
browser floating-point arithmetic is not used for monetary values.

`GET /api/v1/items/{item_id}/analysis` computes a read-only baseline analysis
from snapshots already stored in PostgreSQL:

```sh
curl "http://localhost:8000/api/v1/items/1/analysis?horizon=7&fee_rate=0.10&as_of=2026-06-29T00:00:00Z"
```

Supported query parameters:

- `horizon`: required, one of `7`, `30`, `90`, or `180`.
- `fee_rate`: required Decimal string satisfying `0 <= fee_rate < 1`. The API
  does not assume a real marketplace fee.
- `as_of`: optional ISO-8601 datetime with timezone. When omitted, the API uses
  the current UTC time through a testable clock dependency.

The API queries only the inclusive database window `[as_of - horizon, as_of]`
using `observed_at >= as_of - horizon` and `observed_at <= as_of`, ordered by
`observed_at asc, id asc`. Results are computed immediately and are not
persisted; this repository does not create an `analysis_results` table.

The response includes item metadata, effective inputs, strategy metadata, and
the `RuleBasedV1` output. `reference_sell_price` is a baseline reference sell
price derived from the median valid bid inside the selected window, not a
guaranteed future price. `confidence_score` is an explainability score, not a
profit probability. Decimal values, including `fee_rate`, profit/ROI fields,
spreads, medians, volatility, and scores, are returned as JSON strings or
`null`, never JSON floats.

HTTP errors are reserved for missing items, invalid query parameters, invalid
contract inputs, unavailable strategies, or invalid analytics configuration.
Normal data limitations such as empty windows, too few snapshots, insufficient
coverage, stale latest snapshots, or no valid bid/ask return HTTP 200 with
analysis `status` and stable `reason_codes`.

Web routes:

- `/`: project overview, compliance notice, and entry to the item browser.
- `/imports`: browser CSV upload flow for authorized CSV v1 data.
- `/items`: searchable and paginated list of imported items.
- `/items/{item_id}`: item metadata, latest snapshot, and historical snapshot
  table with time range filters.

The current web scope does not include profit analysis UI, predictive results,
or 7/30/90/180 day analysis buttons.

Example item list response:

```json
{
  "items": [
    {
      "id": 1,
      "external_key": "synthetic-item-alpha",
      "name": "Synthetic Alpha",
      "category": "vehicle",
      "rarity": null,
      "is_active": true,
      "created_at": "2026-06-27T00:00:00Z",
      "updated_at": "2026-06-27T00:00:00Z",
      "latest_snapshot": {
        "observed_at": "2026-06-27T01:00:00Z",
        "best_ask": "12.340000",
        "best_bid": "11.100000",
        "ask_count": 3,
        "bid_count": 2,
        "estimated_volume": "44.500000"
      }
    }
  ],
  "page": 1,
  "page_size": 50,
  "total": 1,
  "total_pages": 1
}
```

Migration helpers:

```sh
make db-current
make db-downgrade
make db-migrate
```

Import the labeled synthetic fixture from this repository after starting the API:

```sh
curl -F "file=@tests/fixtures/synthetic/valid_market_data.csv" http://localhost:8000/api/v1/imports/csv
```

You can also open `http://localhost:3000/imports` and select the same fixture
from the file picker. Then open `http://localhost:3000/items` to browse the
imported synthetic items.

## Checks

```sh
make analytics-test
make test-api
pnpm web:test
make web-lint
pnpm --filter @gaijin-market-analytics/web typecheck
make web-build
make compose-config
```

## Current Scope

Implemented: project skeleton, API health and readiness checks, API tests,
Next.js shell, local configuration examples, PostgreSQL Docker Compose service,
SQLAlchemy async database setup, Alembic migration commands, database foundation
tables, compliant CSV market data import, web CSV upload flow, read-only
item/snapshot query APIs, read-only immediate baseline analysis API, and the
standalone pure Python analytics foundation.

Not implemented: item write APIs, standalone snapshot write APIs, persisted
analysis results, machine-learning dependencies, user accounts, marketplace
scraping, login automation, or automated trading actions.
