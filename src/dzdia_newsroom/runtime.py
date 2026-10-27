"""Process-level helpers shared by the `api` and `worker` entry points."""

import asyncio
import sys
from collections.abc import Callable


def event_loop_factory() -> Callable[[], asyncio.AbstractEventLoop] | None:
    """psycopg's async mode does not work on Windows' default Proactor loop; use a selector
    loop there so both processes also run locally, outside Docker."""
    return asyncio.SelectorEventLoop if sys.platform == "win32" else None
