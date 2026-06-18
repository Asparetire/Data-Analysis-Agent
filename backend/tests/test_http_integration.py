"""Phase 4 HTTP-layer integration tests.

These exercise the FastAPI app end-to-end through TestClient — the glue
between auth middleware, rate limiting, ACL checks, and the route handlers
that the unit tests don't reach. We don't hit a real LLM: ``LLM_MOCK=1``
makes the agent use ``MockChatModel`` so /chat/stream returns a fixed
answer.

Fixtures:
- ``client``: a TestClient bound to the real ``app``. Each test gets a
  fresh app instance so middleware state never leaks.
- ``fake_redis`` (from conftest): the session/refresh-token store.
- ``tmp_data_dir`` (from conftest): isolates uploads + sqlite files.

Tests cover the paths most likely to silently break:
- register → /auth/me → login → refresh rotation → logout revocation
- upload → list datasources → ACL (other user gets 404) → delete
- /chat/stream happy path with mock LLM
- rate-limit 429 on /auth/login after exceeding the per-IP window
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client(fake_redis, users_db) -> Iterator[TestClient]:
    # TestClient with raise_server_exceptions=True (default) surfaces 500s
    # as exceptions so we see the real traceback instead of just the status.
    # users_db (not tmp_data_dir) is what redirects DATABASE_URL so registered
    # users don't leak between tests via the shared main.db.
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, email: str, password: str = "test-password-123") -> dict:
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _auth_headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _make_csv(name: str, rows: str) -> tuple[str, bytes, str]:
    return name, rows.encode("utf-8"), "text/csv"


# ---------------------------------------------------------------------------
# Auth flow
# ---------------------------------------------------------------------------


def test_register_returns_token_pair_and_user_view(client):
    tokens = _register(client, "alice@example.com")
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"]
    assert tokens["refresh_token"]
    assert tokens["expires_in"] > 0

    me = client.get("/api/v1/auth/me", headers=_auth_headers(tokens))
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "alice@example.com"
    assert body["is_active"] is True


def test_me_rejects_missing_token(client):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers


def test_login_with_correct_password(client):
    _register(client, "bob@example.com")
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "bob@example.com", "password": "test-password-123"},
    )
    assert resp.status_code == 200
    tokens = resp.json()
    me = client.get("/api/v1/auth/me", headers=_auth_headers(tokens))
    assert me.json()["email"] == "bob@example.com"


def test_login_rejects_wrong_password(client):
    _register(client, "carol@example.com")
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "carol@example.com", "password": "wrong-password-xxx"},
    )
    assert resp.status_code == 401


def test_refresh_rotates_and_revokes_old(client):
    tokens = _register(client, "dave@example.com")
    resp = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert resp.status_code == 200
    new_tokens = resp.json()
    assert new_tokens["access_token"] != tokens["access_token"]

    # Old refresh token is revoked.
    again = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert again.status_code == 401


def test_logout_revokes_refresh_token(client):
    tokens = _register(client, "erin@example.com")
    resp = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert resp.status_code == 204

    # Refresh after logout must fail.
    again = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert again.status_code == 401


# ---------------------------------------------------------------------------
# Upload + ACL
# ---------------------------------------------------------------------------


def _upload(client: TestClient, tokens: dict, filename: str, csv: str) -> str:
    resp = client.post(
        "/api/v1/upload",
        headers=_auth_headers(tokens),
        files={"file": (filename, csv.encode("utf-8"), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["file_id"]


def test_upload_then_list_datasources_only_returns_own(client):
    alice = _register(client, "alice2@example.com")
    bob = _register(client, "bob2@example.com")

    alice_id = _upload(client, alice, "alice_sales.csv", "id,name\n1,alice\n")
    bob_id = _upload(client, bob, "bob_secret.csv", "id,secret\n1,xxx\n")

    alice_list = client.get("/api/v1/datasources", headers=_auth_headers(alice))
    assert alice_list.status_code == 200
    ids = {d["id"] for d in alice_list.json()}
    assert alice_id in ids
    assert bob_id not in ids  # ACL: Bob's data source is invisible to Alice

    bob_list = client.get("/api/v1/datasources", headers=_auth_headers(bob))
    bob_ids = {d["id"] for d in bob_list.json()}
    assert bob_id in bob_ids
    assert alice_id not in bob_ids


def test_other_user_gets_404_on_foreign_datasource(client):
    alice = _register(client, "alice3@example.com")
    bob = _register(client, "bob3@example.com")
    ds_id = _upload(client, alice, "alice_private.csv", "id,name\n1,alice\n")

    # Bob tries to read Alice's data source.
    resp = client.get(
        f"/api/v1/datasources/{ds_id}/preview",
        headers=_auth_headers(bob),
    )
    assert resp.status_code == 404

    # Bob tries to delete Alice's data source.
    resp = client.delete(
        f"/api/v1/datasources/{ds_id}",
        headers=_auth_headers(bob),
    )
    assert resp.status_code == 404


def test_preview_and_schema_return_data(client):
    tokens = _register(client, "frank@example.com")
    ds_id = _upload(client, tokens, "schema_test.csv", "id,name,amount\n1,alice,100\n2,bob,200\n")

    preview = client.get(
        f"/api/v1/datasources/{ds_id}/preview",
        params={"limit": 5},
        headers=_auth_headers(tokens),
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["count"] == 2

    schema = client.get(
        f"/api/v1/datasources/{ds_id}/schema",
        headers=_auth_headers(tokens),
    )
    assert schema.status_code == 200
    cols = {c["name"] for c in schema.json()["schema"]}
    assert cols == {"id", "name", "amount"}


def test_rows_endpoint_paginates_and_masks(client):
    tokens = _register(client, "gina@example.com")
    csv = "id,email,amount\n"
    for i in range(25):
        csv += f"{i},user{i}@example.com,{i * 10}\n"
    ds_id = _upload(client, tokens, "paginated.csv", csv)

    resp = client.get(
        f"/api/v1/datasources/{ds_id}/rows",
        params={"offset": 0, "limit": 10},
        headers=_auth_headers(tokens),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 25
    assert len(body["rows"]) == 10
    # Phase 4C layer 2: emails are masked at the API boundary.
    assert body["rows"][0]["email"] == "***@***.***"


def test_delete_datasource_removes_it_from_list(client):
    tokens = _register(client, "hank@example.com")
    ds_id = _upload(client, tokens, "to_delete.csv", "id,name\n1,x\n")

    resp = client.delete(
        f"/api/v1/datasources/{ds_id}",
        headers=_auth_headers(tokens),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    listing = client.get("/api/v1/datasources", headers=_auth_headers(tokens))
    assert all(d["id"] != ds_id for d in listing.json())


# ---------------------------------------------------------------------------
# Chat (mock LLM)
# ---------------------------------------------------------------------------


def test_chat_stream_returns_mock_answer(client, monkeypatch):
    # Mock the LLM via the LLM_MOCK path. config.py already evaluated at
    # import time, so we set the attribute directly + patch build_graph to
    # route through MockChatModel. The streaming layer imports build_graph
    # lazily, so the patch takes effect.
    from app.agents import graph as graph_mod
    from app.agents.mock_llm import MockChatModel

    monkeypatch.setattr(graph_mod, "_build_llm", lambda temperature=0: MockChatModel())

    tokens = _register(client, "irene@example.com")
    ds_id = _upload(client, tokens, "chat.csv", "id,name\n1,alice\n2,bob\n")

    # Create a session so /chat/stream has a valid session_id.
    sess = client.post("/api/v1/sessions", headers=_auth_headers(tokens))
    assert sess.status_code == 201
    session_id = sess.json()["session_id"]

    with client.stream(
        "POST",
        "/api/v1/chat/stream",
        headers={**_auth_headers(tokens), "Accept": "text/event-stream"},
        json={"session_id": session_id, "message": "总结", "data_source_id": ds_id},
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    # The mock streams "这是 mock 模型的回复。数据看起来没问题。"
    assert "mock" in body
    assert "event: end" in body


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_rate_limit_429_on_login_brute_force(client, monkeypatch):
    # Tighten the limit so the test doesn't burn 20 real requests.
    # 4 (not 3) because the /auth/register call shares the same IP ZSET key
    # and counts against this window — register(1) + 3 logins(2,3,4) must all
    # fit, then the 4th login (5th call) trips the limit.
    from app.config import settings

    monkeypatch.setattr(settings, "RATE_LIMIT_PER_IP_PER_MINUTE", 4)

    _register(client, "target@example.com")

    # First 3 attempts are allowed (whether they succeed or not).
    for _ in range(3):
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "target@example.com", "password": "wrong-password-xxx"},
        )
        assert resp.status_code == 401  # wrong password, but not rate-limited

    # 4th attempt is rate-limited.
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "target@example.com", "password": "wrong-password-xxx"},
    )
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
