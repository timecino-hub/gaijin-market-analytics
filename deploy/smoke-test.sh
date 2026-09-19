#!/usr/bin/env sh
set -eu

base_url="${1:-https://bvvd-analysis.duckdns.org}"
base_url="${base_url%/}"

check() {
  method="$1"
  path="$2"
  expected="$3"
  actual="$(curl --silent --show-error --max-time 20 --output /dev/null \
    --write-out '%{http_code}' --request "$method" "$base_url$path")"
  if [ "$actual" != "$expected" ]; then
    echo "$method $path: expected $expected, got $actual" >&2
    exit 1
  fi
  echo "$method $path: $actual"
}

check GET / 200
check GET /health 200
check GET '/api/v1/items?page=1&page_size=5' 200
check POST / 405
check POST /api/v1/imports/csv 405
check GET /api/v1/items/1/snapshots 405
check GET /imports 404

echo "Read-only deployment smoke test passed."
