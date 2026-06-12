"""Tests for the SQL safety guard in agents/tools.py."""

from __future__ import annotations

import pytest
from app.agents.tools import _is_safe_select


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM uploaded_data",
        "select * from uploaded_data",
        "WITH t AS (SELECT 1) SELECT * FROM t",
        "  SELECT  *  FROM uploaded_data  ",
        'SELECT "Order ID", amount FROM orders',
    ],
)
def test_safe_queries_accepted(sql: str):
    assert _is_safe_select(sql) is None


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET x = 1",
        "DELETE FROM t",
        "DROP TABLE t",
        "ALTER TABLE t ADD COLUMN x INT",
        "CREATE TABLE t (x INT)",
        "REPLACE INTO t VALUES (1)",
        "ATTACH DATABASE 'x' AS aux",
        "DETACH DATABASE aux",
        "PRAGMA writable_schema = 1",
        "VACUUM",
        "REINDEX",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "GRANT ALL ON t TO public",
        "REVOKE ALL ON t FROM public",
    ],
)
def test_forbidden_keywords_rejected(sql: str):
    err = _is_safe_select(sql)
    assert err is not None
    assert "forbidden" in err.lower()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE t",
        "SELECT `col` FROM t",
    ],
)
def test_forbidden_chars_rejected(sql: str):
    err = _is_safe_select(sql)
    assert err is not None
    assert "forbidden" in err.lower()


def test_non_select_prefix_rejected():
    """Anything not starting with SELECT or WITH is rejected up front."""
    err = _is_safe_select("EXPLAIN SELECT 1")
    assert err is not None
    assert "SELECT" in err


def test_empty_sql_rejected():
    assert _is_safe_select("") is not None
    assert _is_safe_select("   ") is not None
