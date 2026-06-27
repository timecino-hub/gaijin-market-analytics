.PHONY: install api-install web-install api-dev web-dev db-up db-down api-test web-lint web-build compose-config test

UV_CACHE_DIR ?= $(CURDIR)/.uv-cache

install: api-install web-install

api-install:
	cd apps/api && UV_CACHE_DIR=$(UV_CACHE_DIR) uv sync --dev

web-install:
	pnpm install

api-dev:
	cd apps/api && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

web-dev:
	pnpm --filter @gaijin-market-analytics/web dev

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

api-test:
	cd apps/api && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run pytest

web-lint:
	pnpm --filter @gaijin-market-analytics/web lint

web-build:
	pnpm --filter @gaijin-market-analytics/web build

compose-config:
	docker compose config

test: api-test web-lint web-build compose-config
