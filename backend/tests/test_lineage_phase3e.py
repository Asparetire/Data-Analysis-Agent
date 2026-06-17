"""Tests for the lineage audit (Phase 3E)."""

from __future__ import annotations

import pytest
from app.services import lineage, metadata_service


@pytest.fixture
def fresh_meta_dir(tmp_path, monkeypatch):
    """Point metadata_service at a temp dir so we don't clobber the real sidecar."""
    monkeypatch.setattr(
        "app.services.metadata_service._meta_path", lambda: tmp_path / "datasources.json"
    )
    return tmp_path


def test_extract_table_refs_basic():
    sql = "SELECT a, b FROM orders o JOIN users u ON o.uid = u.id WHERE x > 1"
    refs = lineage.extract_table_refs(sql)
    # We don't strictly care about which gets picked up as long as
    # the user-data tables come through.
    assert "orders" in refs
    assert "users" in refs


def test_extract_table_refs_handles_prefixed():
    sql = "SELECT * FROM main.orders UNION ALL SELECT * FROM ds_1.users"
    refs = lineage.extract_table_refs(sql)
    assert "main.orders" in refs
    assert "ds_1.users" in refs


def test_extract_table_refs_strips_string_literals():
    sql = "SELECT 'no from here' AS x, id FROM real_table"
    refs = lineage.extract_table_refs(sql)
    assert "real_table" in refs
    # "no from here" should not have leaked in as a table name.
    assert "no from here" not in refs
    assert "no" not in refs


def test_extract_table_refs_handles_quoted_identifiers():
    sql = 'SELECT * FROM "Order Table" o JOIN "users" u ON 1=1'
    refs = lineage.extract_table_refs(sql)
    assert "Order Table" in refs
    assert "users" in refs


def test_extract_table_refs_empty():
    assert lineage.extract_table_refs("") == []
    assert lineage.extract_table_refs("SELECT 1") == []


def test_record_query_writes_to_each_binding(fresh_meta_dir):
    lineage.record_query(
        source_ids=["ds1", "ds2"],
        sql="SELECT 1 FROM t",
        ok=True,
        row_count=1,
        duration_ms=12.3,
        cache_hit=False,
    )
    a = metadata_service.get_lineage("ds1")
    b = metadata_service.get_lineage("ds2")
    assert len(a) == 1
    assert len(b) == 1
    rec = a[0]
    assert rec["sql"] == "SELECT 1 FROM t"
    assert rec["ok"] is True
    assert rec["row_count"] == 1
    assert rec["duration_ms"] == 12.3
    assert rec["cache_hit"] is False
    assert "ds1" in rec["source_ids"]


def test_record_query_error_path(fresh_meta_dir):
    lineage.record_query(
        source_ids=["ds1"],
        sql="SELECT bad",
        ok=False,
        duration_ms=3.0,
        error="table not found",
    )
    a = metadata_service.get_lineage("ds1")
    assert a[0]["ok"] is False
    assert a[0]["error"] == "table not found"


def test_record_query_cache_hit(fresh_meta_dir):
    lineage.record_query(
        source_ids=["ds1"],
        sql="SELECT 1",
        ok=True,
        cache_hit=True,
    )
    a = metadata_service.get_lineage("ds1")
    assert a[0]["cache_hit"] is True


def test_record_query_respects_max(fresh_meta_dir):
    """We don't test the cap directly here (covered in metadata tests), just
    that multiple writes land in order."""
    for i in range(3):
        lineage.record_query(
            source_ids=["ds1"],
            sql=f"SELECT {i}",
            ok=True,
            row_count=i,
        )
    a = metadata_service.get_lineage("ds1", limit=10)
    assert [r["sql"] for r in a] == ["SELECT 2", "SELECT 1", "SELECT 0"]


def test_record_query_writes_entry_for_unknown_source(fresh_meta_dir):
    """record_query is best-effort and never raises. An unknown source id
    still gets a sidecar entry -- ``delete_entry`` is responsible for
    cleanup when the data source itself is deleted.
    """
    lineage.record_query(
        source_ids=["does-not-exist"],
        sql="SELECT 1",
        ok=True,
    )
    a = metadata_service.get_lineage("does-not-exist")
    assert len(a) == 1
    assert a[0]["sql"] == "SELECT 1"
    # The orphan is cleared by delete_entry, mirroring the data source.
    metadata_service.delete_entry("does-not-exist")
    assert metadata_service.get_lineage("does-not-exist") == []


def test_record_query_truncates_error(fresh_meta_dir):
    huge = "x" * 10_000
    lineage.record_query(
        source_ids=["ds1"],
        sql="SELECT bad",
        ok=False,
        error=huge,
    )
    a = metadata_service.get_lineage("ds1")
    assert len(a[0]["error"]) <= 500
