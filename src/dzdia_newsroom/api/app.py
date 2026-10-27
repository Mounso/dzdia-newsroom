"""FastAPI application: the Telegram webhook and a liveness probe."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI

from dzdia_newsroom.api.webhook import AllowPolicy, JobEnqueuer, WebhookConfig, router


def create_app(
    config: WebhookConfig, open_jobs: Callable[[], AbstractAsyncContextManager[JobEnqueuer]]
) -> FastAPI:
    """`open_jobs` provides the queue for the app's lifetime (a pool in production, a fake in
    tests)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_jobs() as jobs:
            app.state.jobs = jobs
            yield

    # No interactive docs: the API has no human users, so they would only expose its surface.
    app = FastAPI(
        title="dzdia-newsroom", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.config = config
    app.include_router(router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def create_app_from_env() -> FastAPI:
    from dzdia_newsroom.config import DatabaseSettings, TelegramWebhookSettings
    from dzdia_newsroom.db.pool import open_pool
    from dzdia_newsroom.db.repository import JobRepository

    telegram = TelegramWebhookSettings()
    database_url = DatabaseSettings().database_url.get_secret_value()
    team_chat = telegram.telegram_team_chat_id
    config = WebhookConfig(
        secret=telegram.telegram_webhook_secret.get_secret_value(),
        policy=AllowPolicy(
            user_ids=frozenset(str(user_id) for user_id in telegram.telegram_allowed_user_ids),
            team_chat_id=None if team_chat is None else str(team_chat),
        ),
    )

    @asynccontextmanager
    async def open_jobs() -> AsyncIterator[JobEnqueuer]:
        async with open_pool(database_url) as pool:
            yield JobRepository(pool)

    return create_app(config, open_jobs)
