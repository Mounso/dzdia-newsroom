"""`POST /webhooks/telegram` (ADR-004, ADR-005): check the secret, normalize the update,
apply the allowlist, enqueue. Nothing else: the worker does the actual work.

Status codes: 401 for a wrong secret (not Telegram, so no redelivery to fear); 200 for every
update we refuse or ignore (so Telegram does not resend it); 500 if the enqueue fails (Telegram
resends, and the dedup key makes the retry safe).
"""

import hmac
import logging
from dataclasses import dataclass, field
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import JsonValue

from dzdia_newsroom.channels.base import INBOUND_MESSAGE_KIND, InboundMessage
from dzdia_newsroom.channels.telegram import Ignored, parse_update

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhooks/telegram"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


class JobEnqueuer(Protocol):
    """What the API needs from the queue; implemented by `db.repository.JobRepository`."""

    async def enqueue(
        self,
        kind: str,
        payload: dict[str, JsonValue],
        *,
        dedup_key: str | None = None,
        max_attempts: int = 3,
    ) -> int | None: ...


@dataclass(frozen=True)
class AllowPolicy:
    user_ids: frozenset[str]
    team_chat_id: str | None = None


@dataclass(frozen=True)
class WebhookConfig:
    secret: str = field(repr=False)
    policy: AllowPolicy


def verify_secret(received: str | None, expected: str) -> bool:
    """Constant-time comparison, so response timing reveals nothing about the secret."""
    return received is not None and hmac.compare_digest(received.encode(), expected.encode())


def is_allowed(message: InboundMessage, policy: AllowPolicy) -> bool:
    """The sender must be on the allowlist everywhere, and groups other than the team group
    are refused even for listed users (ADR-005). An empty allowlist refuses everyone."""
    if message.user_id not in policy.user_ids:
        return False
    return message.is_private or message.chat_id == policy.team_chat_id


def get_config(request: Request) -> WebhookConfig:
    config: WebhookConfig = request.app.state.config
    return config


def get_jobs(request: Request) -> JobEnqueuer:
    jobs: JobEnqueuer = request.app.state.jobs
    return jobs


router = APIRouter()


@router.post(WEBHOOK_PATH)
async def telegram_webhook(
    request: Request,
    config: Annotated[WebhookConfig, Depends(get_config)],
    jobs: Annotated[JobEnqueuer, Depends(get_jobs)],
    secret: Annotated[str | None, Header(alias=SECRET_HEADER)] = None,
) -> dict[str, bool]:
    if not verify_secret(secret, config.secret):
        logger.warning("webhook call with a wrong secret")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    try:
        data = await request.json()
    except ValueError:
        data = None
    parsed = parse_update(data)
    if isinstance(parsed, Ignored):
        logger.info("update ignored: %s", parsed.reason, extra={"update_id": parsed.event_id})
        return {"ok": True}

    context = {"update_id": parsed.event_id, "user_id": parsed.user_id, "chat_id": parsed.chat_id}
    if not is_allowed(parsed, config.policy):
        logger.warning("update refused: sender or chat not allowed", extra=context)
        return {"ok": True}

    job_id = await jobs.enqueue(
        INBOUND_MESSAGE_KIND, parsed.model_dump(mode="json"), dedup_key=parsed.dedup_key
    )
    if job_id is None:
        logger.info("update already queued", extra=context)
    else:
        logger.info("update queued", extra={**context, "job_id": job_id})
    return {"ok": True}
