"""Phase 6: multi-source ATTACH + _resolve_source_table + list_sessions tests."""

from __future__ import annotations

import pandas as pd
import pytest
from app.agents.tools import _resolve_source_table
from app.services import session_service

# ---------------------------------------------------------------------------
# _resolve_source_table — pure function, fast
# ---------------------------------------------------------------------------


def test_resolve_bare_name_uses_primary():
    assert _resolve_source_table("orders", "ds-primary", []) == ("ds-primary", "orders")


def test_resolve_main_prefix_uses_primary():
    assert _resolve_source_table("main.orders", "ds-primary", []) == ("ds-primary", "orders")


def test_resolve_ds_0_alias_uses_primary():
    # ds_0 is the legacy primary alias — same as "main".
    assert _resolve_source_table("ds_0.orders", "ds-primary", []) == ("ds-primary", "orders")


def test_resolve_ds_1_alias_uses_first_aux():
    aux = [("ds_1", "aux-a"), ("ds_2", "aux-b")]
    assert _resolve_source_table("ds_1.orders", "ds-primary", aux) == ("aux-a", "orders")


def test_resolve_ds_2_alias_uses_second_aux():
    aux = [("ds_1", "aux-a"), ("ds_2", "aux-b")]
    assert _resolve_source_table("ds_2.orders", "ds-primary", aux) == ("aux-b", "orders")


def test_resolve_unknown_prefix_returns_none():
    aux = [("ds_1", "aux-a")]
    assert _resolve_source_table("ds_9.orders", "ds-primary", aux) is None
    assert _resolve_source_table("weird.orders", "ds-primary", aux) is None


def test_resolve_empty_returns_none():
    assert _resolve_source_table("", "ds-primary", []) is None


def test_resolve_with_no_primary_returns_none():
    # No primary bound — bare name can't resolve.
    assert _resolve_source_table("orders", None, [("ds_1", "aux-a")]) is None


# ---------------------------------------------------------------------------
# Cross-source JOIN via query_database
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_database_cross_source_join(tmp_data_dir, make_upload):
    """Build two data sources, ATTACH the second, JOIN across both."""
    # Primary: customers
    cust_df = pd.DataFrame({"customer_id": [1, 2, 3], "name": ["Alice", "Bob", "Carol"]})
    # Aux: orders
    ord_df = pd.DataFrame(
        {"order_id": [10, 20, 30], "customer_id": [1, 2, 1], "amount": [100, 200, 50]}
    )

    # Save primary directly via the loader (bypass UploadFile).
    from app.utils import database

    primary_id = "ds-cust"
    aux_id = "ds-ord"
    # Build the SQLite files by writing DataFrames directly.
    primary_engine = database.get_engine(primary_id)
    cust_df.to_sql("customers", primary_engine, if_exists="replace", index=False)
    aux_engine = database.get_engine(aux_id)
    ord_df.to_sql("orders", aux_engine, if_exists="replace", index=False)

    from app.agents.tools import build_tools

    tools = build_tools([primary_id, aux_id], owner_id="user-1")
    query_db = next(t for t in tools if t.name == "query_database")
    # Cross-source JOIN — primary bare name, aux via ds_1.<table>.
    sql = (
        "SELECT c.name, SUM(o.amount) AS total "
        "FROM customers c "
        "JOIN ds_1.orders o ON c.customer_id = o.customer_id "
        "GROUP BY c.name ORDER BY total DESC"
    )
    import json

    raw = await query_db.ainvoke({"sql_query": sql})
    payload = json.loads(raw)
    assert "error" not in payload, payload
    assert payload["row_count"] == 3
    names = [r["name"] for r in payload["rows"]]
    assert names == ["Alice", "Bob", "Carol"]
    # Alice should have the highest total (100 + 50 = 150).
    assert payload["rows"][0]["name"] == "Alice"


# ---------------------------------------------------------------------------
# list_sessions_for_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_returns_only_owned(fake_redis):
    a = await session_service.create_session(owner_id="user-A")
    b = await session_service.create_session(owner_id="user-A")
    c = await session_service.create_session(owner_id="user-B")
    out = await session_service.list_sessions_for_user("user-A")
    ids = {s["session_id"] for s in out}
    assert ids == {a, b}
    assert c not in ids


@pytest.mark.asyncio
async def test_list_sessions_prunes_expired(fake_redis):
    sid = await session_service.create_session(owner_id="user-A")
    # Simulate TTL expiry by deleting the session key but leaving the index.
    from app.services import session_service as ss

    await ss._get_redis().delete(ss._key(sid))
    out = await ss.list_sessions_for_user("user-A")
    assert out == []
    # The orphan id should have been SREM'd.
    remaining = await ss._get_redis().smembers(ss._user_index_key("user-A"))
    assert remaining == set()


@pytest.mark.asyncio
async def test_delete_session_for_user_removes_from_index(fake_redis):
    sid = await session_service.create_session(owner_id="user-A")
    await session_service.delete_session_for_user(sid, "user-A")
    remaining = await session_service.list_sessions_for_user("user-A")
    assert remaining == []
