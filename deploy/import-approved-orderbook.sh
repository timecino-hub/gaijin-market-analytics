#!/usr/bin/env sh
set -eu

root="${INTAKE_ROOT:-/var/lib/gaijin-market-web-mvp/intake}"
project="${COMPOSE_PROJECT_NAME:-gaijin-market-web-mvp}"
file="${1:-}"
shift || true
write=false
confirm_pending=false

if [ -z "$file" ]; then
  echo "usage: $0 <order-book-inbox-file.json> [--confirm-pending] [--write]" >&2
  exit 2
fi
for argument in "$@"; do
  case "$argument" in
    --write) write=true ;;
    --confirm-pending) confirm_pending=true ;;
    *) echo "unknown argument: $argument" >&2; exit 2 ;;
  esac
done
if [ -L "$file" ] || [ ! -f "$file" ]; then
  echo "order-book input must be one regular non-symlink file" >&2
  exit 2
fi
resolved="$(realpath "$file")"
case "$resolved" in
  "$root/order-books"/*) ;;
  *) echo "order-book input must be inside $root/order-books" >&2; exit 2 ;;
esac

basename="$(basename "$resolved")"
case "$basename" in
  *.json) ;;
  *) echo "order-book input must use the .json extension" >&2; exit 2 ;;
esac

set -- docker compose -p "$project" run --rm \
  -v "$root/order-books:/intake:ro" api \
  uv run python -m api.manual_order_book_import_cli "/intake/$basename"
if [ "$confirm_pending" = true ]; then
  set -- "$@" --confirm-pending
fi
if [ "$write" = true ]; then
  set -- "$@" --write
fi
"$@"

if [ "$write" = true ]; then
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  prefix="$(sha256sum "$resolved" | cut -c1-12)"
  archived="$root/processed/order-book-$timestamp-$prefix.json"
  mv "$resolved" "$archived"
  chmod 0600 "$archived"
  printf 'archived=%s\n' "$archived"
fi
