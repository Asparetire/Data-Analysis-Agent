"""Phase 4D: 服务端分页 + 表浏览器 测试。

覆盖 data_service.fetch_rows 的列校验、排序、分页边界，以及缺表返回 None。
SQL 注入尝试（``sort`` 含分号、``table`` 是 ``"x"; DROP TABLE y; --``）
必须被 PRAGMA 校验拦下，且不会破坏其它表。
"""

from __future__ import annotations

import pandas as pd
from app.services import data_service
from app.utils.database import get_engine


def _csv_bytes(rows: list[dict]) -> bytes:
    return pd.DataFrame(rows).to_csv(index=False).encode("utf-8")


async def _seed(tmp_data_dir, make_upload, ds_id: str = "ds-4d") -> None:
    rows = [{"id": i, "name": f"row{i}", "amount": i * 10} for i in range(1, 26)]
    upload = make_upload("data.csv", _csv_bytes(rows))
    await data_service.save_uploaded_file(upload, ds_id)


async def test_fetch_rows_basic_pagination(tmp_data_dir, make_upload):
    await _seed(tmp_data_dir, make_upload)
    page1 = data_service.fetch_rows("ds-4d", table="uploaded_data", offset=0, limit=10)
    page2 = data_service.fetch_rows("ds-4d", table="uploaded_data", offset=10, limit=10)
    assert page1 is not None and page2 is not None
    assert page1["total"] == 25
    assert len(page1["rows"]) == 10
    assert len(page2["rows"]) == 10
    # No overlap between pages.
    ids1 = {r["id"] for r in page1["rows"]}
    ids2 = {r["id"] for r in page2["rows"]}
    assert ids1.isdisjoint(ids2)


async def test_fetch_rows_sort_asc_and_desc(tmp_data_dir, make_upload):
    await _seed(tmp_data_dir, make_upload)
    asc = data_service.fetch_rows(
        "ds-4d", table="uploaded_data", offset=0, limit=5, sort="amount", direction="asc"
    )
    desc = data_service.fetch_rows(
        "ds-4d", table="uploaded_data", offset=0, limit=5, sort="amount", direction="desc"
    )
    assert asc is not None and desc is not None
    assert [r["amount"] for r in asc["rows"]] == [10, 20, 30, 40, 50]
    assert [r["amount"] for r in desc["rows"]] == [250, 240, 230, 220, 210]


async def test_fetch_rows_invalid_direction_falls_back_to_asc(tmp_data_dir, make_upload):
    await _seed(tmp_data_dir, make_upload)
    out = data_service.fetch_rows("ds-4d", table="uploaded_data", sort="id", direction="sideways")
    assert out is not None
    assert [r["id"] for r in out["rows"]] == sorted(r["id"] for r in out["rows"])


async def test_fetch_rows_unknown_sort_column_ignored(tmp_data_dir, make_upload):
    """A sort column that doesn't exist on the table falls back to rowid order —
    we never interpolate the bad name into SQL."""
    await _seed(tmp_data_dir, make_upload)
    out = data_service.fetch_rows(
        "ds-4d", table="uploaded_data", sort="totally_fake_column", direction="asc"
    )
    assert out is not None
    # Still returns rows, just in natural rowid order.
    assert len(out["rows"]) == 20  # default limit
    ids = [r["id"] for r in out["rows"]]
    assert ids == list(range(1, 21))


async def test_fetch_rows_limit_clamped(tmp_data_dir, make_upload):
    await _seed(tmp_data_dir, make_upload)
    huge = data_service.fetch_rows("ds-4d", table="uploaded_data", offset=0, limit=99999)
    tiny = data_service.fetch_rows("ds-4d", table="uploaded_data", offset=0, limit=0)
    assert huge is not None and tiny is not None
    assert huge["limit"] == 200  # hard cap
    assert tiny["limit"] == 1  # floor


async def test_fetch_rows_offset_clamped_negative(tmp_data_dir, make_upload):
    await _seed(tmp_data_dir, make_upload)
    out = data_service.fetch_rows("ds-4d", table="uploaded_data", offset=-50, limit=5)
    assert out is not None
    assert out["offset"] == 0
    assert [r["id"] for r in out["rows"]] == [1, 2, 3, 4, 5]


async def test_fetch_rows_missing_table_returns_none(tmp_data_dir, make_upload):
    await _seed(tmp_data_dir, make_upload)
    out = data_service.fetch_rows("ds-4d", table="does_not_exist")
    assert out is None


async def test_fetch_rows_rejects_sql_injection_in_sort(tmp_data_dir, make_upload):
    """The sort column passes through PRAGMA validation — anything not in
    table_info is ignored, so an injection payload can't reach SQL."""
    await _seed(tmp_data_dir, make_upload)
    out = data_service.fetch_rows(
        "ds-4d",
        table="uploaded_data",
        sort='id"; DROP TABLE uploaded_data; --',
        direction="asc",
    )
    assert out is not None
    # Table still exists after the call.
    assert data_service.get_table_info("ds-4d", "uploaded_data") is not None


async def test_fetch_rows_rejects_sql_injection_in_table(tmp_data_dir, make_upload):
    """A malicious table name is rejected by PRAGMA — the lookup returns no
    rows, so fetch_rows returns None instead of executing the payload."""
    await _seed(tmp_data_dir, make_upload)
    out = data_service.fetch_rows("ds-4d", table='uploaded_data"; DROP TABLE uploaded_data; --')
    assert out is None
    # Table is intact.
    assert data_service.get_table_info("ds-4d", "uploaded_data") is not None


async def test_list_tables_excludes_sqlite_system_tables(tmp_data_dir, make_upload):
    """sqlite_sequence and friends must not show up in the browser list."""
    await _seed(tmp_data_dir, make_upload)
    # Force-create sqlite_sequence by inserting into an AUTOINCREMENT table.
    engine = get_engine("ds-4d")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE autoinc (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
        conn.exec_driver_sql("INSERT INTO autoinc (v) VALUES ('x')")
    names = data_service.list_tables("ds-4d")
    assert "uploaded_data" in names
    assert "autoinc" in names
    assert not any(n.startswith("sqlite_") for n in names)
