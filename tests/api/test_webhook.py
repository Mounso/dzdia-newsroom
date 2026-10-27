from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import JsonValue

from dzdia_newsroom.api.app import create_app
from dzdia_newsroom.api.webhook import (
    SECRET_HEADER,
    WEBHOOK_PATH,
    AllowPolicy,
    WebhookConfig,
    is_allowed,
    verify_secret,
)
from dzdia_newsroom.channels.base import INBOUND_MESSAGE_KIND, InboundMessage
from dzdia_newsroom.channels.telegram import parse_update
from tests.telegram_updates import (
    ALLOWED_USER_ID,
    OTHER_USER_ID,
    TEAM_CHAT_ID,
    make_update,
    team_update,
)

SECRET = "s" * 40
POLICY = AllowPolicy(user_ids=frozenset({str(ALLOWED_USER_ID)}), team_chat_id=str(TEAM_CHAT_ID))


@dataclass
class FakeJobs:
    """Mimics `JobRepository.enqueue`, including `ON CONFLICT (dedup_key) DO NOTHING`."""

    jobs: list[tuple[str, dict[str, JsonValue], str | None]] = field(default_factory=list)
    fail: bool = False

    async def enqueue(
        self,
        kind: str,
        payload: dict[str, JsonValue],
        *,
        dedup_key: str | None = None,
        max_attempts: int = 3,
    ) -> int | None:
        if self.fail:
            raise ConnectionError("database unreachable")
        if dedup_key is not None and any(key == dedup_key for _, _, key in self.jobs):
            return None
        self.jobs.append((kind, payload, dedup_key))
        return len(self.jobs)


@pytest.fixture
def jobs() -> FakeJobs:
    return FakeJobs()


@pytest.fixture
def client(jobs: FakeJobs) -> Iterator[TestClient]:
    app = create_app(WebhookConfig(secret=SECRET, policy=POLICY), lambda: nullcontext(jobs))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def post(client: TestClient, body: Any, secret: str | None = SECRET) -> int:
    headers = {SECRET_HEADER: secret} if secret is not None else {}
    status: int = client.post(WEBHOOK_PATH, json=body, headers=headers).status_code
    return status


# --- pure functions ---


def test_verify_secret() -> None:
    assert verify_secret(SECRET, SECRET)
    assert not verify_secret(SECRET[:-1] + "x", SECRET)
    assert not verify_secret(None, SECRET)
    assert not verify_secret("é" * 40, SECRET)  # non-ASCII header must not crash


def message(update: dict[str, Any]) -> InboundMessage:
    parsed = parse_update(update)
    assert isinstance(parsed, InboundMessage)
    return parsed


@pytest.mark.parametrize(
    ("update", "allowed"),
    [
        (make_update(), True),  # listed user, private chat
        (team_update(), True),  # listed user, team group
        (make_update(user_id=OTHER_USER_ID), False),  # unlisted user, private chat
        (team_update(user_id=OTHER_USER_ID), False),  # unlisted user, even in the team group
        (make_update(chat_id=-100777, chat_type="group"), False),  # listed user, other group
    ],
)
def test_is_allowed(update: dict[str, Any], allowed: bool) -> None:
    assert is_allowed(message(update), POLICY) is allowed


def test_empty_allowlist_refuses_everyone() -> None:
    assert not is_allowed(message(make_update()), AllowPolicy(user_ids=frozenset()))


def test_no_team_chat_refuses_all_groups() -> None:
    policy = AllowPolicy(user_ids=POLICY.user_ids, team_chat_id=None)
    assert not is_allowed(message(team_update()), policy)


# --- HTTP route ---


def test_allowed_message_is_queued_with_its_dedup_key(client: TestClient, jobs: FakeJobs) -> None:
    update = make_update(update_id=77, text="Vérifie cette info")
    assert post(client, update) == 200

    [(kind, payload, dedup_key)] = jobs.jobs
    assert kind == INBOUND_MESSAGE_KIND
    assert dedup_key == "telegram:77"
    queued = InboundMessage.model_validate(payload)
    assert queued.text == "Vérifie cette info"
    assert queued.raw == update


def test_redelivered_update_is_queued_once(client: TestClient, jobs: FakeJobs) -> None:
    assert post(client, make_update(update_id=5)) == 200
    assert post(client, make_update(update_id=5)) == 200
    assert len(jobs.jobs) == 1


@pytest.mark.parametrize("secret", [None, "wrong-secret-" + "x" * 30])
def test_wrong_secret_is_unauthorized(
    client: TestClient, jobs: FakeJobs, secret: str | None
) -> None:
    assert post(client, make_update(), secret=secret) == 401
    assert jobs.jobs == []


@pytest.mark.parametrize(
    "body",
    [
        make_update(user_id=OTHER_USER_ID),  # refused
        make_update(key="edited_message"),  # ignored
        {"not": "an update"},  # malformed
    ],
)
def test_refused_or_ignored_updates_get_200_and_no_job(
    client: TestClient, jobs: FakeJobs, body: dict[str, Any]
) -> None:
    assert post(client, body) == 200
    assert jobs.jobs == []


def test_invalid_json_gets_200(client: TestClient, jobs: FakeJobs) -> None:
    response = client.post(WEBHOOK_PATH, content=b"{not json", headers={SECRET_HEADER: SECRET})
    assert response.status_code == 200
    assert jobs.jobs == []


def test_enqueue_failure_returns_500_so_telegram_retries(
    client: TestClient, jobs: FakeJobs
) -> None:
    jobs.fail = True
    assert post(client, make_update()) == 500


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_docs_are_not_exposed(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
