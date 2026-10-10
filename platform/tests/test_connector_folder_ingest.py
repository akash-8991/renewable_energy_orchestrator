"""A data_table connector may point at the whole watched data folder (`.`, or
a sub-folder) and ingest every supported file in it: each file has its own
change check and savepoint, so one bad file is reported without stopping the
rest, and an unchanged folder is monitored without being re-ingested."""

import contextlib
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.routers import connectors as cr  # noqa: E402
from database.connection import break_glass_cross_tenant, reset_current_tenant, set_current_tenant  # noqa: E402
from models.canonical import Connector  # noqa: E402
from reo_common.security import AuthContext  # noqa: E402

ASSET = "11111111-1111-1111-1111-111111111111"
HEADER = "asset_id,metric,event_time,value,unit\n"


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cr.settings, "data_watch_dir", str(tmp_path))
    return tmp_path


def _write(folder: Path, name: str, rows: list[str]) -> Path:
    path = folder / name
    path.write_text(HEADER + "".join(r + "\n" for r in rows))
    return path


# --- path resolution ----------------------------------------------------------


def test_the_whole_folder_can_be_named_several_ways(data_dir):
    (data_dir / "exports").mkdir()
    assert cr._resolve_local_source(".") == data_dir.resolve()
    assert cr._resolve_local_source("/") == data_dir.resolve()
    assert cr._resolve_local_source("*") == data_dir.resolve()
    assert cr._resolve_local_source("exports") == (data_dir / "exports").resolve()
    # the host-side path people paste for "my data folder" can only mean the one folder the platform sees
    assert cr._resolve_local_source("/Users/someone/project/data") == data_dir.resolve()
    assert cr._resolve_local_source("/Users/someone/project/data/") == data_dir.resolve()


def test_a_filename_and_other_paths_still_resolve_normally(data_dir):
    assert cr._resolve_local_source("03_renewable_generation.csv") == (data_dir / "03_renewable_generation.csv").resolve()
    assert not cr._resolve_local_source("/Users/someone/project/exports").exists()  # still reported as not found, with the hint


def test_nothing_can_escape_the_watched_folder(data_dir):
    with pytest.raises(ValueError):
        cr._resolve_local_source("../../etc")


def test_the_not_found_hint_mentions_the_whole_folder_option():
    assert '"."' in cr._missing_local_file_message("/Users/someone/project/exports")


# --- ingestion ------------------------------------------------------------------


@pytest.fixture()
def harness(db_session, two_tenants, data_dir, monkeypatch):
    t1, _ = two_tenants
    # run_connector_ingest commits; a committed active connector pointing at "." would be picked up
    # (and its folder ingested) by any running api's poller — so the test session must never commit.
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    published: list[tuple[str, int]] = []
    monkeypatch.setattr(cr, "publish_readings", lambda bus, tenant, readings, lineage_id=None: (published.append((lineage_id, len(readings))), "lineage-x")[1])
    with break_glass_cross_tenant():
        connector = Connector(tenant_id=t1.id, name=f"folder-{uuid.uuid4().hex[:6]}", kind="data_table", endpoint_url=".", status="active")
        db_session.add(connector)
        db_session.flush()
    ctx = AuthContext(user_id="00000000-0000-0000-0000-000000000001", tenant_id=t1.id, roles=["tenant_admin"], email="a@test.example")
    yield db_session, ctx, connector, published
    store = cr._fingerprint_store()
    for pattern in (f"reo:connector:fingerprint:{connector.id}*", f"reo:connector:watermark:{connector.id}*"):
        for key in store.scan_iter(pattern):
            store.delete(key)


def test_every_supported_file_in_the_folder_is_ingested(harness, data_dir):
    db, ctx, connector, published = harness
    _write(data_dir, "a.csv", [f"{ASSET},power_kw,2026-10-04T10:00:00+00:00,1,kW", f"{ASSET},power_kw,2026-10-04T10:05:00+00:00,2,kW"])
    _write(data_dir, "b.csv", [f"{ASSET},soc_pct,2026-10-04T10:00:00+00:00,50,%"])
    (data_dir / "notes.txt").write_text("not data")
    _write(data_dir, ".hidden.csv", [f"{ASSET},power_kw,2026-10-04T10:00:00+00:00,9,kW"])

    response = cr.run_connector_ingest(db, ctx, connector)

    assert response.rows_queued == 3
    detail = response.detail
    assert detail["status"] == "folder" and detail["files_seen"] == 2
    assert [(f["file"], f["rows"]) for f in detail["files"]] == [("a.csv", 2), ("b.csv", 1)]
    assert sorted(n for _, n in published) == [1, 2]


