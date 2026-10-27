"""Typed settings loaded from the environment (and `.env` in development)."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: SecretStr
    # Model used by the legacy v1 bot; kept configurable in case the preview model is retired.
    v1_model: str = "gemini-3.1-flash-lite-preview"
    extraction_model: str = "gemini-2.5-flash"
    llm_max_concurrency: int = 2


class DatabaseSettings(BaseSettings):
    """Kept apart from `Settings` so DB commands (migrations, worker) need no LLM key."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: SecretStr


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment


@lru_cache
def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()  # values come from the environment
