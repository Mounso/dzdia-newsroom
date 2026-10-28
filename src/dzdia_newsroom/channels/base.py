"""Channel abstraction (ADR-004, ADR-005): adapters turn platform events into `InboundMessage`
and send text back; the worker never sees a platform-specific format."""

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, JsonValue

# Job kind whose payload is a serialized `InboundMessage`.
INBOUND_MESSAGE_KIND = "inbound_message"


class InboundMessage(BaseModel):
    """A text message from a user. Ids are strings so no platform format is assumed."""

    model_config = ConfigDict(frozen=True)

    channel: Literal["telegram"]
    event_id: str  # platform delivery id (Telegram `update_id`), used as the dedup key
    chat_id: str
    user_id: str
    message_id: str
    is_private: bool  # one-to-one chat with the bot, as opposed to a group
    text: str
    sent_at: datetime
    raw: dict[str, JsonValue]  # the original event, for debugging and replay only

    @property
    def dedup_key(self) -> str:
        return f"{self.channel}:{self.event_id}"


class ChannelError(Exception):
    """Sending failed but may succeed later (network error, rate limit, server error).

    `retry_after_s` is the minimum wait the platform asked for (rate limit), when it gave one.
    """

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class PermanentChannelError(ChannelError):
    """The platform rejected the request; retrying cannot help (chat not found, bot blocked)."""


class Channel(Protocol):
    async def send_text(self, chat_id: str, text: str, *, reply_to: str | None = None) -> None:
        """Send plain text, never interpreted as markup. Raises `ChannelError`."""
        ...
