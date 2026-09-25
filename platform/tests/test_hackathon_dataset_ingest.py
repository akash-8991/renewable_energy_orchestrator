"""Pure-logic pieces of the reference-dataset ingestion: capacity-weighted
splitting must conserve the total (no energy invented or lost across
assets) and shifted timestamps must land the most recent row at "now" while
preserving each row's original relative spacing."""

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.ingestion.hackathon_dataset import _shifted_timestamps, _split_by_capacity  # noqa: E402


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
