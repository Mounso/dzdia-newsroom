"""Supply-chain guard (S1): every third-party reference is pinned, and copies stay in sync.

Dependabot cannot update some references (the CI service image, the uv version given to
setup-uv), so these tests turn silent drift into a failing check with an explicit message.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
WORKFLOWS = {
    p.name: p.read_text(encoding="utf-8") for p in (ROOT / ".github/workflows").glob("*.yml")
}
CI = WORKFLOWS["ci.yml"]

PINNED_IMAGE = re.compile(r"^[\w.\-/]+:[\w.\-]+@sha256:[0-9a-f]{64}$")
LOCAL_IMAGES = {"dzdia-newsroom:local"}


def _dockerfile_images() -> list[str]:
    syntax = re.findall(r"^# syntax=(\S+)", DOCKERFILE, re.MULTILINE)
    from_lines = re.findall(r"^FROM\s+(\S+)", DOCKERFILE, re.MULTILINE)
    return syntax + from_lines


def _compose_images() -> dict[str, str]:
    """Service name -> image, for services that declare one."""
    images: dict[str, str] = {}
    service = ""
    for line in COMPOSE.splitlines():
        if match := re.match(r"^  ([\w-]+):\s*$", line):
            service = match.group(1)
        elif match := re.match(r"^\s+image:\s*(\S+)", line):
            images[service] = match.group(1)
    return images


def _workflow_images() -> list[str]:
    return [
        image
        for text in WORKFLOWS.values()
        for image in re.findall(r"(?:^[ \t]+image:[ \t]*|image=)(\S+)", text, re.MULTILINE)
    ]


@pytest.mark.parametrize(
    "image",
    _dockerfile_images()
    + [i for i in _compose_images().values() if i not in LOCAL_IMAGES]
    + _workflow_images(),
)
def test_every_image_is_pinned_by_tag_and_digest(image: str) -> None:
    assert PINNED_IMAGE.match(image), f"pin {image!r} as name:tag@sha256:<index digest>"


def test_every_action_is_pinned_by_commit_sha() -> None:
    uses = [
        (name, line.strip())
        for name, text in WORKFLOWS.items()
        for line in text.splitlines()
        if re.match(r"^\s*(?:-\s+)?uses:", line)
    ]
    assert uses
    for name, line in uses:
        assert re.search(r"uses:\s*[\w.\-]+/[\w.\-/]+@[0-9a-f]{40}\s+#\s*v\d", line), (
            f"{name}: pin by full commit SHA with the version as a comment: {line}"
        )


def test_ci_postgres_matches_compose() -> None:
    ci_postgres = [i for i in _workflow_images() if i.startswith("pgvector/")]
    assert ci_postgres == [_compose_images()["postgres"]], (
        "update the postgres service image in ci.yml to match docker-compose.yml"
    )


def test_postgres_major_and_debian_release_are_deliberate() -> None:
    # A major upgrade needs a dump and restore; a new glibc can change collations. Change this
    # test together with the migration procedure, never through a dependency bump.
    assert "-pg16-bookworm@" in _compose_images()["postgres"]


def test_both_dockerfile_stages_use_the_same_python_image() -> None:
    pythons = [i for i in _dockerfile_images() if i.startswith("python:")]
    assert len(pythons) == 2 and pythons[0] == pythons[1]
    assert pythons[0].startswith("python:3.12."), "Python 3.12 is the supported runtime"


def test_ci_uv_version_matches_the_dockerfile() -> None:
    (uv_image,) = [i for i in _dockerfile_images() if i.startswith("ghcr.io/astral-sh/uv:")]
    dockerfile_uv = uv_image.split(":")[1].split("@")[0]
    ci_uv = re.findall(r'^\s+version:\s*"([^"]+)"', CI, re.MULTILINE)
    assert ci_uv and set(ci_uv) == {dockerfile_uv}, (
        f"set every setup-uv `version:` in ci.yml to {dockerfile_uv} (the Dockerfile's uv)"
    )
