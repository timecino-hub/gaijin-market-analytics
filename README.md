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

Start the API on port 8000:

```sh
make api-dev
```

Start the web app on port 3000:

```sh
make web-dev
```

The API exposes `GET /health`. FastAPI's OpenAPI schema remains available at
the default `/openapi.json`.

## Checks

```sh
make api-test
make web-lint
make web-build
make compose-config
```

## Current Scope

Implemented: project skeleton, API health check, API test, Next.js shell, local
configuration examples, PostgreSQL Docker Compose service, and basic Makefile
commands.

Not implemented: CSV import, business database models, item APIs, analytics
calculations, 7/30/90/180 day algorithms, machine-learning dependencies, user
accounts, marketplace scraping, login automation, or automated trading actions.

Future analysis periods are 7, 30, 90, and 180 days. They are not implemented in
this scaffold.
