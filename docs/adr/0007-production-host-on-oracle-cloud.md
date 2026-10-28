# ADR-0007: Production host on Oracle Cloud: access, firewall, deployment, backups and hostname

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Abdou (author), with Claude as reviewer
- **Related:** [ADR-0004](0004-runtime-foundation.md) (hosting and HTTPS choices this ADR implements), [ADR-0006](0006-docker-image-and-compose.md) (compose layout; backups were deferred to this ADR)

## Context

[ADR-0004](0004-runtime-foundation.md) puts the stack on an Oracle Cloud Ampere A1 VM (ARM64, Always Free), behind Caddy, with a DuckDNS name until a `dzdia.com` subdomain exists. [ADR-0006](0006-docker-image-and-compose.md) builds the image on the host, publishes only Caddy on public ports, and defers backups to the deployment step.

Constraints:

- **One VM, one maintainer**, no on-call. Procedures must be scripted, short and recoverable from the Oracle console.
- **Always Free.** Oracle may reclaim an Always Free instance it considers idle: over 7 days, the 95th percentile of CPU, network and (on A1) memory use all stay below 20 %. A bot that waits for a few messages per hour meets all three.
- **No registry** (ADR-0006): the image is built on the VM.
- **Data:** today only the `jobs` table (journalists' messages, raw Telegram updates). Week 2 adds articles, drafts and audit logs, which are harder to recreate.
- **Oracle's Ubuntu images** ship `iptables` rules (including rules that protect the iSCSI boot volume), and Oracle advises against UFW on them: enabling it can conflict with those rules and stop the instance from booting.

Still open before this ADR: host access, firewall, OS updates, the deployment and rollback procedure, backups, and the public hostname.

## Decision

1. **Host and account.**
   - Shape `VM.Standard.A1.Flex` within the Always Free limits, Canonical image **Ubuntu 24.04 LTS (aarch64)**. The exact size is an operational setting, recorded in the [deployment guide](../operations/deployment.md).
   - The Oracle account is **upgraded to Pay As You Go**, with a budget alert. Usage within the Always Free limits stays free, and the instance is no longer subject to idle reclamation.
   - The instance gets a **reserved public IPv4**, so the DNS record and the webhook URL never change when the instance is recreated.
   - Docker Engine and the compose plugin come from Docker's official apt repository.
2. **Access: public SSH, keys only.**
   - Port 22 is open to the Internet. `sshd` allows ed25519 keys only: `PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `PermitRootLogin no`, `AllowUsers ubuntu deploy`.
   - Two users: `ubuntu` (Oracle's default, sudo) administers the host; `deploy` (no sudo, member of the `docker` group) owns the checkout in `/home/deploy/dzdia-newsroom` and runs the stack.
   - Membership of the `docker` group is equivalent to root. The split protects against mistakes, not against an attacker who gets `deploy`.
   - Recovery path if SSH is lost: the serial console from Oracle Cloud Shell.
3. **Firewall: two layers, no UFW.**
   - **Cloud layer:** the VCN security list allows inbound TCP 22, 80 and 443 only (80 for the ACME HTTP challenge and the redirect to HTTPS), plus ICMP type 3 code 4 for path MTU discovery.
   - **Host layer:** Oracle's `iptables` rules are kept. Ports 80 and 443 are added in `/etc/iptables/rules.v4`, before the final `REJECT`, and loaded with `netfilter-persistent`. Oracle's rules for the boot volume are never removed.
   - **Docker-published ports skip the host's `INPUT` chain**, so the host firewall does not protect them. The real control is that only `caddy` publishes ports on all interfaces; `api` and `postgres` bind to `127.0.0.1`. A guard test fails if any other service publishes a port outside the loopback address.
4. **Automatic security updates.**
   - `unattended-upgrades` installs updates from the Ubuntu security pocket and from Docker's repository.
   - `Automatic-Reboot "true"` at 04:00 UTC when a package requires it. `docker.service` is enabled at boot, and the containers come back by themselves (`restart: unless-stopped`).
   - Docker runs with `live-restore`, so containers keep running while the daemon restarts after a patch upgrade. A larger upgrade or a reboot still restarts them: Telegram redelivers the webhook calls that fail meanwhile, and the queue's dedup key makes them safe.
5. **Deployment: a versioned script with checks and rollback.**
   - `deploy.sh <ref>`, run on the VM as `deploy`, in this order:
     1. refuse a dirty working tree, and a `<ref>` that is not an ancestor of `origin/main`;
     2. take a pre-deployment backup (point 6);
     3. check out `<ref>`, build the image, and tag it twice: `dzdia-newsroom:<short-sha>` (immutable, kept for rollback) and `dzdia-newsroom:local` (the image compose runs);
     4. start the stack (`migrate` runs first, as in ADR-0006);
     5. check `https://$PUBLIC_HOST/health` through Caddy, and print `webhook-info`;
     6. append the deployed SHA, date and result to a deployment log on the host.
   - `deploy.sh --rollback` checks out the previous successful SHA, points `dzdia-newsroom:local` back to its image and starts the stack, without a rebuild. The images of the last 3 successful deployments are kept.
   - Compose keeps `image: dzdia-newsroom:local`. On the VM this tag always means "the deployed image", so a manual `docker compose up -d <service>` from the runbook never rebuilds or runs another version.
   - **Migration rule (expand/contract):** every migration must keep working with the code of the previous release. A column is added before the code uses it, and removed only in a later release once no deployed code reads it. Rollback never reverts the schema.
   - `set-webhook` is not part of a deployment: it runs only when the hostname or the secret changes.
6. **Backups: encrypted daily dumps in Oracle Object Storage.**
   - A systemd timer runs `pg_dump -Fc` every day at 03:00 UTC (`Persistent=true`, so a missed run happens at boot), and `deploy.sh` runs it before each deployment.
   - The dump is encrypted with **`age`** before it leaves the VM. Only the public key (recipient) is on the VM. The private key is kept offline by Abdou (password manager and a paper copy): an attacker on the VM cannot read the backups.
   - The encrypted file is uploaded with `rclone` (a pinned image, run as the compose service `rclone` in the `ops` profile) to a private Object Storage bucket, under `daily/` (also `weekly/` on Sundays, `predeploy/` before deployments), with names that are never reused.
   - **No stored bucket credential, and it can only add objects.** The VM authenticates as an *instance principal*: a dynamic group that contains only this instance. Its policy allows inspecting, reading and creating objects in that bucket, and nothing else (no overwrite, no delete). A bucket retention rule also blocks deletion for 7 days. An attacker on the VM can add files, but cannot erase the history, and there is no key to steal.
   - **Retention** through lifecycle rules: `daily/` and `predeploy/` after 8 days, `weekly/` after 29 days. Seven daily and four weekly copies remain.
   - **Two restore tests:**
     - automatic, on the VM, at every backup: the plain dump is restored into a throwaway Postgres container (no network, data in memory) before encryption; the restore must succeed, and the restored database must have the same tables and applied migrations as the live one. Row counts are logged (they can differ, since the live database keeps changing). It proves the dump is usable;
     - manual, every month, from Abdou's workstation: download the latest copy, decrypt it with the private key, restore it into a throwaway container and check it. It proves the whole chain (upload, encryption, key), and leaves an encrypted copy outside Oracle. The procedure is in the [runbook](../operations/runbook.md).
   - **Dead man's switch:** each run pings a healthchecks.io check (start, success, failure). The service alerts Abdou when a run fails or does not happen. The ping URL is a secret in `.env`; without it, the script still runs.
   - **Targets:** at most 24 hours of data lost (RPO), and service back within about one hour on a new VM (RTO).
7. **Hostname and HTTPS.**
   - A DuckDNS name points to the reserved IP. The IP is static, so no DuckDNS updater runs on the VM.
   - Caddy obtains and renews the Let's Encrypt certificate for `PUBLIC_HOST` (unchanged from ADR-0006).
   - **A production bot**, separate from the development bot. Its token exists only in the VM's `.env`. Its first `set-webhook` uses `--drop-pending`.
   - Moving to `bot.dzdia.com`: add the DNS record, change `PUBLIC_HOST`, recreate `caddy`, then run `set-webhook` again (procedure in the runbook).
   - Restricting the webhook to Telegram's IP ranges (design review S5) is not done: the secret header is the control, and a change of ranges would silently cut the bot off.
8. **Host provisioning: an idempotent bash script, with the deployment scripts in `deploy/`.**
   - `deploy/bootstrap.sh`, run once as root (and again after any change), configures everything above: packages, Docker, users, `sshd`, `iptables`, `unattended-upgrades`, the timezone (UTC) and the systemd units in `deploy/systemd/`. Each step checks the current state before changing it, so a second run changes nothing.
   - Safety checks before risky steps: it refuses to restrict `sshd` unless an ed25519 key is authorized for both users, and it validates the configuration with `sshd -t` before reloading.
   - Firewall rules are inserted live with `iptables -I` and written to `/etc/iptables/rules.v4` by editing the file. Never `iptables-restore` or `netfilter-persistent save` on a running host: the first would flush Docker's chains, and the second would persist them.
   - The same folder holds `deploy.sh`, `backup.sh`, `restore-drill.sh` (run on Abdou's workstation) and a shared `lib.sh`. `bootstrap.sh` and `deploy.sh` accept `--dry-run`, which prints the changes without making them.
   - All scripts are checked with `shellcheck` in the CI. The real test is a run on the VM with Abdou.

## Alternatives considered

- **Manual deployment (`git pull` then `compose up --build`)**: no checks, no record of what runs, and rollback is improvised.
- **Image built in CI and pushed to GHCR, pulled by the VM**: the image that runs would be the image that was tested, but it needs a write token in the CI, a VM SSH key or a pull agent, and a registry to secure. Too much for one server; to reconsider if a second host appears.
- **`restic` for backups**: deduplication, encryption and retention built in, but the repository password lives on the VM, so whoever gets root can read and delete every backup.
- **Oracle boot volume backups**: crash-consistent only, no restore of a single table, and tied to the Oracle account.
- **SSH restricted to Abdou's IP**: a dynamic home IP would lock him out at every change.
- **Tailscale, with port 22 closed**: no public SSH at all, but a third-party account and daemon become necessary to reach our own VM.
- **UFW**: advised against by Oracle on its Ubuntu images, and it would not filter Docker-published ports anyway.
- **A domain of our own now (about 10 € a year)**: more reliable than DuckDNS, but it costs money and an account, while `bot.dzdia.com` is expected.
- **Waiting for `bot.dzdia.com`**: the deployment would depend on the Dzdia team.
- **A dedicated IAM user with S3-compatible keys for backups**: a long-lived secret on the VM. The instance principal gives the same rights without one.
- **cloud-init for provisioning**: it runs only at the first boot, so any later change means recreating the VM or doing it by hand, and failures are hard to debug.
- **Ansible**: the standard tool, idempotent by design, but one more tool and language for a single VM, while the deployment and backup logic stays in bash anyway.

## Consequences

- **The production `.env` is not in the backups.** Abdou keeps it in his password manager, with the `age` private key and a console password for `ubuntu` (only usable on the serial console, since SSH refuses passwords).
- **The VM can be rebuilt from the repository and the latest backup:** provisioning, `.env`, `deploy.sh`, then a restore. This is the one-hour RTO, and it has to be rehearsed once.
- **Pay As You Go needs a payment card on the Oracle account.** The budget alert is the guard against an accidental paid resource.
- **SSH stays exposed to scans.** Keys only and automatic security updates bound the risk (a pre-authentication OpenSSH flaw such as regreSSHion in 2024 is the residual one).
- **Shell scripts become production code:** they are checked with `shellcheck` in the CI, and the guard tests cover the compose rules they rely on.
- **Rollback depends on the expand/contract rule.** It is written in the [conventions](../development/conventions.md) and checked in review; a migration that breaks it makes rollback unsafe.
- **The VM, its backups and the bucket are with the same provider.** Losing the Oracle account loses all three; the monthly restore test leaves one encrypted copy on Abdou's workstation.
- **DuckDNS is a volunteer service.** If it is down when Caddy renews, the renewal is retried; a certificate is renewed about 30 days before it expires.
- **Abdou's actions** (on his accounts): Pay As You Go and budget alert, VM and reserved IP, security list, bucket and IAM user, healthchecks.io check, DuckDNS name, production bot. They are given to him as commands or console steps.
