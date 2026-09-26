"""A data_table connector's local-path field expects a bare filename already
inside the watched data folder (DATA_WATCH_DIR) — not the host's own
absolute path to that folder, which means nothing inside the platform's
containers. Pasting a host path (e.g. "/Users/me/project/data") doesn't
error at creation time (it still resolves to a syntactically valid path
*inside* the watched folder, just one that doesn't exist there), so the
"not found" error message needs to actively diagnose that specific,
easy-to-make mistake rather than leave the operator guessing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.routers.connectors import _missing_local_file_message  # noqa: E402


def test_bare_filename_gets_a_plain_not_found_message():
    message = _missing_local_file_message("03_renewable_generation.csv")
    assert message == "'03_renewable_generation.csv' was not found under the watched data folder"


def test_absolute_host_path_gets_an_actionable_hint():
    message = _missing_local_file_message("/Users/akash/Desktop/Projects/Hackathon_ET_Accenture/data")
    assert "was not found under the watched data folder" in message
    assert "bare filename" in message
    assert "03_renewable_generation.csv" in message  # a concrete example, not just abstract advice


def test_relative_subpath_also_gets_the_hint():
    message = _missing_local_file_message("exports/telemetry.csv")
    assert "bare filename" in message
