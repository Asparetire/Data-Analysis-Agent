from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..services import auth_service
from ..services.auth_service import AuthError, InvalidCredentials, InvalidToken

# We use HTTPBearer so /docs gets the "Authorize" button and the failure
# response is a standard 401 with a WWW-Authenticate header. auto_error=False
# lets us return our own error shape instead of Starlette's default.
_bearer = HTTPBearer(auto_error=False)


async def current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """Resolve the authenticated user from the Authorization header.

    Every protected route depends on this. Returns a dict
    ``{id, email, is_active}`` (no password hash) on success.
    """
    token = ""
    if creds is not None and creds.credentials:
        token = creds.credentials
    else:
        # Fall back to raw header parsing for clients that don't go through
        # the OpenAPI bearer flow (curl, axios, SSE fetch).
        header = request.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = auth_service.verify_access_token(token)
    except InvalidToken as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    user = auth_service.get_user(payload["sub"])
    if user is None or not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or disabled",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {"id": user["id"], "email": user["email"], "is_active": user["is_active"]}


def auth_error_to_http(e: AuthError) -> HTTPException:
    """Translate an AuthError to an HTTPException. Used by /auth/* routes."""
    if isinstance(e, (InvalidCredentials, InvalidToken)):
        return HTTPException(status_code=401, detail=str(e))
    if isinstance(e, auth_service.EmailExists):
        return HTTPException(status_code=409, detail="Email already registered")
    return HTTPException(status_code=400, detail=str(e))
