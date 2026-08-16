"""FastAPI app factory - doc section 2.3 "Orchestration service: Python
(FastAPI)"."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from geniebot import __version__
from geniebot.api.routers import admin, health, incidents, ingest, kb
from geniebot.db.session import get_sessionmaker, init_models
from geniebot.kb.factory import ensure_vector_store_schema
from geniebot.observability.logging_config import configure_logging
from geniebot.observability.metrics import router as metrics_router
from geniebot.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if not settings.is_production:
        # local/UAT convenience bootstrap. Production schema changes go
        # through `alembic upgrade head` as an explicit deploy step
        # (db/migrations/), not an implicit create_all on app startup.
        await init_models()

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await ensure_vector_store_schema(session)
    yield


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(title="GenieBot L1 Assistant", version=__version__, lifespan=lifespan)

    # Permissive by default for local/demo use - restrict allow_origins to
    # the real Review UI origin(s) before production cutover.
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )

    app.include_router(health.router)
    app.include_router(metrics_router)
    app.include_router(incidents.router, prefix="/incidents", tags=["incidents"])
    app.include_router(ingest.router, prefix="/ingest", tags=["ingest"])
    app.include_router(kb.router, prefix="/kb", tags=["kb"])
    app.include_router(admin.router, prefix="/admin", tags=["admin"])
    return app


app = create_app()
