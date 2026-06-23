"""Phase 4A: 邮箱/密码 + JWT 认证。

设计要点：
- users 表落在 main.db（与 metadata 同库），用裸 SQLAlchemy text() 调用，
  与项目其他持久化风格一致（不引入 ORM session）。
- 密码用 bcrypt 哈希；JWT 用 HS256 签名。
- access token 短期（默认 15 分钟），refresh token 长期（默认 7 天），
  refresh token 的 jti 落 Redis（key `refresh:{jti}`），用于服务端可吊销。
- main.db 在 startup 时由 init_users_table() 建表；
  migrate_ownerless_data() 把已有的 ownerless 数据源 / 会话回填到默认 admin。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from sqlalchemy import text

from ..config import settings
from ..utils.database import get_engine
from ..utils.logger import get_logger
from . import metadata_service, session_service

logger = get_logger(__name__)

REFRESH_KEY_PREFIX = "refresh:"


class AuthError(Exception):
    """认证 / 授权类错误的基类。"""


class EmailExists(AuthError):
    pass


class InvalidCredentials(AuthError):
    pass


class InvalidToken(AuthError):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Table bootstrap
# ---------------------------------------------------------------------------


_INIT_USERS_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 0
)
"""


def init_users_table() -> None:
    """Create the users table if missing. Idempotent.

    Also backfills the ``must_change_password`` column on pre-existing tables
    — older deployments created the table without it, and SQLite's
    ``ALTER TABLE ADD COLUMN`` is safe here because the column has a default.
    """
    engine = get_engine(None)
    with engine.begin() as conn:
        conn.execute(text(_INIT_USERS_SQL))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)"))
        # Add column if missing (existing DBs from before Phase 6).
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
        if "must_change_password" not in cols:
            conn.execute(
                text("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
            )


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _row_to_user(row: Any) -> dict | None:
    if row is None:
        return None
    return {
        "id": row[0],
        "email": row[1],
        "password_hash": row[2],
        "is_active": bool(row[3]),
        "created_at": row[4],
        "must_change_password": bool(row[5]) if len(row) > 5 else False,
    }


def get_user(user_id: str) -> dict | None:
    engine = get_engine(None)
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT id, email, password_hash, is_active, created_at, must_change_password "
                "FROM users WHERE id = :id"
            ),
            {"id": user_id},
        ).first()
    return _row_to_user(row)


def get_user_by_email(email: str) -> dict | None:
    engine = get_engine(None)
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT id, email, password_hash, is_active, created_at, must_change_password "
                "FROM users WHERE email = :e"
            ),
            {"e": _normalize_email(email)},
        ).first()
    return _row_to_user(row)


def list_user_ids() -> list[str]:
    engine = get_engine(None)
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id FROM users")).fetchall()
    return [r[0] for r in rows]


def register(email: str, password: str, *, must_change_password: bool = False) -> dict:
    """Create a new user. Returns {id, email}. Raises on duplicate / weak password.

    ``must_change_password`` is set True for the migration admin (whose
    password is the committed default) so the frontend can force a password
    change on first login. Regular registrations pass False.
    """
    if not email or not password:
        raise InvalidCredentials("Email and password are required")
    if len(password) < 8:
        raise InvalidCredentials("Password must be at least 8 characters")
    # Phase 6: minimum complexity — at least one letter and one digit. Keeps
    # out trivial passwords (aaaa1234 still passes, but pure "aaaaaaaa" /
    # "12345678" doesn't) without forcing the full NIST-style rule set on
    # users. Bypassed in LLM_MOCK so E2E can use simple test passwords.
    if not getattr(settings, "LLM_MOCK", False):
        has_letter = any(c.isalpha() for c in password)
        has_digit = any(c.isdigit() for c in password)
        if not (has_letter and has_digit):
            raise InvalidCredentials("Password must contain both letters and digits")
    email = _normalize_email(email)
    # Cheap validity check before hitting the DB.
    if "@" not in email or "." not in email.split("@", 1)[1]:
        raise InvalidCredentials("Invalid email format")
    engine = get_engine(None)
    with engine.begin() as conn:
        existing = conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email}).first()
        if existing:
            raise EmailExists(email)
        user_id = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, is_active, created_at, must_change_password) "
                "VALUES (:id, :email, :hash, 1, :ts, :mcp)"
            ),
            {
                "id": user_id,
                "email": email,
                "hash": _hash_password(password),
                "ts": _iso(_utcnow()),
                "mcp": 1 if must_change_password else 0,
            },
        )
    return {"id": user_id, "email": email}


