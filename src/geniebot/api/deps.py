"""API dependencies: DB session (re-exported from db.session) and JWT
auth (doc 2.3 "Identity: JWT via OpenAM, DSP token translation"). The
review UI authenticates against this API with a bearer JWT; AUTH_BACKEND
controls whether that token is verified against the real OpenAM JWKS or a
locally-issued dev token signed with API_DEV_SHARED_SECRET.
"""
from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# Re-exported so routers only need to import from geniebot.api.deps.
from geniebot.db.session import get_session  # noqa: F401
from geniebot.settings import get_settings

_security = HTTPBearer(auto_error=False)


def create_dev_token(sub: str, *, expires_in_seconds: int = 3600) -> str:
    """Issues a locally-signed dev JWT. Used by tests and local demo
    scripts when AUTH_BACKEND=mock; never used in the openam path."""
    import time

    settings = get_settings()
    payload = {"sub": sub, "iat": int(time.time()), "exp": int(time.time()) + expires_in_seconds}
    return jwt.encode(payload, settings.api_dev_shared_secret, algorithm="HS256")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> str:
    settings = get_settings()
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = credentials.credentials

    if settings.auth_backend == "openam":
        try:
            jwks_client = jwt.PyJWKClient(settings.jwt_jwks_url)
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token, signing_key.key, algorithms=["RS256"], audience=settings.jwt_audience
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}") from exc
    else:
        try:
            payload = jwt.decode(token, settings.api_dev_shared_secret, algorithms=["HS256"])
        except jwt.PyJWTError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid dev token: {exc}") from exc

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token missing sub claim")
    return sub
