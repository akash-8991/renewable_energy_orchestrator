"""Pure-logic pieces of the reference-dataset ingestion: capacity-weighted
splitting must conserve the total (no energy invented or lost across
assets) and shifted timestamps must land the most recent row at "now" while
preserving each row's original relative spacing."""

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.ingestion.hackathon_dataset import (  # noqa: E402
    KNOWN_TABLE_KEYS,
    UNMAPPED_TABLE_KEYS,
    _shifted_timestamps,
    _split_by_capacity,
    ingest_reference_rows,
    normalize_table_key,
)


@dataclass
class _FakeAsset:
    id: str
    rated_capacity_kw: float


def test_split_by_capacity_conserves_total():
    assets = [_FakeAsset("a", 40_000), _FakeAsset("b", 35_000), _FakeAsset("c", 25_000)]
    shares = _split_by_capacity(1000.0, assets)
    assert abs(sum(v for _, v in shares) - 1000.0) < 1e-9


def test_split_by_capacity_proportional_to_rated_capacity():
    assets = [_FakeAsset("a", 60_000), _FakeAsset("b", 40_000)]
    shares = dict((a.id, v) for a, v in _split_by_capacity(100.0, assets))
    assert abs(shares["a"] - 60.0) < 1e-9
    assert abs(shares["b"] - 40.0) < 1e-9


def test_split_by_capacity_handles_single_asset():
    assets = [_FakeAsset("only", 50_000)]
    shares = _split_by_capacity(500.0, assets)
    assert shares == [(assets[0], 500.0)]


def test_shifted_timestamps_last_row_is_now():
    ts = _shifted_timestamps(4, step_minutes=15)
    last = datetime.fromisoformat(ts[-1])
    assert (datetime.now(timezone.utc) - last) < timedelta(seconds=5)


def test_shifted_timestamps_preserves_spacing():
    ts = [datetime.fromisoformat(t) for t in _shifted_timestamps(5, step_minutes=15)]
    gaps = [(ts[i + 1] - ts[i]) for i in range(len(ts) - 1)]
    assert all(gap == timedelta(minutes=15) for gap in gaps)


# normalize_table_key() is the recognizer shared by both connector kinds:
# a data_table connector's local filename ("03_renewable_generation.csv")
# and a database connector's bare table name ("renewable_generation") must
# resolve to the same key so ingest_reference_rows() dispatches identically.


def test_normalize_table_key_strips_numeric_prefix_and_csv_extension():
    assert normalize_table_key("03_renewable_generation.csv") == "renewable_generation"


def test_normalize_table_key_strips_hyphenated_prefix_too():
    assert normalize_table_key("01-customer_demographics.CSV") == "customer_demographics"


def test_normalize_table_key_bare_db_table_name_is_unchanged():
    assert normalize_table_key("grid") == "grid"


def test_known_table_keys_cover_every_reference_dataset_file():
    # The 8 reference CSVs, minus their numeric prefix/extension.
    assert KNOWN_TABLE_KEYS == {
        "customer_demographics", "customer_energy_consumption_tariff",
        "renewable_generation", "grid", "market", "external_weather",
        "battery", "scenario_actions",
    }


def test_unmapped_table_keys_are_a_subset_of_known_table_keys():
    assert UNMAPPED_TABLE_KEYS <= KNOWN_TABLE_KEYS


# ingest_reference_rows()'s early-return branches (unknown table, a known but
# deliberately-unmapped table, or no rows) never touch `db` — passing None
# for it here asserts that contract holds rather than merely happening to
# work in production.


def test_ingest_reference_rows_rejects_unrecognized_table():
    result = ingest_reference_rows(None, "tenant-x", "not_a_real_table", [{"a": 1}], source_label="test")
    assert result["status"] == "unknown"


def test_ingest_reference_rows_reports_unmapped_tables_explicitly():
    result = ingest_reference_rows(None, "tenant-x", "battery", [{"a": 1}], source_label="test")
    assert result["status"] == "not_mapped"
    assert result["table"] == "battery"


def test_ingest_reference_rows_handles_empty_rows_without_touching_db():
    result = ingest_reference_rows(None, "tenant-x", "renewable_generation", [], source_label="test")
    assert result == {"table": "renewable_generation", "status": "empty", "count": 0}
