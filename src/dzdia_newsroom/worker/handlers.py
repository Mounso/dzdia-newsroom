"""Job handlers, keyed by job kind.

Week 1 (ADR-004): `inbound_message` only acknowledges the message, to prove the chain
webhook -> queue -> worker -> reply. A duplicate acknowledgement (at-least-once delivery) is
tolerated.

Handlers translate channel errors into the queue's terms: `PermanentChannelError` becomes
`PermanentJobError`, and a `ChannelError` with a `retry_after_s` becomes `RetryLaterJobError`.
"""

from pydantic import ValidationError

from dzdia_newsroom.channels.base import (
    INBOUND_MESSAGE_KIND,
    Channel,
    ChannelError,
    InboundMessage,
    PermanentChannelError,
)
from dzdia_newsroom.db.models import ClaimedJob
from dzdia_newsroom.worker.queue import Handler, PermanentJobError, RetryLaterJobError

ACK_TEXT = "Bien reçu."


def build_handlers(channel: Channel) -> dict[str, Handler]:
    async def handle_inbound_message(job: ClaimedJob) -> None:
        try:
            message = InboundMessage.model_validate(job.payload)
        except ValidationError as exc:
            # Only the error count: pydantic's message would copy payload values (user text)
            # into `jobs.last_error`.
            raise PermanentJobError(f"invalid payload ({exc.error_count()} errors)") from None
        try:
            await channel.send_text(message.chat_id, ACK_TEXT, reply_to=message.message_id)
        except PermanentChannelError as exc:
            raise PermanentJobError(str(exc)) from exc
        except ChannelError as exc:
            if exc.retry_after_s is None:
                raise  # the queue's own backoff applies
            raise RetryLaterJobError(str(exc), delay_s=exc.retry_after_s) from exc

    return {INBOUND_MESSAGE_KIND: handle_inbound_message}
