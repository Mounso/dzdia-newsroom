#!/usr/bin/env bash
# Encrypted Postgres backup (ADR-0007): pg_dump, restore check, age encryption, upload to Oracle
# Object Storage through the compose `rclone` service (instance principal, add-only policy).
#
#   deploy/backup.sh [daily|predeploy]        run by dzdia-backup.timer, and by deploy.sh
#   deploy/backup.sh daily --keep-local DIR   write the encrypted file to DIR, no upload
#
# Reads from .env: BACKUP_AGE_RECIPIENT (public key), BACKUP_BUCKET, and optionally
# BACKUP_HEALTHCHECK_URL (healthchecks.io ping URL, daily runs only).
set -euo pipefail
# shellcheck source=deploy/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

HEALTHCHECK_URL=""
WORK=""  # global: the EXIT trap runs after main has returned

ping_healthcheck() {
  [[ -n $HEALTHCHECK_URL ]] || return 0
  curl -fsS --max-time 10 --retry 3 -o /dev/null "$HEALTHCHECK_URL$1" ||
    log "warning: healthcheck ping failed"
}

# On any exit: remove the plain dump; on failure, tell the dead man's switch.
cleanup() {
  local status=$?
  [[ -z $WORK ]] || rm -rf "$WORK"
  ((status == 0)) || ping_healthcheck /fail
}

main() {
  local kind=${1:-daily} keep_local="" recipient bucket="" work stamp name image prefix
  local -a prefixes
  case $kind in daily | predeploy) ;; *) die "usage: $0 [daily|predeploy] [--keep-local DIR]" ;; esac
  if [[ ${2:-} == --keep-local ]]; then
    keep_local=${3:?--keep-local needs a directory}
    [[ -d $keep_local ]] || die "$keep_local is not a directory"
  fi
  require_cmd docker age curl
  recipient=$(env_get BACKUP_AGE_RECIPIENT) || die "set BACKUP_AGE_RECIPIENT in .env"
  [[ $recipient == age1* ]] || die "BACKUP_AGE_RECIPIENT must be an age public key (age1...)"
  if [[ -z $keep_local ]]; then
    bucket=$(env_get BACKUP_BUCKET) || die "set BACKUP_BUCKET in .env"
  fi
  if [[ $kind == daily ]]; then
    HEALTHCHECK_URL=$(env_get BACKUP_HEALTHCHECK_URL) || HEALTHCHECK_URL=""
  fi

  WORK=$(mktemp -d)
  work=$WORK
  chmod 700 "$work"
  trap cleanup EXIT
  ping_healthcheck /start

  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  name="dzdia-$stamp-$kind.dump"
  log "dumping the database"
  compose exec -T postgres pg_dump -U dzdia -d dzdia -Fc >"$work/$name"

  # Restore into a throwaway container from the live server's exact image, then compare tables
  # and applied migrations. Row counts are only logged: the live database keeps changing.
  image=$(docker inspect --format '{{.Config.Image}}' "$(compose ps -q postgres)")
  log "restore check with $image"
  restore_check "$work/$name" "$image" >"$work/restored.txt"
  compose exec -T postgres psql -U dzdia -d dzdia -XAt -v ON_ERROR_STOP=1 -c "$SUMMARY_SQL" >"$work/live.txt"
  if ! diff <(cut -d' ' -f1,2 "$work/restored.txt") <(cut -d' ' -f1,2 "$work/live.txt") >&2; then
    die "restore check: the restored database differs from the live one"
  fi
  log "restore check passed: $(grep -c '^table ' "$work/restored.txt") tables, rows: $(grep '^table ' "$work/restored.txt" | cut -d' ' -f2- | paste -sd, -)"

  age -r "$recipient" -o "$work/$name.age" "$work/$name"
  rm -f "$work/$name"

  if [[ -n $keep_local ]]; then
    mv "$work/$name.age" "$keep_local/"
    log "backup written to $keep_local/$name.age (not uploaded)"
  else
    prefixes=("$kind")
    [[ $kind == daily && $(date -u +%u) == 7 ]] && prefixes+=(weekly)
    for prefix in "${prefixes[@]}"; do
      compose run --rm -T rclone rcat "oos:$bucket/$prefix/$name.age" <"$work/$name.age"
      log "uploaded $prefix/$name.age ($(wc -c <"$work/$name.age") bytes)"
    done
  fi
  ping_healthcheck ""
}

main "$@"