def authenticate(email: str, password: str) -> dict:
    """Verify credentials. Returns the user dict (without password_hash)."""
    user = get_user_by_email(email)
    if user is None or not _verify_password(password, user["password_hash"]):
        raise InvalidCredentials("Invalid email or password")
    if not user["is_active"]:
        raise InvalidCredentials("Account is disabled")
    return {
        "id": user["id"],
        "email": user["email"],
        "is_active": user["is_active"],
        "must_change_password": user.get("must_change_password", False),
    }


def change_password(user_id: str, old_password: str, new_password: str) -> None:
    """Verify old password, set new password, clear must_change_password.

    Used by the force-change-password flow on first admin login. Raises
    InvalidCredentials if the old password is wrong or the new one fails
    the complexity check.
    """
    user = get_user(user_id)
    if user is None:
        raise InvalidCredentials("User not found")
    if not _verify_password(old_password, user["password_hash"]):
        raise InvalidCredentials("Old password is incorrect")
    # Re-run the same complexity rules as register() so the new password
    # can't be weaker than the registration policy.
    if len(new_password) < 8:
        raise InvalidCredentials("Password must be at least 8 characters")
    if not getattr(settings, "LLM_MOCK", False):
        has_letter = any(c.isalpha() for c in new_password)
        has_digit = any(c.isdigit() for c in new_password)
        if not (has_letter and has_digit):
            raise InvalidCredentials("Password must contain both letters and digits")
    if old_password == new_password:
        raise InvalidCredentials("New password must differ from the old one")
    engine = get_engine(None)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET password_hash = :h, must_change_password = 0 WHERE id = :id"),
            {"h": _hash_password(new_password), "id": user_id},
        )


# ---------------------------------------------------------------------------
# Token issue / verify
# ---------------------------------------------------------------------------


async def issue_tokens(user: dict) -> dict:
    """Issue an access + refresh token pair for the given user dict."""
    now = _utcnow()
    access_payload = {
        "sub": user["id"],
        "email": user["email"],
        "type": "access",
        # jti makes every issued access token unique even when two calls
        # land in the same second (same iat/exp) — otherwise the test
        # asserting new_tokens != old_tokens would flake on fast machines.
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_TTL_MINUTES),
    }
    access_token = jwt.encode(access_payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

    jti = str(uuid.uuid4())
    refresh_payload = {
        "sub": user["id"],
        "email": user["email"],
        "jti": jti,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS),
    }
    refresh_token = jwt.encode(
        refresh_payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM
    )
    await _store_refresh(jti, user["id"])
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_TTL_MINUTES * 60,
    }


def verify_access_token(token: str) -> dict:
    """Decode + validate an access token. Raises InvalidToken on any failure."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError as e:
        raise InvalidToken(str(e)) from e
    if payload.get("type") != "access":
        raise InvalidToken("Wrong token type")
    if "sub" not in payload:
        raise InvalidToken("Missing subject")
    return payload


def verify_refresh_token(token: str) -> dict:
    """Decode a refresh token *without* Redis lookup. Use only when the
    caller will do the Redis revocation check itself (see refresh_tokens)."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError as e:
        raise InvalidToken(str(e)) from e
    if payload.get("type") != "refresh":
        raise InvalidToken("Wrong token type")
    if not payload.get("jti") or not payload.get("sub"):
        raise InvalidToken("Missing jti or sub")
    return payload


