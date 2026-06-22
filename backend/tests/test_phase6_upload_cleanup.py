"""Phase 6: upload-failure cleanup test.

When save_uploaded_file fails mid-load (e.g. to_sql raises), the upload_path,
the cached engine, and the half-written SQLite file should all be cleaned up
so a retry doesn't leak space or confuse list_datasources.
"""

from __future__ import annotations

import pytest
from app.services import data_service
from app.utils import database


@pytest.mark.asyncio
async def test_save_uploaded_file_cleans_up_on_to_sql_failure(
    tmp_data_dir, make_upload, monkeypatch
):
    """Force a failure inside to_sql and verify the SQLite + engine are gone."""
    # Patch pd.DataFrame.to_sql to raise — simulates a disk-full / schema
    # conflict during the load step.
    import pandas as pd

    original_to_sql = pd.DataFrame.to_sql

    def _boom(self, *args, **kwargs):
        raise RuntimeError("simulated to_sql failure")

    monkeypatch.setattr(pd.DataFrame, "to_sql", _boom)

    upload = make_upload("boom.csv", b"a,b\n1,2\n3,4\n")
    with pytest.raises(ValueError, match="Failed to load data into SQLite"):
        await data_service.save_uploaded_file(upload, "ds-boom")

    # Restore so the assertions below don't accidentally go through _boom.
    monkeypatch.setattr(pd.DataFrame, "to_sql", original_to_sql)

    sqlite_path = database.SQLITE_DIR / "ds-boom.db"
    assert not sqlite_path.exists(), "SQLite file should be deleted after failure"

    uploads_dir = database.DATA_DIR / "uploads"
    leftover = list(uploads_dir.glob("ds-boom*"))
    assert leftover == [], f"upload_path should be deleted, found {leftover}"

    # Engine cache should not retain an entry for the failed id.
    assert "ds-boom" not in database._engines


@pytest.mark.asyncio
async def test_save_uploaded_file_persists_row_count_in_sidecar(tmp_data_dir, make_upload):
    """Phase 6: upload writes row_count to sidecar so list_tables can skip COUNT(*)."""
    from app.services import metadata_service

    upload = make_upload(
        "withrows.csv",
        b"id,name\n1,alice\n2,bob\n3,carol\n4,dave\n",
    )
    await data_service.save_uploaded_file(upload, "ds-rows")
    meta = metadata_service.get_table_metadata("ds-rows", "uploaded_data")
    assert meta is not None
    assert meta.get("row_count") == 4


@pytest.mark.asyncio
async def test_get_table_info_uses_sidecar_row_count(tmp_data_dir, make_upload, monkeypatch):
    """Phase 6: get_table_info returns the cached count without firing COUNT(*)."""
    upload = make_upload("cached.csv", b"x\n1\n2\n")
    await data_service.save_uploaded_file(upload, "ds-cached")

    # Make COUNT(*) blow up — if get_table_info tries to run it, the test fails.

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def execute(self, stmt, *args, **kwargs):
            s = str(stmt)
            if "COUNT(*)" in s:
                raise AssertionError("get_table_info fired COUNT(*) — should use sidecar")
            # PRAGMA table_info — return an empty result for the test; the
            # columns list will be empty but row_count still comes from sidecar.
            return []

    def _patched_connect():
        return _Conn()

    monkeypatch.setattr(database.get_engine("ds-cached"), "connect", _patched_connect)

    info = data_service.get_table_info("ds-cached")
    assert info is not None
    assert info["row_count"] == 2
