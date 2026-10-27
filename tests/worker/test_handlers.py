from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from dzdia_newsroom.channels.base import (
    INBOUND_MESSAGE_KIND,
    ChannelError,
    PermanentChannelError,
)
from dzdia_newsroom.channels.telegram import parse_update
from dzdia_newsroom.db.models import ClaimedJob
from dzdia_newsroom.worker.handlers import ACK_TEXT, build_handlers
from dzdia_newsroom.worker.queue import PermanentJobError
from tests.telegram_updates import make_update


@dataclass
class FakeChannel:
    sent: list[tuple[str, str, str | None]] = field(default_factory=list)
    error: Exception | None = None

    async def send_text(self, chat_id: str, text: str, *, reply_to: str | None = None) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append((chat_id, text, reply_to))


def inbound_job(payload: object = None) -> ClaimedJob:
    if payload is None:
        message = parse_update(make_update(user_id=111))
        payload = message.model_dump(mode="json")  # type: ignore[union-attr]
    return ClaimedJob.model_validate(
        {
            "id": 1,
            "kind": INBOUND_MESSAGE_KIND,
            "payload": payload,
            "attempts": 1,
            "max_attempts": 3,
            "claim_token": uuid4(),
        }
    )


async def test_inbound_message_is_acknowledged_in_its_chat() -> None:
    channel = FakeChannel()
    await build_handlers(channel)[INBOUND_MESSAGE_KIND](inbound_job())
    assert channel.sent == [("111", ACK_TEXT, "42")]


async def test_invalid_payload_is_permanent_and_does_not_leak_values() -> None:
    handler = build_handlers(FakeChannel())[INBOUND_MESSAGE_KIND]
    with pytest.raises(PermanentJobError) as raised:
        await handler(inbound_job({"text": "texte confidentiel"}))
    assert "confidentiel" not in str(raised.value)


async def test_rejected_send_is_permanent() -> None:
    channel = FakeChannel(error=PermanentChannelError("sendMessage: rejected (403)"))
    with pytest.raises(PermanentJobError, match="rejected"):
        await build_handlers(channel)[INBOUND_MESSAGE_KIND](inbound_job())


async def test_transient_send_failure_is_left_to_the_queue_retries() -> None:
    channel = FakeChannel(error=ChannelError("sendMessage: server error 502"))
    with pytest.raises(ChannelError) as raised:
        await build_handlers(channel)[INBOUND_MESSAGE_KIND](inbound_job())
    assert not isinstance(raised.value, PermanentJobError)
