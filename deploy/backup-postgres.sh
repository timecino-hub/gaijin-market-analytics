#!/usr/bin/env sh
set -eu

project="${COMPOSE_PROJECT_NAME:-gaijin-market-web-mvp}"
backup_dir="${BACKUP_DIR:-/var/backups/gaijin-market-web-mvp/postgres}"
retention_days="${BACKUP_RETENTION_DAYS:-14}"

case "$backup_dir" in
  /*) ;;
  *) echo "BACKUP_DIR must be an absolute path" >&2; exit 2 ;;
esac

case "$backup_dir" in
  /|/var|/var/backups) echo "Refusing unsafe BACKUP_DIR: $backup_dir" >&2; exit 2 ;;
esac

case "$retention_days" in
  ''|*[!0-9]*) echo "BACKUP_RETENTION_DAYS must be a non-negative integer" >&2; exit 2 ;;
esac

umask 077
install -d -m 0700 "$backup_dir"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$backup_dir/gaijin-market-$timestamp.dump"
temporary="$backup_dir/.gaijin-market-$timestamp.dump.tmp"
trap 'rm -f "$temporary"' EXIT HUP INT TERM

docker compose -p "$project" exec -T postgres sh -c \
  'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' \
  > "$temporary"

test -s "$temporary"
mv "$temporary" "$target"
sha256sum "$target" > "$target.sha256"
find "$backup_dir" -type f \( -name '*.dump' -o -name '*.dump.sha256' \) \
  -mtime "+$retention_days" -delete

printf '%s\n' "$target"
