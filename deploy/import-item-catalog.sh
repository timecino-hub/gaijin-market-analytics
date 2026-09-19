#!/usr/bin/env sh
set -eu

root="${INTAKE_ROOT:-/var/lib/gaijin-market-web-mvp/intake}"
project="${COMPOSE_PROJECT_NAME:-gaijin-market-web-mvp}"
file="${1:-}"
mode="${2:-}"

if [ -z "$file" ] || { [ -n "$mode" ] && [ "$mode" != "--write" ]; }; then
  echo "usage: $0 <catalog-inbox-file.csv> [--write]" >&2
  exit 2
fi
if [ -L "$file" ] || [ ! -f "$file" ]; then
  echo "catalog input must be one regular non-symlink file" >&2
  exit 2
fi
resolved="$(realpath "$file")"
case "$resolved" in
  "$root/catalog"/*) ;;
  *) echo "catalog input must be inside $root/catalog" >&2; exit 2 ;;
esac

basename="$(basename "$resolved")"
case "$basename" in
  *.csv) ;;
  *) echo "catalog input must use the .csv extension" >&2; exit 2 ;;
esac

set -- docker compose -p "$project" run --rm \
  -v "$root/catalog:/intake:ro" api \
  uv run python -m api.item_catalog_import_cli "/intake/$basename"
if [ "$mode" = "--write" ]; then
  set -- "$@" --write
fi
"$@"

if [ "$mode" = "--write" ]; then
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  prefix="$(sha256sum "$resolved" | cut -c1-12)"
  archived="$root/processed/catalog-$timestamp-$prefix.csv"
  mv "$resolved" "$archived"
  chmod 0600 "$archived"
  printf 'archived=%s\n' "$archived"
fi
