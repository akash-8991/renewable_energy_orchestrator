"""Pure-logic pieces of the generic table-mapping agent
(backend/app/ingestion/generic_table_mapper.py): column_signature()'s
order/case independence (the property that makes rule caching actually
work across re-uploads with reordered columns), and apply_mapping()'s
early-return validation branches, which — like
hackathon_dataset.ingest_reference_rows() — never touch `db` before them,
so passing None for it here asserts that contract holds."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.ingestion.generic_table_mapper import apply_mapping, column_signature  # noqa: E402


def test_column_signature_is_order_independent():
    a = column_signature(["site_name", "reading_ts", "output_kw"])
    b = column_signature(["output_kw", "site_name", "reading_ts"])
    assert a == b


def test_column_signature_is_case_independent():
    a = column_signature(["Site_Name", "Reading_TS"])
    b = column_signature(["site_name", "reading_ts"])
    assert a == b


def test_column_signature_differs_for_different_columns():
    a = column_signature(["site_name", "reading_ts", "output_kw"])
    b = column_signature(["site_name", "reading_ts", "voltage"])
    assert a != b


def test_apply_mapping_handles_empty_rows_without_touching_db():
    result = apply_mapping(None, "tenant-x", "telemetry", {"a": "event_time"}, [], source_label="test")
    assert result == {"status": "empty", "count": 0}


def test_apply_mapping_telemetry_requires_event_time_column():
    column_roles = {"site": "asset_ref", "power": "metric:power_kw:kW"}
    result = apply_mapping(None, "tenant-x", "telemetry", column_roles, [{"site": "a", "power": 1}], source_label="test")
    assert result["status"] == "error"
    assert "event_time" in result["message"]


def test_apply_mapping_telemetry_requires_asset_ref_column():
    column_roles = {"ts": "event_time", "power": "metric:power_kw:kW"}
    result = apply_mapping(None, "tenant-x", "telemetry", column_roles, [{"ts": "x", "power": 1}], source_label="test")
    assert result["status"] == "error"
    assert "asset_ref" in result["message"]


def test_apply_mapping_telemetry_requires_a_metric_column():
    column_roles = {"ts": "event_time", "site": "asset_ref"}
    result = apply_mapping(None, "tenant-x", "telemetry", column_roles, [{"ts": "x", "site": "a"}], source_label="test")
    assert result["status"] == "error"
    assert "metric" in result["message"]


def test_apply_mapping_customer_requires_customer_ref_column():
    column_roles = {"plan": "customer_field:tariff_plan"}
    result = apply_mapping(None, "tenant-x", "customer", column_roles, [{"plan": "x"}], source_label="test")
    assert result["status"] == "error"
    assert "customer_ref" in result["message"]


def test_apply_mapping_customer_requires_a_customer_field_column():
    column_roles = {"acct": "customer_ref"}
    result = apply_mapping(None, "tenant-x", "customer", column_roles, [{"acct": "CUST_1"}], source_label="test")
    assert result["status"] == "error"
    assert "customer_field" in result["message"]


def test_apply_mapping_unknown_file_kind_reports_unknown_without_touching_db():
    result = apply_mapping(None, "tenant-x", "not_a_real_kind", {}, [{"a": 1}], source_label="test")
    assert result["status"] == "unknown"
