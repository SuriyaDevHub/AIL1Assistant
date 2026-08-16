"""API dependencies: DB session (re-exported from db.session) and JWT
auth (doc 2.3 "Identity: JWT via OpenAM, DSP token translation"). The
review UI authenticates against this API with a bearer JWT; AUTH_BACKEND
controls whether that token is verified against the real OpenAM JWKS or a
locally-issued dev token signed with API_DEV_SHARED_SECRET.

get_current_user (sub only, unchanged) stays exactly as it was for the
existing "who acted" call sites (review decisions, admin/config audit
actors) - none of them need a role. get_current_principal is the new,
separate dependency for role-scoped visibility (GenieBot L1 Enhancement
Plan, "Scope what each role can see") - kept as an addition rather than
changing get_current_user's return type, so existing callers are
untouched.
"""
from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# Re-exported so routers only need to import from geniebot.api.deps.
from geniebot.db.session import get_session  # noqa: F401
from geniebot.settings import get_settings

_security = HTTPBearer(auto_error=False)
_ROLES = ("end_user", "l2_support", "admin")


def create_dev_token(sub: str, *, role: str = "end_user", expires_in_seconds: int = 3600) -> str:
    """Issues a locally-signed dev JWT. Used by tests and local demo
    scripts when AUTH_BACKEND=mock; never used in the openam path. role
    defaults to "end_user" (least privilege) - pass role="l2_support" or
    role="admin" for a token that should see everything."""
    import time

    settings = get_settings()
    payload = {
        "sub": sub,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + expires_in_seconds,
    }
    return jwt.encode(payload, settings.api_dev_shared_secret, algorithm="HS256")


def _decode_token(token: str) -> dict:
    settings = get_settings()
    if settings.auth_backend == "openam":
        try:
            jwks_client = jwt.PyJWKClient(settings.jwt_jwks_url)
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            return jwt.decode(
                token, signing_key.key, algorithms=["RS256"], audience=settings.jwt_audience
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}") from exc
    else:
        try:
            return jwt.decode(token, settings.api_dev_shared_secret, algorithms=["HS256"])
        except jwt.PyJWTError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid dev token: {exc}") from exc


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> str:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    payload = _decode_token(credentials.credentials)
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token missing sub claim")
    return sub


@dataclass(frozen=True)
class AuthenticatedUser:
    sub: str
    role: str
    """"end_user" | "l2_support" | "admin" - see _ROLES."""


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> AuthenticatedUser:
    """Like get_current_user, but also carries the caller's role, for
    endpoints that need to tell an end user from L2/admin (incident
    visibility scoping). The real OpenAM path's actual role claim/group
    name isn't confirmed yet (GenieBot L1 Enhancement Plan, open
    questions) - falls back to "end_user" (least privilege) for any token
    that doesn't carry a recognised role claim, rather than guessing."""
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    payload = _decode_token(credentials.credentials)
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token missing sub claim")
    role = payload.get("role")
    if role not in _ROLES:
        role = "end_user"
    return AuthenticatedUser(sub=sub, role=role)
