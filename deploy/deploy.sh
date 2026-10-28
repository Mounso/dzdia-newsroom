#!/usr/bin/env bash
# Deploy a commit of origin/main on the production VM, or roll back (ADR-0007). Run as `deploy`
# from the checkout:
#
#   deploy/deploy.sh [--dry-run] <ref>        e.g. origin/main, a tag or a commit SHA
#   deploy/deploy.sh [--dry-run] --rollback   back to the previous successful deployment
#   deploy/deploy.sh --status                 current deployment and recent history
#
# Each deployment builds dzdia-newsroom:<sha> and points dzdia-newsroom:local (the image compose
# runs) at it; rollback re-points `local` without a rebuild. Migrations must follow the
# expand/contract rule (docs/development/conventions.md): rollback never reverts the schema.
set -euo pipefail
# shellcheck source=deploy/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

IMAGE=dzdia-newsroom
KEEP_IMAGES=3
STATE_DIR=${XDG_STATE_HOME:-$HOME/.local/state}/dzdia-newsroom
# One line per event: <UTC time> <deploy|rollback> <full sha> <ok|failed|reverted>, tab-separated.
HISTORY=$STATE_DIR/deployments.tsv
HEALTH_TIMEOUT_S=120

record() {
  [[ $DRY_RUN == 1 ]] && return 0
  mkdir -p "$STATE_DIR"
  printf '%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$3" >>"$HISTORY"
}

# The latest status of each sha decides: a reverted or failed sha is never "current" or a
# rollback target until it is deployed successfully again.
current_sha() {
  [[ -f $HISTORY ]] || return 0
  awk -F'\t' '{ st[$3] = $4; if ($4 == "ok") last[$3] = NR }
    END { for (s in last) if (st[s] == "ok" && last[s] > m) { m = last[s]; c = s }; print c }' "$HISTORY"
}

# Successful shas, most recent first, excluding the current one.
previous_shas() {
  [[ -f $HISTORY ]] || return 0
  awk -F'\t' -v cur="$1" '{ st[$3] = $4; if ($4 == "ok") last[$3] = NR }
    END { for (s in last) if (st[s] == "ok" && s != cur) print last[s], s }' "$HISTORY" |
    sort -rn | cut -d' ' -f2
}

short() { git rev-parse --short=12 "$1"; }

check_health() {
  local host=$1 deadline=$((SECONDS + HEALTH_TIMEOUT_S))
  until curl -fsS --max-time 5 -o /dev/null "https://$host/health"; do
    ((SECONDS < deadline)) || return 1
    sleep 3
  done
}

# Keep the images of the current and the last KEEP_IMAGES successful deployments.
prune_images() {
  local -A keep=()
  local sha tag
  for sha in $(current_sha) $(previous_shas "$(current_sha)" | head -n "$KEEP_IMAGES"); do
    keep[$(short "$sha")]=1
  done
  for tag in $(docker image ls "$IMAGE" --format '{{.Tag}}'); do
    [[ $tag == local || -n ${keep[$tag]:-} ]] && continue
    run docker image rm "$IMAGE:$tag" >/dev/null
    log "removed old image $IMAGE:$tag"
  done
}

# Start the stack on the checked-out code and the image tagged `local`, then check it through
# Caddy, the way Telegram reaches it.
start_and_check() {
  local action=$1 sha=$2 host=$3
  # A failed `migrate` makes `up` fail before api and worker are recreated: the old containers
  # keep serving, so the health check alone would wrongly pass.
  if ! run compose --profile prod up -d --remove-orphans; then
    record "$action" "$sha" failed
    die "docker compose up failed (see above, often migrate); deploy/deploy.sh --rollback restores the code"
  fi
  if [[ $DRY_RUN == 1 ]]; then
    log "dry run: would check https://$host/health"
    return 0
  fi
  if ! check_health "$host"; then
    record "$action" "$sha" failed
    compose ps >&2
    die "https://$host/health did not answer within ${HEALTH_TIMEOUT_S}s; inspect the logs, then deploy/deploy.sh --rollback"
  fi
  record "$action" "$sha" ok
  prune_images
  compose run --rm telegram-cli webhook-info || log "warning: webhook-info failed"
  log "$action of $(short "$sha") done: https://$host/health answers"
}

predeploy_backup() {
  if [[ -z $(compose ps -q postgres) ]]; then
    log "no running database (first deployment?): skipping the pre-deployment backup"
    return 0
  fi
  run "$REPO_ROOT/deploy/backup.sh" predeploy
}

preflight() {
  [[ $EUID -ne 0 ]] || die "run as the deploy user, not root"
  require_cmd git docker curl
  cd "$REPO_ROOT"
  [[ -z $(git status --porcelain) ]] || die "the checkout has local changes: git status"
  git fetch --quiet origin
}

deploy() {
  local ref=$1 host sha
  host=$(env_get PUBLIC_HOST) || die "set PUBLIC_HOST in .env"
  sha=$(git rev-parse --verify --quiet "$ref^{commit}") || die "unknown ref: $ref"
  git merge-base --is-ancestor "$sha" origin/main || die "$ref is not on origin/main"
  log "deploying $(short "$sha"): $(git log -1 --format=%s "$sha")"
  predeploy_backup
  run git checkout --quiet --detach "$sha"
  # Every app service shares the image: building one builds dzdia-newsroom:local for all.
  run compose build migrate
  run docker tag "$IMAGE:local" "$IMAGE:$(short "$sha")"
  start_and_check deploy "$sha" "$host"
}

rollback() {
  local host current target
  host=$(env_get PUBLIC_HOST) || die "set PUBLIC_HOST in .env"
  current=$(current_sha)
  target=$(previous_shas "$current" | head -n 1)
  [[ -n $target ]] || die "no earlier successful deployment to roll back to"
  docker image inspect "$IMAGE:$(short "$target")" >/dev/null 2>&1 ||
    die "image $IMAGE:$(short "$target") was pruned: deploy $target instead (it rebuilds)"
  log "rolling back from ${current:0:12} to $(short "$target")"
  predeploy_backup
  [[ -z $current ]] || record rollback "$current" reverted
  run git checkout --quiet --detach "$target"
  run docker tag "$IMAGE:$(short "$target")" "$IMAGE:local"
  start_and_check rollback "$target" "$host"
}

status() {
  local current
  cd "$REPO_ROOT"
  current=$(current_sha)
  echo "current: ${current:-none} ($(git log -1 --format='%h %s' "${current:-HEAD}" 2>/dev/null))"
  echo "checkout: $(git rev-parse HEAD)"
  [[ -f $HISTORY ]] && { echo "history:"; tail -n 10 "$HISTORY"; }
  compose --profile prod ps
}

main() {
  if [[ ${1:-} == --dry-run ]]; then
    DRY_RUN=1
    shift
  fi
  # Plain sequences, never `preflight && deploy`: bash ignores `set -e` inside a function called
  # on the left of `&&`.
  case ${1:-} in
    --status)
      status
      ;;
    --rollback)
      preflight
      rollback
      ;;
    "" | -*)
      die "usage: $0 [--dry-run] <ref> | [--dry-run] --rollback | --status"
      ;;
    *)
      preflight
      deploy "$1"
      ;;
  esac
}

# Last line on purpose: `git checkout` rewrites this file while it runs, and bash reads scripts
# as it goes. Calling main here makes bash parse the whole file before anything executes.
main "$@"
