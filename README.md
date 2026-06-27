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

The API exposes `GET /health` as a database-independent liveness check and
`GET /ready` as a PostgreSQL readiness check. FastAPI's OpenAPI schema remains
available at the default `/openapi.json`.

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

Read-only market query endpoints:

- `GET /api/v1/items`
- `GET /api/v1/items/{item_id}`
- `GET /api/v1/items/{item_id}/snapshots`

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

## Checks

```sh
make test-api
make web-lint
make web-build
make compose-config
```

## Current Scope

Implemented: project skeleton, API health and readiness checks, API tests,
Next.js shell, local configuration examples, PostgreSQL Docker Compose service,
SQLAlchemy async database setup, Alembic migration commands, database foundation
tables, compliant CSV market data import, and read-only item/snapshot query
APIs.

Not implemented: item write APIs, standalone snapshot write APIs, analytics
calculations, 7/30/90/180 day algorithms, machine-learning dependencies, user
accounts, marketplace scraping, login automation, or automated trading actions.

Future analysis periods are 7, 30, 90, and 180 days. They are not implemented in
this scaffold.
