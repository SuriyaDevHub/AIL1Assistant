"""doc 7.1: GET /health - Liveness and dependency status."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.api.deps import get_session
from geniebot.settings import get_settings

router = APIRouter()


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover - defensive
        checks["database"] = f"error: {exc}"

    settings = get_settings()
    checks["llm_backend"] = settings.llm_backend
    checks["queue_backend"] = settings.queue_backend
    checks["storage_backend"] = settings.storage_backend
    checks["jira_backend"] = settings.jira_backend
    checks["mail_backend"] = settings.mail_backend
    checks["vector_store_backend"] = settings.vector_store_backend

    healthy = checks["database"] == "ok"
    return JSONResponse(
        content={"status": "ok" if healthy else "degraded", "checks": checks},
        status_code=200 if healthy else 503,
    )
