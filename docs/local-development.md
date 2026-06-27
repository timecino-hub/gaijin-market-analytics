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
- API OpenAPI JSON: http://localhost:8000/openapi.json
- PostgreSQL: localhost:5432

## Verify

```sh
make api-test
make web-lint
make web-build
make compose-config
```

## Compliance Boundary

Local development must use CSV, JSON, manual, fixture, or explicitly authorized
data only. This scaffold does not include marketplace scraping, login
automation, internal endpoint calls, or automated buy, sell, cancel, account, or
payment actions.
