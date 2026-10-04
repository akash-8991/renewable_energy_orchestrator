"""Apply database migrations, waiting for the database to accept connections.

`alembic upgrade head` on its own fails if it starts in the gap where a fresh
Postgres has passed its healthcheck but is still restarting after first-time
initialisation ("connection refused"). This waits for the database first, then
runs the same upgrade, so `docker compose up` doesn't need a second attempt.
Used as the `migrate` service's entrypoint; plain `alembic upgrade head` still
works wherever the database is already known to be up (CI, ECS run-task).
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "reo_common"))


def wait_for_database(timeout_seconds: float = 120.0) -> None:
    from reo_common.config import get_settings
    from sqlalchemy import create_engine, text

    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:
            if time.monotonic() > deadline:
                raise
            print(f"database not ready yet ({type(exc).__name__}) — retrying in 2s", file=sys.stderr)
            time.sleep(2)


def main() -> None:
    from alembic import command
    from alembic.config import Config

    wait_for_database()
    config = Config(os.path.join(os.path.dirname(__file__), "alembic.ini"))
    config.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "migrations"))
    command.upgrade(config, "head")


if __name__ == "__main__":
    main()
