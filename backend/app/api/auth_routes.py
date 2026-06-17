"""Phase 4A: /auth/* endpoints.

Routes:
- POST /auth/register  → create user, return tokens
- POST /auth/login     → verify credentials, return tokens
- POST /auth/refresh   → rotate refresh token, return new pair
- POST /auth/logout    → revoke the supplied refresh token
- GET  /auth/me        → return the current user

Access tokens are stateless JWTs; refresh tokens are JWTs whose jti is also
tracked in Redis so the server can revoke them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..models.schemas import (
    RefreshRequest,
    TokenResponse,
    UserLogin,
    UserRegister,
    UserView,
)
from ..services import auth_service
from ..utils.logger import get_logger
from .dependencies import auth_error_to_http, current_user

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: UserRegister):
    try:
        user = auth_service.register(body.email, body.password)
    except auth_service.AuthError as e:
        raise auth_error_to_http(e) from e
    tokens = auth_service.issue_tokens(user)
    logger.info("registered user %s", user["email"])
    return TokenResponse(**tokens)


@router.post("/login", response_model=TokenResponse)
async def login(body: UserLogin):
    try:
        user = auth_service.authenticate(body.email, body.password)
    except auth_service.AuthError as e:
        raise auth_error_to_http(e) from e
    tokens = auth_service.issue_tokens(user)
    logger.info("login user %s", user["email"])
    return TokenResponse(**tokens)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest):
    try:
        tokens = await auth_service.refresh_tokens(body.refresh_token)
    except auth_service.AuthError as e:
        raise auth_error_to_http(e) from e
    return TokenResponse(**tokens)


@router.post("/logout", status_code=204)
async def logout(body: RefreshRequest):
    await auth_service.revoke_refresh_token(body.refresh_token)
    return None


@router.get("/me", response_model=UserView)
async def me(user: dict = Depends(current_user)):
    return UserView(id=user["id"], email=user["email"], is_active=user["is_active"])
