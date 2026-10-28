"""Telegram adapter (ADR-004, ADR-005): parse webhook updates, call the Bot API over httpx.

Register the webhook at deploy time:
`python -m dzdia_newsroom.channels.telegram set-webhook --url https://<host>/webhooks/telegram`
"""

import argparse
import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from dzdia_newsroom.channels.base import ChannelError, InboundMessage, PermanentChannelError

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
ALLOWED_UPDATES = ["message"]  # week 1: plain text messages only (ADR-005)


# Minimal views of the Bot API objects; unknown fields are ignored.
class _User(BaseModel):
    id: int


class _Chat(BaseModel):
    id: int
    type: str  # private, group, supergroup or channel


class _Message(BaseModel):
    message_id: int
    date: int
    chat: _Chat
    from_: _User | None = Field(default=None, alias="from")
    text: str | None = None


class _Update(BaseModel):
    update_id: int
    message: _Message | None = None


@dataclass(frozen=True)
class Ignored:
    """An update we do not process. `reason` is safe to log: it never contains message text."""

    reason: str
    event_id: str | None = None


def parse_update(data: object) -> InboundMessage | Ignored:
    """Turn a webhook body into an `InboundMessage`, or say why it is ignored."""
    if not isinstance(data, dict):
        return Ignored("malformed update")
    try:
        update = _Update.model_validate(data)
    except ValidationError:
        return Ignored("malformed update")
    event_id = str(update.update_id)
    message = update.message
    if message is None:
        return Ignored("not a new message", event_id)
    if message.from_ is None:
        return Ignored("message without author", event_id)
    if message.text is None:
        return Ignored("message without text", event_id)
    return InboundMessage(
        channel="telegram",
        event_id=event_id,
        chat_id=str(message.chat.id),
        user_id=str(message.from_.id),
        message_id=str(message.message_id),
        is_private=message.chat.type == "private",
        text=message.text,
        sent_at=datetime.fromtimestamp(message.date, UTC),
        raw=data,
    )


class TelegramClient:
    """The few Bot API methods we use, with bounded retries.

    Short failures are retried here (network errors, 5xx, 429 with a short `retry_after`).
    Longer ones raise `ChannelError` so the job queue retries the whole job later; a 429 error
    carries its `retry_after_s`, which the queue must wait at least. Rejected requests (other
    4xx) raise `PermanentChannelError`.
    """

    def __init__(
        self,
        token: str,
        http: httpx.AsyncClient,
        *,
        max_attempts: int = 3,
        base_delay_s: float = 1.0,
        max_wait_s: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._token = token
        self._http = http
        self._max_attempts = max_attempts
        self._base_delay_s = base_delay_s
        self._max_wait_s = max_wait_s
        self._sleep = sleep
        self._rand = rand

    async def send_text(self, chat_id: str, text: str, *, reply_to: str | None = None) -> None:
        # No `parse_mode`: the text is sent as is, never interpreted as Markdown (v1 bug #7).
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "link_preview_options": {"is_disabled": True},
        }
        if reply_to is not None:
            params["reply_parameters"] = {
                "message_id": int(reply_to),
                "allow_sending_without_reply": True,
            }
        await self._call("sendMessage", params)

    async def set_webhook(self, url: str, secret: str, *, drop_pending: bool = False) -> None:
        await self._call(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret,
                "allowed_updates": ALLOWED_UPDATES,
                "drop_pending_updates": drop_pending,
            },
        )

    async def get_webhook_info(self) -> dict[str, Any]:
        info: dict[str, Any] = await self._call("getWebhookInfo", {})
        return info

    async def _call(self, method: str, params: dict[str, Any]) -> Any:
        url = f"{API_BASE}/bot{self._token}/{method}"
        for attempt in range(1, self._max_attempts + 1):
            started = time.perf_counter()
            try:
                response = await self._http.post(url, json=params)
            except httpx.TransportError as exc:  # includes timeouts
                self._log(method, None, started, attempt)
                error = ChannelError(f"{method}: {type(exc).__name__}: {self._redact(str(exc))}")
                delay = self._backoff_s(attempt)
            else:
                status = response.status_code
                self._log(method, status, started, attempt)
                body = _json_object(response)
                if body.get("ok") is True:
                    return body.get("result")
                description = self._redact(str(body.get("description", "")))[:300]
                if status == 429:
                    delay = _retry_after_s(body)
                    error = ChannelError(
                        f"{method}: rate limited for {delay:.0f} s", retry_after_s=delay
                    )
                    if delay > self._max_wait_s:
                        raise error  # too long to hold the worker: let the queue retry later
                elif status >= 500:
                    error = ChannelError(f"{method}: server error {status}: {description}")
                    delay = self._backoff_s(attempt)
                else:
                    raise PermanentChannelError(f"{method}: rejected ({status}): {description}")
            if attempt == self._max_attempts:
                raise error
            await self._sleep(delay)
        raise AssertionError("unreachable")

    def _backoff_s(self, attempt: int) -> float:
        """Exponential, with jitter in [50 %, 100 %] of the step."""
        step = min(self._max_wait_s, self._base_delay_s * 2.0 ** (attempt - 1))
        return step * (0.5 + self._rand() / 2)

    def _redact(self, text: str) -> str:
        # Bot API URLs embed the token; never let it reach logs or `jobs.last_error`.
        return text.replace(self._token, "<token>")

    def _log(self, method: str, status: int | None, started: float, attempt: int) -> None:
        logger.info(
            "telegram %s -> %s",
            method,
            status if status is not None else "network error",
            extra={
                "method": method,
                "status": status,
                "attempt": attempt,
                "latency_ms": int((time.perf_counter() - started) * 1000),
            },
        )


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _retry_after_s(body: dict[str, Any]) -> float:
    parameters = body.get("parameters")
    retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
    return float(retry_after) if isinstance(retry_after, int | float) else 1.0


async def _main(argv: list[str] | None = None) -> None:
    from dzdia_newsroom.config import TelegramBotSettings, TelegramWebhookSettings

    parser = argparse.ArgumentParser(prog="python -m dzdia_newsroom.channels.telegram")
    commands = parser.add_subparsers(dest="command", required=True)
    set_hook = commands.add_parser("set-webhook", help="register the webhook (idempotent)")
    set_hook.add_argument("--url", required=True, help="https://<host>/webhooks/telegram")
    set_hook.add_argument("--drop-pending", action="store_true", help="discard queued updates")
    commands.add_parser("webhook-info", help="show the webhook registered at Telegram")
    args = parser.parse_args(argv)

    token = TelegramBotSettings().telegram_bot_token.get_secret_value()
    async with httpx.AsyncClient(timeout=10.0) as http:
        client = TelegramClient(token, http)
        if args.command == "set-webhook":
            if not args.url.startswith("https://"):
                parser.error("--url must start with https://")
            secret = TelegramWebhookSettings().telegram_webhook_secret.get_secret_value()
            await client.set_webhook(args.url, secret, drop_pending=args.drop_pending)
            print(f"Webhook set to {args.url} (updates: {', '.join(ALLOWED_UPDATES)})")
        else:
            info = await client.get_webhook_info()
            for key in ("url", "pending_update_count", "last_error_date", "last_error_message"):
                print(f"{key}: {info.get(key)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_main())
