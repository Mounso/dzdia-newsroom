from datetime import UTC, datetime

import pytest

from dzdia_newsroom.channels.base import InboundMessage
from dzdia_newsroom.channels.telegram import Ignored, parse_update
from tests.telegram_updates import TEAM_CHAT_ID, make_update, team_update


def test_private_text_message_is_normalized() -> None:
    update = make_update(update_id=7, user_id=111, text="Résume cet article")
    message = parse_update(update)
    assert message == InboundMessage(
        channel="telegram",
        event_id="7",
        chat_id="111",
        user_id="111",
        message_id="42",
        is_private=True,
        text="Résume cet article",
        sent_at=datetime(2025, 10, 27, 8, 0, tzinfo=UTC),
        raw=update,
    )
    assert message.dedup_key == "telegram:7"


def test_group_message_is_not_private() -> None:
    message = parse_update(team_update())
    assert isinstance(message, InboundMessage)
    assert message.chat_id == str(TEAM_CHAT_ID)
    assert not message.is_private


@pytest.mark.parametrize(
    ("update", "reason"),
    [
        (make_update(key="edited_message"), "not a new message"),
        (make_update(text=None), "message without text"),
        (make_update(user_id=None, chat_id=-100555, chat_type="channel"), "message without author"),
    ],
)
def test_unsupported_updates_are_ignored_with_their_id(update: object, reason: str) -> None:
    assert parse_update(update) == Ignored(reason, event_id="1")


@pytest.mark.parametrize("body", [None, [], "text", {"message": {}}, {"update_id": "x"}])
def test_malformed_bodies_are_ignored(body: object) -> None:
    assert parse_update(body) == Ignored("malformed update")
