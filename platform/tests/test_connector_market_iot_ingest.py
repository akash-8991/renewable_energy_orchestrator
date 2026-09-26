"""market_energy_purchase / iot connectors used to be registration-only
(production-readiness gap: "Connector Studio can register a market-data/
SCADA endpoint but nothing reads from one yet"). `parse_market_price_entries`
is the pure-function core of the new market_energy_purchase ingest path in
connectors.py — pulled out so it's testable without a DB, HTTP call, or
FastAPI request context. The iot path reuses `parse_telemetry_file`, already
covered by test_ingestion.py."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.routers.connectors import parse_market_price_entries  # noqa: E402


def test_parses_a_bare_array():
    result = parse_market_price_entries([
        {"timestamp": "2026-06-01T12:00:00Z", "price_per_mwh": 65.5},
        {"timestamp": "2026-06-01T13:00:00Z", "price_per_mwh": 70.0},
    ])
    assert result == [
        (datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc), 65.5),
        (datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc), 70.0),
    ]


def test_parses_a_wrapped_prices_object():
    result = parse_market_price_entries({"prices": [{"timestamp": "2026-06-01T12:00:00Z", "price_per_mwh": 65.5}]})
    assert len(result) == 1
    assert result[0][1] == 65.5


def test_returns_none_for_non_array_shape():
    assert parse_market_price_entries({"unexpected": "shape"}) is None
    assert parse_market_price_entries("just a string") is None
    assert parse_market_price_entries(42) is None


def test_skips_malformed_entries_without_failing_the_whole_batch():
    result = parse_market_price_entries([
        {"timestamp": "2026-06-01T12:00:00Z", "price_per_mwh": 65.5},
        {"timestamp": "not-a-date", "price_per_mwh": 70.0},
        {"timestamp": "2026-06-01T14:00:00Z"},  # missing price_per_mwh
        {"price_per_mwh": "not-a-number", "timestamp": "2026-06-01T15:00:00Z"},
        {"timestamp": "2026-06-01T16:00:00Z", "price_per_mwh": 80.0},
    ])
    assert len(result) == 2
    assert [price for _, price in result] == [65.5, 80.0]


def test_empty_array_returns_empty_list_not_none():
    assert parse_market_price_entries([]) == []
