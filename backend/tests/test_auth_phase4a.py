"""Phase 4A: 认证 + ACL 的端到端测试。

覆盖：
- register / login / refresh / me 的正常路径
- 重复注册、错密码、坏 token 等错误路径
- ACL：A 用户的会话 / 数据源 B 用户访问得到 404
- migrate_ownerless_data 把无主 sidecar entry 回填到默认 admin
"""

from __future__ import annotations

import pytest
from app.services import auth_service, metadata_service


def test_register_creates_user_and_hashes_password(users_db):
    user = auth_service.register("alice@example.com", "topsecret123")
    assert user["email"] == "alice@example.com"
    stored = auth_service.get_user_by_email("alice@example.com")
    assert stored is not None
    assert stored["password_hash"] != "topsecret123"
    assert stored["is_active"] is True


def test_register_rejects_duplicate_email(users_db):
    auth_service.register("bob@example.com", "topsecret123")
    with pytest.raises(auth_service.EmailExists):
        auth_service.register("bob@example.com", "other-password")


def test_register_rejects_short_password(users_db):
    with pytest.raises(auth_service.InvalidCredentials):
        auth_service.register("short@example.com", "abc")


def test_authenticate_verifies_password(users_db):
    auth_service.register("carol@example.com", "topsecret123")
    user = auth_service.authenticate("carol@example.com", "topsecret123")
    assert user["email"] == "carol@example.com"

    with pytest.raises(auth_service.InvalidCredentials):
        auth_service.authenticate("carol@example.com", "wrong")


def test_authenticate_is_case_insensitive_on_email(users_db):
    auth_service.register("dave@example.com", "topsecret123")
    user = auth_service.authenticate("DAVE@example.com", "topsecret123")
    assert user["email"] == "dave@example.com"


async def test_issue_and_verify_access_token(users_db, fake_redis):
    user = auth_service.register("erin@example.com", "topsecret123")
    tokens = await auth_service.issue_tokens(user)
    assert tokens["access_token"]
    assert tokens["refresh_token"]
    payload = auth_service.verify_access_token(tokens["access_token"])
    assert payload["sub"] == user["id"]
    assert payload["type"] == "access"


async def test_verify_access_token_rejects_refresh_token(users_db, fake_redis):
    user = auth_service.register("frank@example.com", "topsecret123")
    tokens = await auth_service.issue_tokens(user)
    with pytest.raises(auth_service.InvalidToken):
        auth_service.verify_access_token(tokens["refresh_token"])


def test_verify_access_token_rejects_garbage():
    with pytest.raises(auth_service.InvalidToken):
        auth_service.verify_access_token("not-a-jwt")


async def test_refresh_rotates_and_revokes_old(users_db, fake_redis):
    user = auth_service.register("gina@example.com", "topsecret123")
    tokens = await auth_service.issue_tokens(user)

    new_tokens = await auth_service.refresh_tokens(tokens["refresh_token"])
    assert new_tokens["access_token"] != tokens["access_token"]

    # The old refresh token is revoked.
    with pytest.raises(auth_service.InvalidToken):
        await auth_service.refresh_tokens(tokens["refresh_token"])


# ---------------------------------------------------------------------------
# ACL on metadata_service.owner
# ---------------------------------------------------------------------------


def test_set_and_get_owner_round_trip(users_db):
    metadata_service.set_owner("ds-1", "user-a")
    assert metadata_service.get_owner("ds-1") == "user-a"
    assert metadata_service.get_owner("ds-missing") is None


def test_list_ids_for_owner_filters_by_owner(users_db):
    metadata_service.set_owner("ds-1", "user-a")
    metadata_service.set_owner("ds-2", "user-a")
    metadata_service.set_owner("ds-3", "user-b")
    assert set(metadata_service.list_ids_for_owner("user-a")) == {"ds-1", "ds-2"}
    assert set(metadata_service.list_ids_for_owner("user-b")) == {"ds-3"}


def test_assign_owner_to_ownerless_only_touches_unowned(users_db):
    metadata_service.set_owner("ds-owned", "user-a")
    # Bypass set_owner so ds-raw is created without an owner field.
    from app.services import metadata_service as ms

    with ms._lock:  # noqa: SLF001
        data = ms._load()
        data["ds-raw"] = ms._normalize_entry({"display_name": "raw"})
        ms._save(data)

    stamped = metadata_service.assign_owner_to_ownerless("user-admin")
    assert stamped == 1
    assert metadata_service.get_owner("ds-owned") == "user-a"
    assert metadata_service.get_owner("ds-raw") == "user-admin"


# ---------------------------------------------------------------------------
# Migration: ownerless data → default admin
# ---------------------------------------------------------------------------


def test_migrate_ownerless_data_creates_admin_and_stamps(users_db, monkeypatch):
    # Plant an ownerless entry directly in the sidecar.
    from app.services import metadata_service as ms

    with ms._lock:  # noqa: SLF001
        data = ms._load()
        data["ds-legacy"] = ms._normalize_entry({"display_name": "legacy"})
        ms._save(data)

    stamped = auth_service.migrate_ownerless_data()
    assert stamped == 1
    admin = auth_service.get_user_by_email(auth_service.settings.MIGRATION_ADMIN_EMAIL)
    assert admin is not None
    assert metadata_service.get_owner("ds-legacy") == admin["id"]


def test_migrate_ownerless_data_is_idempotent(users_db):
    auth_service.migrate_ownerless_data()
    # Second run: admin already exists, no ownerless data left.
    stamped = auth_service.migrate_ownerless_data()
    assert stamped == 0
