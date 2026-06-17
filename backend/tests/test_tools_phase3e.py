"""Phase 3E: query_database cache + lineage integration tests."""

from __future__ import annotations

import json

import pytest
from app.agents import tools as agent_tools
from app.services import data_service, metadata_service, query_cache


def _invoke(tool, **kwargs):
    return tool.invoke(kwargs) if kwargs else tool.invoke({})


async def _upload(csv: str, ds_id: str, filename: str = "s.csv"):
    from tests.conftest import _FakeUploadFile

    upload = _FakeUploadFile(filename, csv.encode())
    await data_service.save_uploaded_file(upload, ds_id)


def _find(tool_list, name: str):
    for t in tool_list:
        if getattr(t, "name", None) == name:
            return t
    raise AssertionError(f"tool {name!r} not in {tool_list}")


@pytest.fixture
def fresh_query_cache(monkeypatch):
    cache = query_cache.QueryCache(ttl_seconds=60.0, max_entries=32)
    query_cache.set_cache(cache)
    yield cache
    query_cache.set_cache(None)


async def test_query_database_records_lineage_on_miss(tmp_data_dir, fresh_query_cache):
    await _upload("a,b\n1,x\n2,y\n", "ds-qe-1")
    tool_list = agent_tools.build_tools("ds-qe-1")
    q = _find(tool_list, "query_database")
    result = json.loads(_invoke(q, sql_query="SELECT a, b FROM uploaded_data"))
    assert "error" not in result
    assert result["cache_hit"] is False
    # Lineage is per-data-source.
    entries = metadata_service.get_lineage("ds-qe-1")
    assert len(entries) == 1
    assert entries[0]["sql"].strip().upper().startswith("SELECT")
    assert entries[0]["ok"] is True
    assert entries[0]["cache_hit"] is False


async def test_query_database_cache_hit_skips_sqlite(tmp_data_dir, fresh_query_cache):
    await _upload("a,b\n1,x\n2,y\n", "ds-qe-2")
    tool_list = agent_tools.build_tools("ds-qe-2")
    q = _find(tool_list, "query_database")
    first = json.loads(_invoke(q, sql_query="SELECT a FROM uploaded_data"))
    second = json.loads(_invoke(q, sql_query="SELECT a FROM uploaded_data"))
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert second["rows"] == first["rows"]
    # Two lineage records (miss + hit), hit is flagged.
    entries = metadata_service.get_lineage("ds-qe-2")
    assert len(entries) == 2
    assert entries[0]["cache_hit"] is True  # newest first
    assert entries[1]["cache_hit"] is False


async def test_query_database_cache_key_includes_bindings(tmp_data_dir, fresh_query_cache):
    """A query that runs against ds1 alone should not hit when the binding
    set becomes {ds1, ds2}."""
    await _upload("a\n1\n2\n", "ds-qe-a")
    await _upload("a\n3\n4\n", "ds-qe-b")

    tools_a = agent_tools.build_tools(["ds-qe-a"])
    tools_ab = agent_tools.build_tools(["ds-qe-a", "ds-qe-b"])
    qa = _find(tools_a, "query_database")
    qab = _find(tools_ab, "query_database")
    json.loads(_invoke(qa, sql_query="SELECT a FROM uploaded_data"))
    second = json.loads(_invoke(qab, sql_query="SELECT a FROM uploaded_data"))
    # Different binding set => cache miss.
    assert second["cache_hit"] is False


async def test_query_database_records_error_in_lineage(tmp_data_dir, fresh_query_cache):
    await _upload("a\n1\n", "ds-qe-err")
    tool_list = agent_tools.build_tools("ds-qe-err")
    q = _find(tool_list, "query_database")
    result = json.loads(_invoke(q, sql_query="SELECT * FROM nonexistent_table"))
    assert "error" in result
    entries = metadata_service.get_lineage("ds-qe-err")
    assert len(entries) == 1
    assert entries[0]["ok"] is False
    assert entries[0]["error"]


async def test_query_database_rejects_unsafe_sql_without_recording(tmp_data_dir, fresh_query_cache):
    await _upload("a\n1\n", "ds-qe-safe")
    tool_list = agent_tools.build_tools("ds-qe-safe")
    q = _find(tool_list, "query_database")
    result = json.loads(_invoke(q, sql_query="DROP TABLE uploaded_data"))
    assert "error" in result
    # Unsafe query is rejected before reaching the cache/lineage path.
    assert metadata_service.get_lineage("ds-qe-safe") == []


async def test_delete_data_source_invalidates_cache(tmp_data_dir, fresh_query_cache):
    await _upload("a\n1\n2\n", "ds-qe-evict")
    tool_list = agent_tools.build_tools("ds-qe-evict")
    q = _find(tool_list, "query_database")
    json.loads(_invoke(q, sql_query="SELECT a FROM uploaded_data"))
    # Sanity: the entry is in the cache.
    assert fresh_query_cache.stats()["size"] == 1
    data_service.delete_data_source("ds-qe-evict")
    # After deletion, no entry references the data source.
    assert fresh_query_cache.stats()["size"] == 0
