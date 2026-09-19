#!/usr/bin/env sh
set -eu

root="${INTAKE_ROOT:-/var/lib/gaijin-market-web-mvp/intake}"
case "$root" in
  /*) ;;
  *) echo "INTAKE_ROOT must be an absolute path" >&2; exit 2 ;;
esac
case "$root" in
  /|/var|/var/lib) echo "Refusing unsafe INTAKE_ROOT: $root" >&2; exit 2 ;;
esac

umask 077
install -d -m 0700 \
  "$root" \
  "$root/catalog" \
  "$root/order-books" \
  "$root/processed" \
  "$root/rejected"
printf '%s\n' "$root"