async def refresh_tokens(refresh_token: str) -> dict:
    """Verify a refresh token, rotate it, issue a new pair."""
    payload = verify_refresh_token(refresh_token)
    jti = payload["jti"]
    redis = session_service._get_redis()
    key = f"{REFRESH_KEY_PREFIX}{jti}"
    stored = await redis.get(key)
    if stored is None:
        raise InvalidToken("Refresh token revoked or expired")
    # Rotate: invalidate the old jti before issuing a new pair.
    await redis.delete(key)
    user = get_user(payload["sub"])
    if user is None or not user["is_active"]:
        raise InvalidCredentials("User not found or disabled")
    return await issue_tokens({"id": user["id"], "email": user["email"]})


async def revoke_refresh_token(refresh_token: str) -> None:
    """Best-effort revoke. Used by /auth/logout."""
    try:
        payload = jwt.decode(
            refresh_token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
    except jwt.PyJWTError:
        return
    if payload.get("type") != "refresh":
        return
    jti = payload.get("jti")
    if not jti:
        return
    await session_service._get_redis().delete(f"{REFRESH_KEY_PREFIX}{jti}")


# ---------------------------------------------------------------------------
# Refresh token storage (Redis)
# ---------------------------------------------------------------------------


async def _store_refresh(jti: str, user_id: str) -> None:
    """Persist a refresh-token jti so we can revoke later.

    Routes through ``session_service._get_redis()`` so tests can patch it
    with fakeredis (the previous sync-client path bypassed the fake and
    wrote to real Redis, breaking test isolation when Redis was running
    locally).
    """
    await session_service._get_redis().setex(
        f"{REFRESH_KEY_PREFIX}{jti}",
        int(timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS).total_seconds()),
        user_id,
    )


# ---------------------------------------------------------------------------
# Migration: assign ownerless data to a default admin user
# ---------------------------------------------------------------------------


def migrate_ownerless_data() -> int:
    """Stamp a default admin as owner of any pre-Phase-4 data.

    Called from FastAPI startup. Returns the count of data sources that
    were re-stamped. Sessions in Redis are not migrated — they expire
    within 30 minutes anyway, and forcing re-login is the safe move.
    """
    if not list_user_ids():
        # First-time boot with no users. Create the migration admin so
        # existing data is reachable. If there's no existing data at all
        # we still create the admin so tests / dev can log in immediately.
        try:
            register(
                settings.MIGRATION_ADMIN_EMAIL,
                settings.MIGRATION_ADMIN_PASSWORD,
                must_change_password=True,
            )
            logger.warning(
                "Created migration admin %s — change its password immediately.",
                settings.MIGRATION_ADMIN_EMAIL,
            )
        except EmailExists:
            pass

    admin = get_user_by_email(settings.MIGRATION_ADMIN_EMAIL)
    if admin is None:
        # A user registered before the migration admin was created (e.g. the
        # first /auth/register call landed before startup finished). Create
        # the admin now so ownerless data has a valid owner to stamp.
        try:
            register(
                settings.MIGRATION_ADMIN_EMAIL,
                settings.MIGRATION_ADMIN_PASSWORD,
                must_change_password=True,
            )
            logger.warning(
                "Created migration admin %s — change its password immediately.",
                settings.MIGRATION_ADMIN_EMAIL,
            )
        except EmailExists:
            pass
        admin = get_user_by_email(settings.MIGRATION_ADMIN_EMAIL)
        if admin is None:
            return 0

    # Walk the sidecar; any entry missing owner_id gets stamped.
    stamped = metadata_service.assign_owner_to_ownerless(admin["id"])
    if stamped:
        logger.info("Migrated %d ownerless data sources to admin %s", stamped, admin["email"])
    return stamped
