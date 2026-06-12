"""Tests for data_service: upload, parsing, and data source lifecycle."""

from __future__ import annotations

import io

import pandas as pd
import pytest
from app.services import data_service
from app.utils.database import get_engine


def _csv_bytes(rows: list[dict]) -> bytes:
    df = pd.DataFrame(rows)
    return df.to_csv(index=False).encode("utf-8")


async def test_save_csv_round_trip(tmp_data_dir, make_upload):
    """CSV upload creates a SQLite db that we can query back."""
    upload = make_upload("sales.csv", _csv_bytes([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]))
    out = await data_service.save_uploaded_file(upload, "ds-1")

    assert out.endswith("ds-1.db")
    assert (tmp_data_dir / "uploads" / "ds-1.csv").exists()

    engine = get_engine("ds-1")
    with engine.connect() as conn:
        rows = list(conn.exec_driver_sql("SELECT a, b FROM uploaded_data ORDER BY a"))
    assert rows == [(1, "x"), (2, "y")]


async def test_save_xlsx_round_trip(tmp_data_dir, make_upload):
    """Excel upload (.xlsx) goes through openpyxl and lands in SQLite."""
    df = pd.DataFrame([{"k": "alpha", "v": 10}, {"k": "beta", "v": 20}])
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    upload = make_upload("data.xlsx", buf.getvalue())

    await data_service.save_uploaded_file(upload, "ds-2")

    engine = get_engine("ds-2")
    with engine.connect() as conn:
        rows = list(conn.exec_driver_sql("SELECT k, v FROM uploaded_data ORDER BY v"))
    assert rows == [("alpha", 10), ("beta", 20)]


async def test_save_rejects_oversize(tmp_data_dir, make_upload):
    """A file larger than MAX_FILE_BYTES is rejected and cleaned up."""
    big_value = b"x" * (data_service.MAX_FILE_BYTES + 1024)
    upload = make_upload("big.csv", b"a\n" + big_value + b"\n")

    with pytest.raises(ValueError, match="File too large"):
        await data_service.save_uploaded_file(upload, "ds-big")

    assert not (tmp_data_dir / "uploads" / "ds-big.csv").exists()
    assert not (tmp_data_dir / "sqlite" / "ds-big.db").exists()


async def test_save_rejects_unsupported_extension(tmp_data_dir, make_upload):
    upload = make_upload("data.txt", b"hello")
    with pytest.raises(ValueError, match="Unsupported file type"):
        await data_service.save_uploaded_file(upload, "ds-bad")


async def test_save_rejects_empty_file(tmp_data_dir, make_upload):
    upload = make_upload("empty.csv", b"")
    with pytest.raises(ValueError, match="(no rows|parse)"):
        await data_service.save_uploaded_file(upload, "ds-empty")


async def test_save_rejects_malformed_csv(tmp_data_dir, make_upload):
    """Garbage bytes that pandas can't parse raise a clean ValueError."""
    upload = make_upload("bad.csv", b"\x00\x01\x02not a csv at all")
    with pytest.raises(ValueError, match="(parse|no rows)"):
        await data_service.save_uploaded_file(upload, "ds-bad-csv")
    assert not (tmp_data_dir / "uploads" / "ds-bad-csv.csv").exists()


async def test_save_normalizes_duplicate_columns(tmp_data_dir, make_upload):
    """Duplicate column names get suffix _2, _3, ..."""
    upload = make_upload("dup.csv", b"a,a,a\n1,2,3\n")
    await data_service.save_uploaded_file(upload, "ds-dup")

    engine = get_engine("ds-dup")
    with engine.connect() as conn:
        cols = list(conn.exec_driver_sql("SELECT * FROM uploaded_data").keys())
    assert "a" in cols
    assert len(set(cols)) == 3, f"expected 3 distinct columns, got {cols}"


async def test_get_sample_rows(tmp_data_dir, make_upload):
    upload = make_upload("s.csv", _csv_bytes([{"v": i} for i in range(20)]))
    await data_service.save_uploaded_file(upload, "ds-sample")

    rows = data_service.get_sample_rows("ds-sample", limit=3)
    assert rows is not None
    assert len(rows) == 3
    assert rows[0] == {"v": 0}


async def test_delete_data_source_removes_artifacts(tmp_data_dir, make_upload):
    upload = make_upload("del.csv", _csv_bytes([{"x": 1}]))
    await data_service.save_uploaded_file(upload, "ds-del")

    upload_file = tmp_data_dir / "uploads" / "ds-del.csv"
    db_file = tmp_data_dir / "sqlite" / "ds-del.db"
    assert upload_file.exists() and db_file.exists()

    assert data_service.delete_data_source("ds-del") is True
    assert not upload_file.exists()
    assert not db_file.exists()

    # Second call is a no-op and returns False.
    assert data_service.delete_data_source("ds-del") is False
