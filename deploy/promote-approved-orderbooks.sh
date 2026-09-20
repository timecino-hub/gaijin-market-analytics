#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."
docker compose exec -T api \
  uv run python -m api.manual_order_book_snapshot_cli "$@"
