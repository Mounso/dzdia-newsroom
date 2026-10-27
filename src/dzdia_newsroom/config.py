"""Typed settings loaded from the environment (and `.env` in development).

Each process only loads the settings it needs (ADR-005): the API never sees the bot token,
DB commands never need an LLM key.
"""

import re
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_ENV = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
# Telegram accepts 1-256 chars of [A-Za-z0-9_-]; 32 is our floor so the secret cannot be guessed.
_WEBHOOK_SECRET = re.compile(r"[A-Za-z0-9_-]{32,256}")


class Settings(BaseSettings):
    model_config = _ENV

    gemini_api_key: SecretStr
    # Model used by the legacy v1 bot; kept configurable in case the preview model is retired.
    v1_model: str = "gemini-3.1-flash-lite-preview"
    extraction_model: str = "gemini-2.5-flash"
    llm_max_concurrency: int = 2


class DatabaseSettings(BaseSettings):
    """Kept apart from `Settings` so DB commands (migrations, worker) need no LLM key."""

    model_config = _ENV

    database_url: SecretStr


class LoggingSettings(BaseSettings):
    model_config = _ENV

    log_format: Literal["json", "text"] = "json"  # `text` for local development
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class TelegramWebhookSettings(BaseSettings):
    """What the API needs to accept updates: the webhook secret and the allowlist."""

    model_config = _ENV

    telegram_webhook_secret: SecretStr
    # Comma-separated in the environment, e.g. `TELEGRAM_ALLOWED_USER_IDS=123,456`.
    # Empty means nobody is allowed (fail closed).
    telegram_allowed_user_ids: Annotated[frozenset[int], NoDecode] = frozenset()
    telegram_team_chat_id: int | None = None

    @field_validator("telegram_webhook_secret")
    @classmethod
    def _check_secret(cls, value: SecretStr) -> SecretStr:
        if not _WEBHOOK_SECRET.fullmatch(value.get_secret_value()):
            raise ValueError("must be 32-256 characters among A-Z, a-z, 0-9, '_' and '-'")
        return value

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def _split_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(int(part) for part in value.split(",") if part.strip())
        return value


class TelegramBotSettings(BaseSettings):
    """What the worker and the `set-webhook` command need to call the Bot API."""

    model_config = _ENV

    telegram_bot_token: SecretStr


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment


@lru_cache
def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()  # values come from the environment
