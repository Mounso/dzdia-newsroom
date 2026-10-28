#!/usr/bin/env bash
# Provision the production VM (ADR-0007): packages, Docker, users, sshd, firewall, automatic
# security updates and the backup timer. Idempotent: every step checks the current state first,
# so running it again only applies what changed.
#
#   sudo deploy/bootstrap.sh [--dry-run]
#
# Run as root (through sudo) from Oracle's default `ubuntu` user, on Ubuntu 24.04 (aarch64).
set -euo pipefail
# shellcheck source=deploy/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ADMIN_USER=ubuntu
DEPLOY_USER=deploy
DEPLOY_HOME=/home/$DEPLOY_USER
CHECKOUT=$DEPLOY_HOME/dzdia-newsroom
REPO_URL=https://github.com/Mounso/dzdia-newsroom.git
# Docker's apt signing key, as published on docs.docker.com/engine/install/ubuntu.
DOCKER_KEY_FINGERPRINT=9DC858229FC7DD38854AE2D88D81803C0EBFCD88
RULES_V4=/etc/iptables/rules.v4

# install_file DEST MODE CONTENT: write DEST only if its content differs. Returns 0 when the file
# changed (or would change, under --dry-run), 1 when it was already up to date.
install_file() {
  local dest=$1 mode=$2 content=$3
  if [[ -f $dest ]] && [[ $(<"$dest") == "$content" ]]; then
    return 1
  fi
  if [[ $DRY_RUN == 1 ]]; then
    printf '[dry-run] write %s:\n%s\n' "$dest" "$content" >&2
  else
    install -D -m "$mode" /dev/null "$dest"
    printf '%s\n' "$content" >"$dest"
  fi
  log "updated $dest"
}

check_host() {
  [[ $EUID -eq 0 ]] || die "run as root: sudo $0"
  # shellcheck source=/dev/null
  source /etc/os-release
  [[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 24.04 ]] || die "expected Ubuntu 24.04, got ${PRETTY_NAME:-unknown}"
  [[ $(uname -m) == aarch64 ]] || log "warning: $(uname -m) host; production is aarch64 (Ampere A1)"
  id "$ADMIN_USER" >/dev/null 2>&1 || die "user $ADMIN_USER not found (expected on Oracle's Ubuntu image)"
}

set_timezone() {
  [[ $(timedatectl show -p Timezone --value) == Etc/UTC ]] || run timedatectl set-timezone Etc/UTC
}

