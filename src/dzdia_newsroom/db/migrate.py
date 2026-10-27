"""Apply the numbered SQL files of `migrations/` once each, in order (ADR-004).

Run before starting `api` and `worker`: `python -m dzdia_newsroom.db.migrate`.
"""

import asyncio
import logging
from pathlib import Path

from psycopg import AsyncConnection

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
# Arbitrary constant: serializes concurrent migration runs (e.g. two containers starting together).
_ADVISORY_LOCK_ID = 7_302_001

logger = logging.getLogger(__name__)


async def apply_migrations(conn: AsyncConnection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations and return their versions. `conn` must be in autocommit mode,
    so that each file runs in its own transaction."""
    if not conn.autocommit:
        raise ValueError("apply_migrations needs an autocommit connection")
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    await conn.execute("SELECT pg_advisory_lock(%s)", (_ADVISORY_LOCK_ID,))
    try:
        cursor = await conn.execute("SELECT version FROM schema_migrations")
        applied = {version for (version,) in await cursor.fetchall()}
        newly_applied: list[str] = []
        for path in sorted(directory.glob("*.sql")):
            if path.stem in applied:
                continue
            async with conn.transaction():
                # Bytes, not str: the file is trusted SQL with several statements and no params.
                await conn.execute(path.read_bytes())
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,)
                )
            logger.info("applied migration %s", path.stem)
            newly_applied.append(path.stem)
        return newly_applied
    finally:
        await conn.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_ID,))


async def _main() -> None:
    from dzdia_newsroom.config import get_database_settings

    url = get_database_settings().database_url.get_secret_value()
    async with await AsyncConnection.connect(url, autocommit=True, connect_timeout=10) as conn:
        versions = await apply_migrations(conn)
    print(f"Applied {len(versions)} migration(s): {', '.join(versions) or 'none'}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
