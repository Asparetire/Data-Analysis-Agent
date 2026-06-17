"""Phase 4C: SQL 沙箱 + 4 层数据脱敏 测试。"""

from __future__ import annotations

import pandas as pd
from app.utils import pii_mask

# ---------------------------------------------------------------------------
# mask_value: each PII pattern
# ---------------------------------------------------------------------------


def test_mask_value_redacts_email():
    assert pii_mask.mask_value("alice@example.com") == "***@***.***"


def test_mask_value_redacts_phone():
    assert pii_mask.mask_value("13812345678") == "1**********"


def test_mask_value_redacts_id_18():
    assert pii_mask.mask_value("110101199003070123") == "******************"


def test_mask_value_redacts_id_15():
    assert pii_mask.mask_value("110101900307012") == "***************"


def test_mask_value_redacts_bank_card():
    assert pii_mask.mask_value("4111111111111111") == "****"


def test_mask_value_passes_through_none():
    assert pii_mask.mask_value(None) is None


def test_mask_value_passes_through_numbers():
    # ints / floats are not strings — they should be untouched so SQL
    # aggregations on numeric columns still work.
    assert pii_mask.mask_value(42) == 42
    assert pii_mask.mask_value(3.14) == 3.14


def test_mask_value_leaves_short_numbers_alone():
    # 4-digit order id shouldn't be touched.
    assert pii_mask.mask_value("order-1234") == "order-1234"


# ---------------------------------------------------------------------------
# mask_rows + mask_dataframe
# ---------------------------------------------------------------------------


def test_mask_rows_redacts_each_cell():
    rows = [
        {"id": 1, "email": "alice@example.com", "phone": "13812345678"},
        {"id": 2, "email": "bob@x.io", "phone": "13900001111"},
    ]
    out = pii_mask.mask_rows(rows)
    assert out[0]["email"] == "***@***.***"
    assert out[0]["phone"] == "1**********"
    assert out[1]["email"] == "***@***.***"
    # Non-string cells pass through unchanged.
    assert out[0]["id"] == 1


def test_mask_dataframe_preserves_numeric_columns():
    df = pd.DataFrame(
        {
            "email": ["a@x.com", "b@y.com"],
            "amount": [100, 200],
            "id_no": ["110101199003070123", "110101199003070999"],
        }
    )
    out = pii_mask.mask_dataframe(df)
    assert list(out["email"]) == ["***@***.***", "***@***.***"]
    assert list(out["amount"]) == [100, 200]  # numeric column untouched
    assert list(out["id_no"]) == ["******************", "******************"]
    # Original DF is not mutated.
    assert df.loc[0, "email"] == "a@x.com"


def test_mask_dataframe_empty_returns_copy():
    df = pd.DataFrame()
    out = pii_mask.mask_dataframe(df)
    assert out.empty


# ---------------------------------------------------------------------------
# mask_sql_literals: layer 3
# ---------------------------------------------------------------------------


def test_mask_sql_literals_redacts_string_constants():
    sql = "SELECT * FROM users WHERE email = 'alice@example.com' AND phone = '13812345678'"
    out = pii_mask.mask_sql_literals(sql)
    assert "alice@example.com" not in out
    assert "13812345678" not in out
    assert "'***@***.***'" in out
    assert "'1**********'" in out
    # Structure preserved.
    assert out.startswith("SELECT * FROM users WHERE email = ")


def test_mask_sql_literals_handles_escaped_quotes():
    sql = "SELECT 'alice''s email is alice@x.com'"
    out = pii_mask.mask_sql_literals(sql)
    assert "alice@x.com" not in out
    # The literal should still be valid SQL (single quotes balanced).
    assert out.count("'") % 2 == 0


def test_mask_sql_literals_passes_through_without_strings():
    sql = "SELECT id, COUNT(*) FROM t GROUP BY id"
    assert pii_mask.mask_sql_literals(sql) == sql


def test_mask_sql_literals_empty():
    assert pii_mask.mask_sql_literals("") == ""
