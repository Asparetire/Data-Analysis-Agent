"""Tests for Phase 3B Agent smart upgrade: list_tables, get_table_schema, get_sample_rows, big-file sampling."""

from __future__ import annotations

import json

from app.agents import tools as agent_tools
from app.services import data_service


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


# ---------------------------------------------------------------------------
# list_tables
# ---------------------------------------------------------------------------


async def test_list_tables_returns_summaries(tmp_data_dir):
    await _upload("category,amount,note\nA,10,x\nB,20,y\nA,30,z\nB,40,q\n", "ds-tools")
    tool_list = agent_tools.build_tools("ds-tools")
    list_tables = _find(tool_list, "list_tables")
    result = json.loads(_invoke(list_tables))
    assert "tables" in result
    assert len(result["tables"]) == 1
    summary = result["tables"][0]
    assert summary["table"] == "uploaded_data"
    assert summary["row_count"] == 4
    col_names = [c["name"] for c in summary["columns"]]
    assert col_names == ["category", "amount", "note"]


async def test_list_tables_with_no_data_source(tmp_data_dir):
    tool_list = agent_tools.build_tools(None)
    list_tables = _find(tool_list, "list_tables")
    result = json.loads(_invoke(list_tables))
    assert result.get("error") or result.get("tables") == []


# ---------------------------------------------------------------------------
# get_table_schema with description / unit / sample injection
# ---------------------------------------------------------------------------


async def test_get_table_schema_includes_descriptions_and_samples(tmp_data_dir):
    from app.services import metadata_service

    await _upload("category,amount,note\nA,10,x\nB,20,y\n", "ds-tools-2")
    metadata_service.set_table_metadata(
        "ds-tools-2",
        "uploaded_data",
        columns={
            "amount": {
                "type": "currency",
                "description": "销售额",
                "unit": "CNY",
                "sample": [10, 20, 30],
            }
        },
    )
    tool_list = agent_tools.build_tools("ds-tools-2")
    schema = _find(tool_list, "get_table_schema")
    result = json.loads(_invoke(schema))
    assert result["table"] == "uploaded_data"
    amount_col = next(c for c in result["columns"] if c["name"] == "amount")
    assert amount_col["description"] == "销售额"
    assert amount_col["unit"] == "CNY"
    assert amount_col["sample"] == [10, 20, 30]


async def test_get_table_schema_accepts_explicit_table_name(tmp_data_dir):
    await _upload("a,b\n1,2\n", "ds-tools-3")
    tool_list = agent_tools.build_tools("ds-tools-3")
    schema = _find(tool_list, "get_table_schema")
    result = json.loads(_invoke(schema, table_name="uploaded_data"))
    assert result["table"] == "uploaded_data"


async def test_get_table_schema_returns_error_for_missing_table(tmp_data_dir):
    await _upload("a,b\n1,2\n", "ds-tools-4")
    tool_list = agent_tools.build_tools("ds-tools-4")
    schema = _find(tool_list, "get_table_schema")
    result = json.loads(_invoke(schema, table_name="ghost"))
    assert "error" in result


# ---------------------------------------------------------------------------
# get_sample_rows with big-file sampling shrink
# ---------------------------------------------------------------------------


async def test_get_sample_rows_default_is_5(tmp_data_dir):
    await _upload("a,b\n" + "\n".join(f"{i},x" for i in range(20)), "ds-sr-1")
    tool_list = agent_tools.build_tools("ds-sr-1")
    sample = _find(tool_list, "get_sample_rows")
    result = json.loads(_invoke(sample))
    assert result["row_count"] == 5


async def test_get_sample_rows_respects_explicit_limit(tmp_data_dir):
    await _upload("a,b\n" + "\n".join(f"{i},x" for i in range(20)), "ds-sr-2")
    tool_list = agent_tools.build_tools("ds-sr-2")
    sample = _find(tool_list, "get_sample_rows")
    result = json.loads(_invoke(sample, limit=2))
    assert result["row_count"] == 2


async def test_get_sample_rows_caps_at_100(tmp_data_dir):
    await _upload("a,b\n1,2\n3,4\n", "ds-sr-3")
    tool_list = agent_tools.build_tools("ds-sr-3")
    sample = _find(tool_list, "get_sample_rows")
    # Asked for 9999, capped at 100 (and we only have 2 rows).
    result = json.loads(_invoke(sample, limit=9999))
    assert result["row_count"] == 2


async def test_get_sample_rows_shrinks_for_large_tables(tmp_data_dir):
    """Tables over 50k rows get their default sample capped at 5."""
    rows = "\n".join(f"A,{i},n" for i in range(60_000))
    csv = "category,id,note\n" + rows
    await _upload(csv, "ds-sr-big", filename="big.csv")
    tool_list = agent_tools.build_tools("ds-sr-big")
    sample = _find(tool_list, "get_sample_rows")
    # Asked for 50, big-table shrink caps at 5.
    result = json.loads(_invoke(sample, limit=50))
    assert result["row_count"] == 5


# ---------------------------------------------------------------------------
# _format_schema_for_prompt helper
# ---------------------------------------------------------------------------


def test_format_schema_for_prompt_includes_description_unit_sample():
    info = {
        "table": "orders",
        "row_count": 100,
        "columns": [
            {"name": "id", "type": "integer", "nullable": False},
            {
                "name": "amount",
                "type": "currency",
                "nullable": True,
                "description": "销售额",
                "unit": "CNY",
                "sample": [10, 20, 30],
            },
        ],
    }
    out = agent_tools._format_schema_for_prompt(info)
    assert "orders" in out
    assert "id (integer, not null)" in out
    assert "amount (currency, null, unit: CNY) -- 销售额" in out
    assert "examples: 10, 20, 30" in out