install_packages() {
  local packages=(ca-certificates curl git gnupg age jq iptables-persistent unattended-upgrades)
  local missing=() pkg
  for pkg in "${packages[@]}"; do
    dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed" || missing+=("$pkg")
  done
  ((${#missing[@]})) || return 0
  # iptables-persistent must not save the current rules: Docker's chains would become permanent.
  run debconf-set-selections <<<"iptables-persistent iptables-persistent/autosave_v4 boolean false
iptables-persistent iptables-persistent/autosave_v6 boolean false"
  run apt-get update -q
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends "${missing[@]}"
}

install_docker() {
  local key=/etc/apt/keyrings/docker.asc tmp fingerprint arch
  if [[ ! -f $key ]]; then
    tmp=$(mktemp)
    curl -fsSL --max-time 30 --retry 3 https://download.docker.com/linux/ubuntu/gpg -o "$tmp"
    fingerprint=$(gpg --show-keys --with-colons "$tmp" | awk -F: '$1 == "fpr" {print $10; exit}')
    [[ $fingerprint == "$DOCKER_KEY_FINGERPRINT" ]] || die "Docker apt key fingerprint mismatch: $fingerprint"
    run install -D -m 0644 "$tmp" "$key"
    rm -f "$tmp"
  fi
  arch=$(dpkg --print-architecture)
  if install_file /etc/apt/sources.list.d/docker.sources 0644 "Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $VERSION_CODENAME
Components: stable
Architectures: $arch
Signed-By: $key"; then
    run apt-get update -q
  fi
  if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y -q \
      docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
  # live-restore: containers keep running while the daemon restarts after an upgrade.
  if install_file /etc/docker/daemon.json 0644 '{
  "live-restore": true,
  "log-driver": "json-file",
  "log-opts": {"max-size": "10m", "max-file": "5"}
}'; then
    run systemctl enable docker
    run systemctl restart docker
  fi
  systemctl is-enabled --quiet docker || run systemctl enable --now docker
}

create_deploy_user() {
  if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
    run useradd --create-home --shell /bin/bash "$DEPLOY_USER"
  fi
  # Membership of `docker` is root-equivalent: the split with `ubuntu` guards against mistakes,
  # not against an attacker who gets this account (ADR-0007).
  id -nG "$DEPLOY_USER" 2>/dev/null | grep -qw docker || run usermod -aG docker "$DEPLOY_USER"
  if [[ ! -s $DEPLOY_HOME/.ssh/authorized_keys ]]; then
    run install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$DEPLOY_HOME/.ssh"
    run install -m 0600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" \
      "/home/$ADMIN_USER/.ssh/authorized_keys" "$DEPLOY_HOME/.ssh/authorized_keys"
  fi
}

harden_ssh() {
  local user file
  for user in "$ADMIN_USER" "$DEPLOY_USER"; do
    file=/home/$user/.ssh/authorized_keys
    # Under --dry-run the deploy user's copy may not exist yet; it would be the admin's file.
    [[ -f $file ]] || file=/home/$ADMIN_USER/.ssh/authorized_keys
    grep -q 'ssh-ed25519 AAAA' "$file" 2>/dev/null ||
      die "no ed25519 key in $file: add one before restricting sshd, or you will be locked out"
  done
  # sshd keeps the first value it reads for each option, and this file sorts before Ubuntu's
  # 50-cloud-init.conf, so these settings win.
  if install_file /etc/ssh/sshd_config.d/10-dzdia.conf 0644 "# Managed by deploy/bootstrap.sh (ADR-0007).
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AuthenticationMethods publickey
PubkeyAcceptedAlgorithms ssh-ed25519,sk-ssh-ed25519@openssh.com
AllowUsers $ADMIN_USER $DEPLOY_USER
X11Forwarding no"; then
    [[ $DRY_RUN == 1 ]] || sshd -t || die "sshd rejected the new configuration; fix it before logging out"
    run systemctl reload-or-restart ssh
    log "sshd restricted: test a new SSH session as $DEPLOY_USER before closing this one"
  fi
}

# Oracle's image ships iptables rules ending with a REJECT, plus rules protecting the boot volume
# that must stay. UFW is advised against on these images. Insert live rules after the SSH rule,
# and edit rules.v4 for the next boot. Never iptables-restore (it would flush Docker's chains) or
# netfilter-persistent save (it would persist them).
open_firewall() {
  local port pos rule
  [[ -f $RULES_V4 ]] || die "$RULES_V4 not found: not Oracle's Ubuntu image?"
  [[ -f $RULES_V4.orig ]] || run cp -p "$RULES_V4" "$RULES_V4.orig"
  for port in 80 443; do
    rule=(-p tcp -m state --state NEW -m tcp --dport "$port" -j ACCEPT)
    if ! iptables -C INPUT "${rule[@]}" 2>/dev/null; then
      pos=$(iptables -L INPUT --line-numbers -n | awk '$2 == "ACCEPT" && / dpt:22( |$)/ {print $1; exit}')
      [[ -n $pos ]] || die "no SSH ACCEPT rule in INPUT: open port $port by hand (see the runbook)"
      run iptables -I INPUT $((pos + 1)) "${rule[@]}"
    fi
    if ! grep -qF -- "--dport $port -j ACCEPT" "$RULES_V4"; then
      grep -qF -- "--dport 22 -j ACCEPT" "$RULES_V4" || die "no SSH rule in $RULES_V4"
      run sed -i "/--dport 22 -j ACCEPT/a -A INPUT ${rule[*]}" "$RULES_V4"
    fi
  done
}

configure_updates() {
  install_file /etc/apt/apt.conf.d/20auto-upgrades 0644 'APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";' || true
  # Ubuntu's 50unattended-upgrades already allows the security pocket; lists add up across files.
  # shellcheck disable=SC2016  # ${distro_codename} is expanded by apt, not by the shell
  install_file /etc/apt/apt.conf.d/52dzdia-unattended-upgrades 0644 '// Managed by deploy/bootstrap.sh (ADR-0007).
Unattended-Upgrade::Origins-Pattern {
        "origin=Docker,codename=${distro_codename},label=Docker CE";
};
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:00";' || true
}

clone_checkout() {
  [[ -d $CHECKOUT/.git ]] && return 0
  run sudo -u "$DEPLOY_USER" git clone --quiet "$REPO_URL" "$CHECKOUT"
}

install_units() {
  local unit changed=0 source_dir
  source_dir=$(dirname "${BASH_SOURCE[0]}")/systemd
  for unit in "$source_dir"/*; do
    install_file "/etc/systemd/system/$(basename "$unit")" 0644 "$(<"$unit")" && changed=1
  done
  ((changed)) && run systemctl daemon-reload
  systemctl is-enabled --quiet dzdia-backup.timer 2>/dev/null || run systemctl enable --now dzdia-backup.timer
}

main() {
  case ${1:-} in
    --dry-run) DRY_RUN=1 ;;
    "") ;;
    *) die "usage: sudo $0 [--dry-run]" ;;
  esac
  check_host
  set_timezone
  install_packages
  install_docker
  create_deploy_user
  harden_ssh
  open_firewall
  configure_updates
  clone_checkout
  install_units
  log "bootstrap done. Next, as $DEPLOY_USER: create $CHECKOUT/.env, then deploy/deploy.sh origin/main"
}

main "$@"
