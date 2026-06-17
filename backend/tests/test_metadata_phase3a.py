"""Tests for Phase 3A additions to the metadata sidecar: nested schema + legacy migration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.services import metadata_service


@pytest.fixture(autouse=True)
def isolated_meta(monkeypatch, tmp_path):
    monkeypatch.setattr(metadata_service.settings, "DATA_DIR", str(tmp_path))
    yield


def test_set_and_get_table_metadata_round_trips():
    metadata_service.set_table_metadata(
        "ds-1",
        "orders",
        columns={
            "id": {"type": "integer", "description": "订单号", "unit": None, "sample": [1, 2]},
            "amount": {"type": "currency", "description": "金额", "unit": "CNY", "sample": [10.0]},
        },
        indexes=["id"],
    )
    meta = metadata_service.get_table_metadata("ds-1", "orders")
    assert meta is not None
    assert meta["indexes"] == ["id"]
    assert meta["columns"]["amount"]["unit"] == "CNY"
    assert meta["columns"]["id"]["type"] == "integer"


def test_set_table_metadata_merges_columns_by_default():
    """Calling set_table_metadata twice with the same col updates fields, doesn't drop others."""
    metadata_service.set_table_metadata(
        "ds-1", "orders", columns={"id": {"type": "integer", "description": "订单号"}}
    )
    metadata_service.set_table_metadata("ds-1", "orders", columns={"id": {"description": "新描述"}})
    meta = metadata_service.get_table_metadata("ds-1", "orders")
    # The type from the first call is preserved; description was overwritten.
    assert meta["columns"]["id"]["type"] == "integer"
    assert meta["columns"]["id"]["description"] == "新描述"


def test_set_table_metadata_replace_columns_drops_existing():
    """replace_columns=True wipes the previous columns dict first."""
    metadata_service.set_table_metadata(
        "ds-1", "orders", columns={"a": {"type": "string"}, "b": {"type": "string"}}
    )
    metadata_service.set_table_metadata(
        "ds-1",
        "orders",
        columns={"c": {"type": "integer"}},
        replace_columns=True,
    )
    meta = metadata_service.get_table_metadata("ds-1", "orders")
    assert set(meta["columns"].keys()) == {"c"}


def test_set_table_metadata_creates_distinct_tables():
    metadata_service.set_table_metadata("ds-1", "orders", indexes=["id"])
    metadata_service.set_table_metadata("ds-1", "customers", indexes=["id"])

    tables = metadata_service.get_all_tables("ds-1")
    assert set(tables.keys()) == {"orders", "customers"}


def test_get_table_metadata_returns_none_for_unknown_table():
    assert metadata_service.get_table_metadata("ds-x", "missing") is None


def test_source_type_round_trips():
    metadata_service.set_source_type("ds-1", "sqlite")
    assert metadata_service.get_source_type("ds-1") == "sqlite"


def test_source_type_defaults_to_unknown():
    assert metadata_service.get_source_type("never-set") == "unknown"


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------


def test_legacy_entry_migrates_on_read(tmp_path):
    """A pre-3A entry with only display_name loads with the new fields filled in."""
    sidecar = Path(tmp_path) / "datasources.json"
    sidecar.write_text(
        json.dumps({"ds-old": {"display_name": "Q1 销售"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    # Wipe in-memory cache by re-importing? No -- the module reads on every call.
    assert metadata_service.get_display_name("ds-old") == "Q1 销售"
    tables = metadata_service.get_all_tables("ds-old")
    # Old entries have no tables key, but the loader fills it in to an empty dict.
    assert tables == {}


def test_legacy_entry_gets_source_type_default():
    """A pre-3A entry's source_type defaults to ``unknown`` after load."""
    sidecar = Path(metadata_service.settings.DATA_DIR) / "datasources.json"
    sidecar.write_text(
        json.dumps({"ds-old2": {"display_name": "X"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert metadata_service.get_source_type("ds-old2") == "unknown"


def test_writing_legacy_then_setting_table_metadata_preserves_display_name():
    """Set display name, then set table metadata -- display name must survive."""
    metadata_service.set_display_name("ds-x", "Quarterly Sales")
    metadata_service.set_table_metadata("ds-x", "orders", indexes=["id"])
    assert metadata_service.get_display_name("ds-x") == "Quarterly Sales"


# ---------------------------------------------------------------------------
# delete_entry cleans up the new fields too
# ---------------------------------------------------------------------------


def test_delete_entry_removes_table_metadata():
    metadata_service.set_table_metadata("ds-1", "orders", indexes=["id"])
    assert metadata_service.get_table_metadata("ds-1", "orders") is not None
    metadata_service.delete_entry("ds-1")
    assert metadata_service.get_table_metadata("ds-1", "orders") is None
