"""Deployment guards (ADR-0007): compose rules the production host relies on.

Docker-published ports skip the host firewall's INPUT chain, so the loopback bindings below are
the real control, not iptables. deploy/deploy.sh also relies on every app service running the
`dzdia-newsroom:local` image, which it retags at each deployment and rollback.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
DEPLOY_SCRIPTS = sorted((ROOT / "deploy").glob("*.sh"))

PUBLIC_SERVICE = "caddy"
APP_SERVICES = {"migrate", "api", "worker", "telegram-cli"}


def _services() -> dict[str, list[str]]:
    """Service name -> its block of lines (top-level `services:` entries only)."""
    blocks: dict[str, list[str]] = {}
    section = ""
    service = ""
    for line in COMPOSE.splitlines():
        if match := re.match(r"^(\S[^:]*):", line):
            section, service = match.group(1), ""
        elif section == "services" and (match := re.match(r"^  ([\w-]+):\s*$", line)):
            service = match.group(1)
            blocks[service] = []
        elif service:
            blocks[service].append(line)
    return blocks


def _published_ports(block: list[str]) -> list[str]:
    ports: list[str] = []
    in_ports = False
    for line in block:
        if re.match(r"^    ports:\s*$", line):
            in_ports = True
        elif in_ports and (match := re.match(r'^      - "?([^"\s]+)"?', line)):
            ports.append(match.group(1))
        elif re.match(r"^    \S", line):
            in_ports = False
    return ports


def test_parser_sees_the_known_services() -> None:
    services = _services()
    assert {"postgres", "api", "worker", PUBLIC_SERVICE} <= services.keys()
    assert _published_ports(services["api"]) == ["127.0.0.1:${API_PORT:-8000}:8000"]


def test_only_caddy_publishes_ports_outside_loopback() -> None:
    for name, block in _services().items():
        for port in _published_ports(block):
            if name == PUBLIC_SERVICE:
                continue
            assert port.startswith("127.0.0.1:"), (
                f"{name} publishes {port!r} on every interface: Docker bypasses the host "
                "firewall, so bind it to 127.0.0.1 (ADR-0007)"
            )


def test_caddy_publishes_only_http_and_https() -> None:
    assert sorted(_published_ports(_services()[PUBLIC_SERVICE])) == ["443:443", "80:80"]


def test_app_services_run_the_image_deploy_retags() -> None:
    services = _services()
    for name in APP_SERVICES:
        # The `x-app` anchor provides the image; a service must not override it.
        assert any("<<: *app" in line for line in services[name]), name
        assert not any(re.match(r"^    image:", line) for line in services[name]), name
    assert re.search(r"^x-app: &app\n(?:  .*\n)*  image: dzdia-newsroom:local$", COMPOSE, re.M)


def test_deploy_scripts_are_strict_and_parsed_before_running() -> None:
    assert DEPLOY_SCRIPTS
    for script in DEPLOY_SCRIPTS:
        text = script.read_text(encoding="utf-8")
        assert text.startswith("#!/usr/bin/env bash\n"), script.name
        assert "set -euo pipefail" in text, script.name
        assert "\r\n" not in text, f"{script.name}: CRLF line endings break bash"
        if script.name != "lib.sh":
            # bash reads a script while running it; `git checkout` in deploy.sh rewrites the file.
            # Calling `main` on the last line makes bash parse the whole file first.
            assert text.rstrip().endswith('main "$@"'), script.name
