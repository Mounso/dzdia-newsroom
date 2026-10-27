"""Run the API: `python -m dzdia_newsroom.api [--host 0.0.0.0] [--port 8000]`."""

import argparse
import asyncio

import uvicorn

from dzdia_newsroom.api.app import create_app_from_env
from dzdia_newsroom.config import LoggingSettings
from dzdia_newsroom.logging_setup import configure_logging
from dzdia_newsroom.runtime import event_loop_factory


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m dzdia_newsroom.api")
    parser.add_argument("--host", default="0.0.0.0")  # inside a container, behind Caddy
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    logging_settings = LoggingSettings()
    configure_logging(logging_settings.log_format, logging_settings.log_level)
    # `log_config=None`: uvicorn keeps our JSON handler instead of installing its own.
    config = uvicorn.Config(create_app_from_env(), host=args.host, port=args.port, log_config=None)
    # Server.serve() instead of uvicorn.run() so we choose the event loop (see runtime.py).
    asyncio.run(uvicorn.Server(config).serve(), loop_factory=event_loop_factory())


if __name__ == "__main__":
    main()
