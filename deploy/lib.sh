#!/usr/bin/env bash
# Shared helpers for the deployment scripts (ADR-0007). Sourced, never executed.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DRY_RUN=${DRY_RUN:-0}

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die() { log "error: $*"; exit 1; }

# Print a command instead of running it under --dry-run. Only commands that change state go
# through `run`; read-only checks always execute, so a dry run still validates the host.
run() {
  if [[ $DRY_RUN == 1 ]]; then
    printf '[dry-run] %s\n' "$*" >&2
  else
    "$@"
  fi
}

require_cmd() {
  local cmd
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null || die "missing command: $cmd"
  done
}

# Read KEY from a dotenv file (default: the repository's .env) without sourcing it: compose
# syntax is not bash, and sourcing would execute whatever the file contains.
env_get() {
  local key=$1 file=${2:-$REPO_ROOT/.env} line value
  [[ -f $file ]] || return 1
  line=$(grep -E "^[[:space:]]*${key}=" "$file" | tail -n 1) || return 1
  value=${line#*=}
  value=${value%$'\r'}
  if [[ $value =~ ^\"(.*)\"$ || $value =~ ^\'(.*)\'$ ]]; then
    value=${BASH_REMATCH[1]}
  fi
  [[ -n $value ]] || return 1
  printf '%s' "$value"
}

compose() { docker compose --project-directory "$REPO_ROOT" "$@"; }

# One line per public table ("table <name> <rows>") and per applied migration
# ("migration <version>"), sorted, so two databases can be compared with diff.
# shellcheck disable=SC2016  # $... inside the SQL is not a shell expansion
SUMMARY_SQL='
SELECT line FROM (
  SELECT format($$table %s %s$$, table_name,
    (xpath($$/row/c/text()$$, query_to_xml(
      format($$SELECT count(*) AS c FROM %I.%I$$, table_schema, table_name),
      false, true, $$$$)))[1]::text) AS line
  FROM information_schema.tables
  WHERE table_schema = $$public$$ AND table_type = $$BASE TABLE$$
  UNION ALL
  SELECT $$migration $$ || version FROM schema_migrations
) AS summary ORDER BY line'

# restore_check DUMP IMAGE: restore a `pg_dump -Fc` file into a throwaway Postgres (no network,
# data in memory) and print its summary. Fails if the restore reports any error.
restore_check() (
  local dump=$1 image=$2 name tries=0
  name="dzdia-restore-check-$$-$RANDOM"
  trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
  docker run -d --rm --name "$name" --network none --tmpfs /var/lib/postgresql/data \
    -e POSTGRES_USER=dzdia -e POSTGRES_DB=dzdia -e POSTGRES_PASSWORD=restore-check \
    "$image" >/dev/null
  # TCP, not the socket: during initdb the image runs a temporary server on the socket only.
  until docker exec "$name" pg_isready -q -h 127.0.0.1 -U dzdia -d dzdia; do
    ((++tries <= 60)) || die "restore check: Postgres did not start"
    sleep 1
  done
  docker exec -i "$name" pg_restore -U dzdia -d dzdia --no-owner --exit-on-error <"$dump"
  docker exec "$name" psql -U dzdia -d dzdia -XAt -v ON_ERROR_STOP=1 -c "$SUMMARY_SQL"
)
