"""FastAPI application entry for mem-mcp.

Public ASGI app: ``mem_mcp.main:app``. The systemd unit at
``deploy/systemd/mem-mcp.service`` (PR #135) starts uvicorn against this
target with --workers 2, listening on 127.0.0.1:8080.

This module is intentionally minimal in v1:
- Lifespan: setup_logging + init_pool / close_pool
- Endpoints: /healthz (liveness, always 200) and /readyz (dependency probe)
- Tools (memory.write, memory.search, ...) and OAuth handlers land in later PRs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from mem_mcp.config import get_settings
from mem_mcp.db import close_pool, init_pool
from mem_mcp.health import (
    BedrockHealthChecker,
    CognitoJwksHealthChecker,
    DbHealthChecker,
    HealthChecker,
    aggregate,
)
from mem_mcp.logging_setup import get_logger, setup_logging


def _build_default_checkers() -> list[HealthChecker]:
    """Construct the production set of HealthCheckers from current Settings."""
    s = get_settings()
    return [
        DbHealthChecker(),
        BedrockHealthChecker(region=s.region),
        CognitoJwksHealthChecker(region=s.region, user_pool_id=s.cognito_user_pool_id),
    ]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup → init logging + DB pool. Shutdown → close pool."""
    s = get_settings()
    setup_logging(s.log_level)
    log = get_logger("mem_mcp.main")
    log.info("startup_begin", region=s.region, log_level=s.log_level)

    await init_pool()
    log.info("pool_initialized")

    # Stash default checker list on app state so test clients can override
    if not hasattr(app.state, "health_checkers"):
        app.state.health_checkers = _build_default_checkers()

    log.info("startup_complete")
    try:
        yield
    finally:
        log.info("shutdown_begin")
        await close_pool()
        log.info("shutdown_complete")


def create_app(checkers: list[HealthChecker] | None = None) -> FastAPI:
    """Build a FastAPI instance. ``checkers=None`` defers construction to lifespan.

    Tests pass a pre-built ``checkers`` list (with fakes) to avoid hitting
    real DB/Bedrock/Cognito.
    """
    app = FastAPI(
        title="mem-mcp",
        version="0.0.0",
        lifespan=lifespan,
        docs_url=None,        # no Swagger UI in production
        redoc_url=None,       # no ReDoc
        openapi_url=None,     # no public OpenAPI spec
    )

    if checkers is not None:
        app.state.health_checkers = checkers

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness — always 200 if the process is up."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> Response:
        """Readiness — runs each HealthChecker; 200 if all OK, else 503."""
        checks = [await c.check() for c in app.state.health_checkers]
        overall, payload = aggregate(checks)
        body = {"status": overall, "checks": payload}
        status_code = 200 if overall == "ok" else 503
        return JSONResponse(content=body, status_code=status_code)

    return app


# Module-level ASGI app for uvicorn
app = create_app()
