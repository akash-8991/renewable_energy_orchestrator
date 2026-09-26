"""Pulls rows from an external database table for a `database`-kind
connector (backend/app/routers/connectors.py) — the one place in the
platform that opens a second, ad-hoc SQLAlchemy engine against a
human-configured connection string rather than the platform's own DB.

Table names are reflected via SQLAlchemy (`autoload_with`) rather than
interpolated into a raw SQL string, so there's no identifier-injection
surface here — the caller is still expected to validate `table_name`
against a known allow-list before calling this (see
`hackathon_dataset.KNOWN_TABLE_KEYS`), both to avoid using this as a
generic arbitrary-table reflection oracle and because ingestion only knows
how to map a fixed set of table shapes anyway.
"""

from __future__ import annotations

from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.engine import URL, make_url


def _engine_url(connection_url: str) -> URL:
    """A bare `postgresql://` (no +driver) resolves to psycopg (v3) by
    default as of SQLAlchemy 2.1 — not installed here, only psycopg2-binary
    is (see backend/requirements.txt) — so force the driver explicitly
    rather than depend on whatever a human typed or SQLAlchemy's current
    default happens to be."""
    url = make_url(connection_url)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg2")
    return url


def rows_from_db_table(connection_url: str, table_name: str, *, limit: int | None = None) -> list[dict]:
    engine = create_engine(_engine_url(connection_url), pool_pre_ping=True)
    try:
        metadata = MetaData()
        table = Table(table_name, metadata, autoload_with=engine)
        stmt = select(table)
        if limit:
            stmt = stmt.limit(limit)
        with engine.connect() as conn:
            result = conn.execute(stmt)
            return [dict(row._mapping) for row in result]
    finally:
        engine.dispose()


def test_db_connection(connection_url: str) -> tuple[bool, str | None]:
    engine = create_engine(_engine_url(connection_url), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return True, None
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller as a test result, not swallowed
        return False, str(exc)
    finally:
        engine.dispose()
