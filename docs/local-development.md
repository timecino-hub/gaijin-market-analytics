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