def test_one_bad_file_is_reported_without_stopping_the_others(harness, data_dir):
    db, ctx, connector, published = harness
    _write(data_dir, "good.csv", [f"{ASSET},power_kw,2026-10-04T10:00:00+00:00,1,kW"])
    (data_dir / "bad.csv").write_text("foo,bar\n1,2\n")

    response = cr.run_connector_ingest(db, ctx, connector)

    by_file = {f["file"]: f for f in response.detail["files"]}
    assert by_file["good.csv"]["status"] == "ingested" and by_file["good.csv"]["rows"] == 1
    assert by_file["bad.csv"]["status"] == "error"
    assert response.detail["files_failed"] == 1 and response.rows_queued == 1


def test_a_folder_where_nothing_works_is_an_error(harness, data_dir):
    db, ctx, connector, _ = harness
    (data_dir / "bad.csv").write_text("foo,bar\n1,2\n")
    with pytest.raises(HTTPException) as exc:
        cr.run_connector_ingest(db, ctx, connector)
    assert exc.value.status_code == 400 and "bad.csv" in exc.value.detail


def test_an_empty_folder_is_a_clear_error(harness, data_dir):
    db, ctx, connector, _ = harness
    (data_dir / "readme.txt").write_text("x")
    with pytest.raises(HTTPException) as exc:
        cr.run_connector_ingest(db, ctx, connector)
    assert "no .csv/.json/.xlsx files" in exc.value.detail


def test_polling_a_folder_ingests_only_what_is_new(harness, data_dir):
    db, ctx, connector, published = harness
    a = _write(data_dir, "a.csv", [f"{ASSET},power_kw,2026-10-04T10:00:00+00:00,1,kW"])
    _write(data_dir, "b.csv", [f"{ASSET},soc_pct,2026-10-04T10:00:00+00:00,50,%"])
    cr.run_connector_ingest(db, ctx, connector)  # first ingest (as a manual click or the first poll)
    published.clear()

    quiet = cr.run_connector_ingest(db, ctx, connector, skip_unchanged=True)
    assert quiet.rows_queued == 0 and quiet.detail == {"status": "no_new_data"} and published == []

    # a.csv grows by one newer reading: only that reading is taken, b.csv is not even re-read
    with a.open("a") as fh:
        fh.write(f"{ASSET},power_kw,2026-10-04T10:10:00+00:00,3,kW\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.utime(a, ns=(a.stat().st_atime_ns, a.stat().st_mtime_ns + 5_000_000_000))
    grown = cr.run_connector_ingest(db, ctx, connector, skip_unchanged=True)
    assert grown.rows_queued == 1
    by_file = {f["file"]: f["status"] for f in grown.detail["files"]}
    assert by_file == {"a.csv": "ingested", "b.csv": "no_new_data"}


def test_a_recognised_but_unmapped_reference_file_is_not_an_error(harness, data_dir):
    db, ctx, connector, _ = harness
    (data_dir / "05_battery.csv").write_text("timestamp,soc\n2026-01-01 00:00:00,50\n")
    _write(data_dir, "extra.csv", [f"{ASSET},power_kw,2026-10-04T10:00:00+00:00,1,kW"])
    response = cr.run_connector_ingest(db, ctx, connector)
    statuses = {f["file"]: f["status"] for f in response.detail["files"]}
    assert statuses["05_battery.csv"] == "not_mapped" and statuses["extra.csv"] == "ingested"
    assert response.detail["files_failed"] == 0


def test_the_connection_test_describes_the_folder(harness, data_dir):
    db, ctx, connector, _ = harness
    _write(data_dir, "a.csv", [f"{ASSET},power_kw,2026-10-04T10:00:00+00:00,1,kW"])
    token = set_current_tenant(ctx.tenant_id)
    try:
        result = cr.test_connector(connector.id, ctx=ctx, db=db)
        assert result.http_reachable is True and "1 supported file" in result.note
        for f in data_dir.glob("*.csv"):
            f.unlink()
        assert cr.test_connector(connector.id, ctx=ctx, db=db).http_reachable is False
    finally:
        reset_current_tenant(token)
