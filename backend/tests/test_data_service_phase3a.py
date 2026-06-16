"""Tests for Phase 3A additions: multi-sheet parsing, type inference, indexes."""

from __future__ import annotations

import io

import pandas as pd
from app.services import data_service, metadata_service
from app.utils.database import get_engine


def _xlsx_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Multi-sheet Excel
# ---------------------------------------------------------------------------


async def test_xlsx_multisheet_creates_multiple_tables(tmp_data_dir, make_upload):
    """An xlsx with two sheets lands in two tables, named by sheet."""
    payload = _xlsx_bytes(
        {
            "Orders": pd.DataFrame([{"id": 1, "amt": 10.0}, {"id": 2, "amt": 20.0}]),
            "Customers": pd.DataFrame([{"name": "A"}, {"name": "B"}]),
        }
    )
    upload = make_upload("multi.xlsx", payload)
    await data_service.save_uploaded_file(upload, "ds-multi")

    engine = get_engine("ds-multi")
    with engine.connect() as conn:
        tables = sorted(
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            if not r[0].startswith("sqlite_")
        )
    assert tables == ["Customers", "Orders"]


async def test_xlsx_sheet_name_is_sanitized(tmp_data_dir, make_upload):
    """Sheet names with spaces / non-ascii become SQL-safe identifiers."""
    payload = _xlsx_bytes(
        {
            "Q1 销售": pd.DataFrame([{"x": 1}]),
            "2026-raw": pd.DataFrame([{"x": 2}]),
        }
    )
    upload = make_upload("cn.xlsx", payload)
    await data_service.save_uploaded_file(upload, "ds-cn")

    engine = get_engine("ds-cn")
    with engine.connect() as conn:
        names = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert any(n.startswith("Q1_") for n in names)
    assert any(n.startswith("2026_raw") or n.startswith("sheet_2026") for n in names)


async def test_xlsx_single_sheet_keeps_default_table_name(tmp_data_dir, make_upload):
    """A single-sheet xlsx still uses the legacy ``uploaded_data`` table name.

    This is the backward-compat contract: the LLM's existing prompt and the
    pre-3A test suite both assume this name for the one-table case.
    """
    payload = _xlsx_bytes({"Anything": pd.DataFrame([{"x": 1}])})
    upload = make_upload("single.xlsx", payload)
    await data_service.save_uploaded_file(upload, "ds-single")

    engine = get_engine("ds-single")
    with engine.connect() as conn:
        names = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            if not r[0].startswith("sqlite_")
        }
    assert names == {"uploaded_data"}


# ---------------------------------------------------------------------------
# Type inference
# ---------------------------------------------------------------------------


def test_infer_column_type_handles_currency_strings():
    s = pd.Series(["$1,234.50", "¥99", "$10", "(500)"])
    assert data_service.infer_column_type(s) == "currency"


def test_infer_column_type_handles_datetime():
    s = pd.to_datetime(pd.Series(["2026-01-01", "2026-02-01", "2026-03-01"]))
    assert data_service.infer_column_type(s) == "datetime"


def test_infer_column_type_handles_integer():
    s = pd.Series([1, 2, 3, 4], dtype="int64")
    assert data_service.infer_column_type(s) == "integer"


def test_infer_column_type_handles_float():
    s = pd.Series([1.5, 2.5, 3.5], dtype="float64")
    assert data_service.infer_column_type(s) == "number"


def test_infer_column_type_handles_boolean():
    s = pd.Series([True, False, True])
    assert data_service.infer_column_type(s) == "boolean"


def test_infer_column_type_falls_back_to_string():
    s = pd.Series(["alpha", "beta", "gamma"])
    assert data_service.infer_column_type(s) == "string"


def test_infer_column_type_all_null_is_string():
    s = pd.Series([None, None, None], dtype=object)
    assert data_service.infer_column_type(s) == "string"


def test_infer_column_type_currency_rejects_non_money_strings():
    s = pd.Series(["$not money", "100%"])
    # Not all entries match the currency regex -> falls back to string.
    assert data_service.infer_column_type(s) == "string"


# ---------------------------------------------------------------------------
# Auto-indexing
# ---------------------------------------------------------------------------


async def test_unique_id_column_gets_indexed(tmp_data_dir, make_upload):
    """A column with unique values (typical ID) gets an index."""
    csv = "id,label\n" + "\n".join(f"{i},row_{i}" for i in range(50))
    upload = make_upload("ids.csv", csv.encode())
    await data_service.save_uploaded_file(upload, "ds-ids")

    engine = get_engine("ds-ids")
    with engine.connect() as conn:
        idxs = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
    assert any("id" in idx for idx in idxs)


async def test_low_cardinality_column_is_not_indexed(tmp_data_dir, make_upload):
    """A column with few unique values does NOT get an index."""
    csv = "category,value\n" + "\n".join(f"cat_{i%3},{i}" for i in range(60))
    upload = make_upload("cats.csv", csv.encode())
    await data_service.save_uploaded_file(upload, "ds-cats")

    engine = get_engine("ds-cats")
    with engine.connect() as conn:
        idxs = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
    # category has only 3 distinct values, well below the 0.95 ratio.
    assert not any("category" in idx for idx in idxs)


async def test_metadata_records_indexed_columns(tmp_data_dir, make_upload):
    """Indexed columns are recorded in the sidecar's indexes list."""
    csv = "id,name\n" + "\n".join(f"{i},n_{i}" for i in range(50))
    upload = make_upload("ids2.csv", csv.encode())
    await data_service.save_uploaded_file(upload, "ds-idx-meta")

    meta = metadata_service.get_table_metadata("ds-idx-meta", "uploaded_data")
    assert meta is not None
    assert "id" in meta["indexes"]


async def test_metadata_records_currency_column_type(tmp_data_dir, make_upload):
    """A currency-typed column is recorded as such in the sidecar."""
    csv = "amount,note\n$10,a\n$20,b\n$30,c\n"
    upload = make_upload("money.csv", csv.encode())
    await data_service.save_uploaded_file(upload, "ds-money")

    meta = metadata_service.get_table_metadata("ds-money", "uploaded_data")
    assert meta is not None
    assert meta["columns"]["amount"]["type"] == "currency"
    assert meta["columns"]["note"]["type"] == "string"


# ---------------------------------------------------------------------------
# Multi-sheet metadata
# ---------------------------------------------------------------------------


async def test_multisheet_metadata_records_all_tables(tmp_data_dir, make_upload):
    """Every sheet gets its own entry in the sidecar's tables dict."""
    payload = _xlsx_bytes(
        {
            "Sales": pd.DataFrame([{"id": 1, "amount": 10.0}]),
            "Refunds": pd.DataFrame([{"id": 2, "amount": -5.0}]),
        }
    )
    upload = make_upload("two.xlsx", payload)
    await data_service.save_uploaded_file(upload, "ds-two")

    tables = metadata_service.get_all_tables("ds-two")
    assert set(tables.keys()) == {"Sales", "Refunds"}


# ---------------------------------------------------------------------------
# get_table_info
# ---------------------------------------------------------------------------


async def test_get_table_info_uses_sidecar_types(tmp_data_dir, make_upload):
    """The richer sidecar types win over PRAGMA's coarse ones."""
    csv = "amt\n$1\n$2\n$3\n"
    upload = make_upload("info.csv", csv.encode())
    await data_service.save_uploaded_file(upload, "ds-info")

    info = data_service.get_table_info("ds-info")
    assert info is not None
    col_types = {c["name"]: c["type"] for c in info["columns"]}
    assert col_types["amt"] == "currency"
