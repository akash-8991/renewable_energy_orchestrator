"""backend/app/ingestion/db_source.py: the driver-normalization step a
`database`-kind connector's connection string goes through before
create_engine() ever sees it. Regression test for a real bug hit while
building this — SQLAlchemy 2.1 resolves a bare "postgresql://" to the
`psycopg` (v3) driver by default, which isn't installed here (only
psycopg2-binary is, see backend/requirements.txt), so ingestion 502'd with
"No module named 'psycopg'" until the driver was pinned explicitly."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.ingestion.db_source import _engine_url  # noqa: E402


def test_bare_postgresql_url_is_pinned_to_psycopg2():
    url = _engine_url("postgresql://user:pass@source-db:5432/client_export")
    assert url.drivername == "postgresql+psycopg2"


def test_explicit_driver_is_left_untouched():
    url = _engine_url("postgresql+psycopg2://user:pass@source-db:5432/client_export")
    assert url.drivername == "postgresql+psycopg2"


def test_normalization_preserves_host_and_database():
    url = _engine_url("postgresql://reo_source:reo-source-secret@source-db:5432/client_export")
    assert url.host == "source-db"
    assert url.port == 5432
    assert url.database == "client_export"
    assert url.username == "reo_source"
