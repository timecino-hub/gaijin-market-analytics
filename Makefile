.PHONY: install analytics-install analytics-test analytics-lock-check api-install web-install api-dev web-dev db-up db-down db-migrate db-downgrade db-current api-test test-api web-test web-lint web-typecheck web-build compose-config test

UV_CACHE_DIR ?= $(CURDIR)/.uv-cache

install: api-install web-install

analytics-install:
	cd packages/analytics && uv --cache-dir $(UV_CACHE_DIR) sync --dev

analytics-test:
	cd packages/analytics && uv --cache-dir $(UV_CACHE_DIR) run pytest

analytics-lock-check:
	cd packages/analytics && uv --cache-dir $(UV_CACHE_DIR) lock --check

api-install:
	cd apps/api && UV_CACHE_DIR=$(UV_CACHE_DIR) uv sync --dev

web-install:
	pnpm install

api-dev:
	cd apps/api && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run api

web-dev:
	pnpm --filter @gaijin-market-analytics/web dev

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

db-migrate:
	cd apps/api && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run alembic upgrade head

db-downgrade:
	cd apps/api && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run alembic downgrade -1

db-current:
	cd apps/api && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run alembic current

api-test:
	cd apps/api && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run pytest

test-api: api-test

web-test:
	pnpm --filter @gaijin-market-analytics/web test

web-lint:
	pnpm --filter @gaijin-market-analytics/web lint

web-typecheck:
	pnpm --filter @gaijin-market-analytics/web typecheck

web-build:
	pnpm --filter @gaijin-market-analytics/web build

compose-config:
	docker compose config

test: analytics-test api-test web-test web-lint web-typecheck web-build compose-config
