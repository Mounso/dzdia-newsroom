"""Run the worker: `python -m dzdia_newsroom.worker`.

On SIGTERM (`docker compose stop`) or SIGINT, the worker stops claiming jobs and lets the
current one finish for up to `SHUTDOWN_GRACE_S`, then cancels it. A cancelled job is released
back to the queue without using up an attempt, so another worker takes it over at once (if the
release fails, its lease expires instead).

The worker needs the database URL, the bot token and the Gemini key (ADR-0019, point 10): it
refuses to start without any of them.
"""

import asyncio
import contextlib
import logging
import signal

import httpx

from dzdia_newsroom.channels.telegram import TelegramClient
from dzdia_newsroom.config import (
    DatabaseSettings,
    LLMSettings,
    LoggingSettings,
    TelegramBotSettings,
)
from dzdia_newsroom.db.pool import open_pool
from dzdia_newsroom.db.repository import JobRepository
from dzdia_newsroom.guardrails.ssrf import guarded_client
from dzdia_newsroom.llm import GeminiClient
from dzdia_newsroom.logging_setup import configure_logging
from dzdia_newsroom.runtime import event_loop_factory
from dzdia_newsroom.tools.fetch_page import PHASE_TIMEOUT_S
from dzdia_newsroom.worker.handlers import build_handlers
from dzdia_newsroom.worker.queue import Worker, listen_for_jobs

logger = logging.getLogger("dzdia_newsroom.worker")  # not __name__, which is "__main__"

# Plus the release timeout (3 s), keep below compose's `stop_grace_period` (30 s), after which
# Docker sends SIGKILL.
SHUTDOWN_GRACE_S = 25.0
TELEGRAM_TIMEOUT_S = 15.0


async def run() -> None:
    database_url = DatabaseSettings().database_url.get_secret_value()
    bot_token = TelegramBotSettings().telegram_bot_token.get_secret_value()
    # Fails at start without the Gemini key (ADR-0019, point 10).
    llm_settings = LLMSettings()
    # One generator for the process, built like the eval's v2 arm: production runs what was
    # measured.
    llm = GeminiClient(
        llm_settings.gemini_api_key.get_secret_value(),
        model=llm_settings.generation_model,
        max_concurrency=llm_settings.llm_max_concurrency,
        max_rpm=llm_settings.llm_max_rpm,
        timeout_s=llm_settings.llm_timeout_s,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # Not available on Windows: Ctrl+C then cancels everything (no grace period).
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    async with (
        open_pool(database_url) as pool,
        # The Bot API host is fixed in code: a plain client (ADR-0018, point 3).
        httpx.AsyncClient(timeout=TELEGRAM_TIMEOUT_S) as http,
        # Journalists' links go through the SSRF guard only.
        guarded_client(timeout=httpx.Timeout(PHASE_TIMEOUT_S)) as pages,
    ):
        handlers = build_handlers(TelegramClient(bot_token, http), pages, llm)
        worker = Worker(JobRepository(pool), handlers)
        wake = asyncio.Event()
        listener = asyncio.create_task(listen_for_jobs(database_url, wake))
        working = asyncio.create_task(worker.run_forever(wake, stop))
        stopping = asyncio.create_task(stop.wait())
        logger.info("worker started")
        try:
            await asyncio.wait({working, stopping}, return_when=asyncio.FIRST_COMPLETED)
            if stop.is_set():
                logger.info("stop requested, finishing the current job")
            try:
                await asyncio.wait_for(working, timeout=SHUTDOWN_GRACE_S)  # cancels on timeout
            except TimeoutError:
                logger.warning("grace period over: current job cancelled")
        finally:
            for task in (listener, working, stopping):
                task.cancel()
            await asyncio.gather(listener, working, stopping, return_exceptions=True)
    logger.info("worker stopped")


def main() -> None:
    logging_settings = LoggingSettings()
    configure_logging(logging_settings.log_format, logging_settings.log_level)
    asyncio.run(run(), loop_factory=event_loop_factory())


if __name__ == "__main__":
    main()
