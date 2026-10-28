#!/usr/bin/env bash
# Monthly restore drill (ADR-0007), run on the workstation, never on the VM: the private key
# must not exist there. Decrypts a downloaded backup, restores it into a throwaway Postgres from
# the image pinned in docker-compose.yml, and prints what it contains.
#
#   deploy/restore-drill.sh <backup.dump.age> <age identity file>
#
# Keep the encrypted file afterwards: it is the copy of the data outside Oracle.
set -euo pipefail
# shellcheck source=deploy/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

WORK=""  # global: the EXIT trap runs after main has returned

main() {
  local backup=${1:-} identity=${2:-} image
  [[ -f $backup && -f $identity ]] || die "usage: $0 <backup.dump.age> <age identity file>"
  require_cmd age docker
  image=$(grep -oE 'pgvector/pgvector:[^[:space:]]+' "$REPO_ROOT/docker-compose.yml" | head -n 1)
  [[ -n $image ]] || die "no pgvector image in docker-compose.yml"

  WORK=$(mktemp -d)
  chmod 700 "$WORK"
  trap '[[ -z $WORK ]] || rm -rf "$WORK"' EXIT
  age -d -i "$identity" -o "$WORK/backup.dump" "$backup"
  log "decrypted $(basename "$backup"); restoring into $image"
  restore_check "$WORK/backup.dump" "$image"
  log "restore drill passed"
}

main "$@"
