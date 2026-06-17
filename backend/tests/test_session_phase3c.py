"""Tests for Phase 3C multi-source + JOIN behavior."""

from __future__ import annotations

import json

from app.agents import tools as agent_tools
from app.services import data_service, session_service


def _invoke(tool, **kwargs):
    return tool.invoke(kwargs) if kwargs else tool.invoke({})


def _find(tool_list, name: str):
    for t in tool_list:
        if getattr(t, "name", None) == name:
            return t
    raise AssertionError(f"tool {name!r} not in {tool_list}")


async def _upload(csv: str, ds_id: str, filename: str = "s.csv"):
    from tests.conftest import _FakeUploadFile

    upload = _FakeUploadFile(filename, csv.encode())
    await data_service.save_uploaded_file(upload, ds_id)


# ---------------------------------------------------------------------------
# session_service: multi-binding
# ---------------------------------------------------------------------------


async def test_set_data_source_ids_dedupes_and_sets_primary(fake_redis):
    sid = await session_service.create_session()
    merged = await session_service.set_data_source_ids(sid, ["a", "b", "a", "c"])
    assert merged["data_source_ids"] == ["a", "b", "c"]
    assert merged["data_source_id"] == "a"


async def test_set_data_source_ids_empty_clears_bindings(fake_redis):
    sid = await session_service.create_session()
    await session_service.set_data_source_ids(sid, ["a", "b"])
    cleared = await session_service.set_data_source_ids(sid, [])
    assert cleared["data_source_ids"] == []
    assert cleared["data_source_id"] is None


async def test_unbind_data_source_drops_from_list(fake_redis):
    sid = await session_service.create_session()
    await session_service.set_data_source_ids(sid, ["a", "b", "c"])
    after = await session_service.unbind_data_source(sid, "b")
    assert after["data_source_ids"] == ["a", "c"]
    # Removing the primary hands off to the next remaining entry.
    after2 = await session_service.unbind_data_source(sid, "a")
    assert after2["data_source_ids"] == ["c"]
    assert after2["data_source_id"] == "c"


async def test_delete_sessions_by_data_source_preserves_sessions_with_other_bindings(
    fake_redis,
):
    """A session bound to [a, b] should survive deleting data source a."""
    sid = await session_service.create_session()
    await session_service.set_data_source_ids(sid, ["a", "b"])
    removed = await session_service.delete_sessions_by_data_source("a")
    assert removed == 0
    s = await session_service.get_session(sid)
    assert s is not None
    assert s["data_source_ids"] == ["b"]
    assert s["data_source_id"] == "b"


async def test_delete_sessions_by_data_source_drops_orphan_sessions(fake_redis):
    """A session bound to [a] only should be removed when a is deleted."""
    sid = await session_service.create_session()
    await session_service.set_data_source_ids(sid, ["a"])
    removed = await session_service.delete_sessions_by_data_source("a")
    assert removed == 1
    assert await session_service.get_session(sid) is None


# ---------------------------------------------------------------------------
# tools.build_tools: multi-source routing
# ---------------------------------------------------------------------------


async def test_build_tools_with_single_id_keeps_primary_alias(tmp_data_dir):
    await _upload("a,b\n1,2\n", "ds-only")
    tool_list = agent_tools.build_tools("ds-only")
    list_tables = _find(tool_list, "list_tables")
    result = json.loads(_invoke(list_tables))
    assert all(t["alias"] == "main" for t in result["tables"])
    assert result["tables"][0]["table"] == "uploaded_data"


async def test_build_tools_with_list_annotates_aliases(tmp_data_dir):
    await _upload("a,b\n1,2\n", "ds-first")
    await _upload("c,d\n3,4\n", "ds-second")
    tool_list = agent_tools.build_tools(["ds-first", "ds-second"])
    list_tables = _find(tool_list, "list_tables")
    result = json.loads(_invoke(list_tables))
    aliases = {t["alias"] for t in result["tables"]}
    assert aliases == {"main", "ds_1"}


async def test_query_database_join_across_sources(tmp_data_dir):
    """ATTACH makes primary + ds_1 queryable in one SQL.

    The primary's tables are accessed without a prefix; aux sources are
    referenced via their ``ds_N.`` alias.
    """
    await _upload("id,amt\n1,10\n2,20\n", "ds-orders")
    await _upload("id,name\n1,A\n2,B\n", "ds-customers")
    tool_list = agent_tools.build_tools(["ds-orders", "ds-customers"])
    q = _find(tool_list, "query_database")
    sql = (
        "SELECT o.id, c.name, o.amt "
        "FROM uploaded_data AS o "
        "JOIN ds_1.uploaded_data AS c ON o.id = c.id "
        "ORDER BY o.id"
    )
    result = json.loads(_invoke(q, sql_query=sql))
    assert "error" not in result, result
    assert result["row_count"] == 2
    assert result["rows"][0]["name"] == "A"
    # The tools layer stringifies cell values for JSON, so we compare as str.
    assert int(result["rows"][0]["amt"]) == 10


async def test_query_database_single_source_still_works(tmp_data_dir):
    await _upload("a,b\n1,2\n3,4\n", "ds-single-2")
    tool_list = agent_tools.build_tools("ds-single-2")
    q = _find(tool_list, "query_database")
    result = json.loads(_invoke(q, sql_query="SELECT a FROM uploaded_data"))
    assert "error" not in result
    assert result["row_count"] == 2


async def test_get_table_schema_with_alias_routes_to_aux_source(tmp_data_dir):
    await _upload("a,b\n1,2\n", "ds-prim")
    await _upload("c,d\n3,4\n", "ds-aux")
    tool_list = agent_tools.build_tools(["ds-prim", "ds-aux"])
    schema = _find(tool_list, "get_table_schema")
    result = json.loads(_invoke(schema, table_name="ds_1.uploaded_data"))
    assert result["table"] == "uploaded_data"
    assert result["source_id"] == "ds-aux"


async def test_get_table_schema_rejects_unknown_alias(tmp_data_dir):
    await _upload("a,b\n1,2\n", "ds-prim-2")
    tool_list = agent_tools.build_tools(["ds-prim-2"])
    schema = _find(tool_list, "get_table_schema")
    result = json.loads(_invoke(schema, table_name="ds_99.uploaded_data"))
    assert "error" in result
