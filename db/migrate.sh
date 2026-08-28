#!/usr/bin/env bash
# Applies the non-bootstrap SQL migrations in lexical order against the
# dev database (REST API + schema), but only on FIRST boot.
#
# Why first-boot-only: several migrations are not idempotent across runs
# (e.g. 0040's `CREATE OR REPLACE FUNCTION rw_send_message` cannot change
# its return type, which 0110 later drops+recreates). Re-applying them on
# an existing schema errors. More importantly, resetting the schema on
# every boot would regenerate every user UUID and silently invalidate all
# previously-issued JWTs (the copilot audit insert keys `rw_user_id` off
# the JWT `sub`, so a stale token becomes a foreign-key 500).
#
# So: on a fresh volume (rw_user absent) we reset + migrate + let `seed`
# load the corpus. On subsequent boots rw_user already exists → skip, so
# data and sessions stay stable across restarts.
#
# This service runs as the postgres superuser (dev-only; the runtime
# `rw_app` role never touches DDL). Roles are cluster-level objects and
# survive a DROP SCHEMA, so 0002_roles.sql stays as an initdb bootstrap
# and is skipped here; 0001 (extensions) is re-applied after the schema
# reset because the extensions lived inside `public`.
set -euo pipefail

MIGRATIONS_DIR="${MIGRATIONS_DIR:-/migrations}"
DB_URL="${DB_URL:-postgresql://postgres:postgres@db:5432/db_santiago_sanchez_nakamoto}"

if psql "$DB_URL" -tAc "SELECT 1 FROM pg_class WHERE relname='rw_user' LIMIT 1" | grep -q 1; then
  echo "migrate: schema already present, skipping (preserves UUIDs + sessions)."
  exit 0
fi

echo "migrate: fresh schema, resetting public (dev-only)"
# `CREATE SCHEMA public` (unlike initdb's built-in public schema) does not
# grant USAGE to PUBLIC by default in PG15+. Restore the same grant the
# original dev schema had so the runtime rw_app_login role can reach the
# tables (table-level grants are applied by 0080_grants.sql).
psql "$DB_URL" -v ON_ERROR_STOP=1 -q \
  -c "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;" \
  -c "GRANT ALL ON SCHEMA public TO PUBLIC;"

applied=0
for f in "$MIGRATIONS_DIR"/*.sql; do
  base="$(basename "$f")"
  case "$base" in
    0002_roles.sql)
      echo "skip $base (roles survive schema reset; bootstrap via initdb)"
      continue
      ;;
  esac
  echo "Applying $base"
  psql "$DB_URL" -v ON_ERROR_STOP=1 -q -f "$f"
  applied=$((applied + 1))
done

echo "migrate: $applied migration file(s) applied."
