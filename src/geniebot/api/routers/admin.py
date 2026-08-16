"""doc 7.1 admin endpoints: config read/update, kill-switch.

PUT /config writes straight to the config/*.yaml files on disk and clears
the in-process cache - fine for calibration in dev/UAT. Doc 8.2 requires
production config changes to go "through environments with the same review
as code" (i.e. via source control / CI, not a live PUT) - consider gating
or disabling this endpoint in the production environment, or restricting
it to a narrow allow-list of fields, before cutover.
"""
from __future__ import annotations

from typing import Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.api.deps import get_current_user, get_session
from geniebot.audit import ledger
from geniebot.security.killswitch import is_processing_enabled, set_processing_enabled
from geniebot.settings import (
    CONFIG_DIR,
    clear_config_cache,
    get_generic_l1_checklist,
    get_guardrail_config,
    get_taxonomy,
    get_thresholds,
)

router = APIRouter()

_CONFIG_FILES = {
    "thresholds": "thresholds.yaml",
    "guardrails": "guardrails.yaml",
    "taxonomy": "taxonomy.yaml",
    "generic_l1_checklist": "generic_l1_checklist.yaml",
}


@router.get("/config")
async def get_config() -> dict:
    return {
        "thresholds": get_thresholds(),
        "guardrails": get_guardrail_config(),
        "taxonomy": get_taxonomy(),
        "generic_l1_checklist": get_generic_l1_checklist(),
    }


class ConfigUpdate(BaseModel):
    file: Literal["thresholds", "guardrails", "taxonomy", "generic_l1_checklist"]
    content: dict


@router.put("/config")
async def update_config(
    body: ConfigUpdate,
    actor: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    path = CONFIG_DIR / _CONFIG_FILES[body.file]
    if "version" not in body.content:
        raise HTTPException(422, "content must include a 'version' field")

    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(body.content, fh, sort_keys=False)
    clear_config_cache()

    await ledger.record(
        session,
        incident_id=None,
        event_type="CONFIG_CHANGE",
        payload={"file": body.file, "version": body.content.get("version"), "actor": actor},
    )
    await session.commit()
    return {"file": body.file, "updated": True}


class KillswitchRequest(BaseModel):
    enabled: bool


@router.post("/killswitch")
async def set_killswitch(
    body: KillswitchRequest,
    actor: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await set_processing_enabled(session, enabled=body.enabled, actor=actor)
    await ledger.record(
        session,
        incident_id=None,
        event_type="KILLSWITCH",
        payload={"enabled": body.enabled, "actor": actor},
    )
    await session.commit()
    return {"automated_processing_enabled": body.enabled}


@router.get("/killswitch")
async def get_killswitch(session: AsyncSession = Depends(get_session)) -> dict:
    return {"automated_processing_enabled": await is_processing_enabled(session)}
