"""Builders for Telegram webhook bodies, shared by the channel, API and integration tests."""

from typing import Any

TEAM_CHAT_ID = -100123
ALLOWED_USER_ID = 111
OTHER_USER_ID = 999


def make_update(
    *,
    update_id: int = 1,
    user_id: int | None = ALLOWED_USER_ID,
    chat_id: int | None = None,
    chat_type: str = "private",
    text: str | None = "Bonjour",
    key: str = "message",
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": 42,
        "date": 1_761_552_000,  # 2025-10-27T08:00:00Z
        "chat": {"id": chat_id if chat_id is not None else user_id, "type": chat_type},
    }
    if user_id is not None:
        message["from"] = {"id": user_id, "is_bot": False, "first_name": "Amina"}
    if text is not None:
        message["text"] = text
    return {"update_id": update_id, key: message}


def team_update(**kwargs: Any) -> dict[str, Any]:
    return make_update(chat_id=TEAM_CHAT_ID, chat_type="supergroup", **kwargs)
