"""Dev-only token issuance for local testing (AUTH_BACKEND=mock only) - a
real deployment replaces this entirely with the OpenAM login redirect
(README's production cutover checklist), which is the actual place a
username's real role gets established. Until then, this validates the
requested role against a placeholder local "user directory"
(api/deps.py's _DEV_USER_ROLES) rather than trusting whatever role the
caller asks for - a username can't just claim to be admin, matching what
a real identity provider would enforce.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from geniebot.api.deps import create_dev_token, lookup_dev_user_role
from geniebot.settings import get_settings

router = APIRouter()


class DevTokenRequest(BaseModel):
    username: str
    role: Literal["end_user", "l2_support", "admin"]


@router.post("/dev-token")
async def issue_dev_token(body: DevTokenRequest) -> dict:
    settings = get_settings()
    if settings.auth_backend != "mock":
        raise HTTPException(404, "dev token issuance is only available with AUTH_BACKEND=mock")

    actual_role = lookup_dev_user_role(body.username)
    if actual_role is None:
        raise HTTPException(404, f"{body.username!r} is not a known test user")
    if actual_role != body.role:
        raise HTTPException(
            403, f"{body.username!r} is a {actual_role!r}, not {body.role!r} - select the correct role"
        )

    token = create_dev_token(body.username, role=actual_role)
    return {"token": token, "username": body.username, "role": actual_role}
