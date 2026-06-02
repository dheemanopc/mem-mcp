"""FastAPI application entry for mem-mcp.

Public ASGI app: ``mem_mcp.main:app``. The systemd unit at
``deploy/systemd/mem-mcp.service`` (PR #135) starts uvicorn against this
target with --workers 2, listening on 127.0.0.1:8080.

Wires all routers (auth, well-known, DCR, DCR admin, internal_invite, MCP
transport, web handlers) + middleware (CSRF, Bearer) + tool registry.
"""
# mypy: disable-error-code="unused-ignore,type-arg,arg-type"

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from mem_mcp.audit.logger import DbAuditLogger
from mem_mcp.auth.dcr import (
    BotoCognitoClientFactory,
    DbAllowedSoftwareLookup,
    DbOauthClientStore,
    InMemoryRateLimiter,
    make_dcr_router,
)
from mem_mcp.auth.dcr_admin import (
    DbOauthClientDeleter,
    DbOauthClientLookup,
    make_dcr_admin_router,
)
from mem_mcp.auth.internal_invite import DbInviteStore, make_internal_invite_router
from mem_mcp.auth.jwks import HttpxJwksFetcher, JwksCache
from mem_mcp.auth.jwt_validator import JwtValidator
from mem_mcp.auth.middleware import DbTenantResolver, DbTouch, make_bearer_middleware
from mem_mcp.auth.well_known import make_well_known_router
from mem_mcp.cognito_clients import (
    BotoAdminDeleter,
    BotoClientDeleter,
    BotoGlobalSignOutter,
    HttpxTokenExchanger,
    IdTokenUserInfoFetcher,
)
from mem_mcp.config import get_settings
from mem_mcp.db import close_maint_pool, close_pool, init_maint_pool, init_pool
from mem_mcp.health import (
    BedrockHealthChecker,
    CognitoJwksHealthChecker,
    DbHealthChecker,
    HealthChecker,
    aggregate,
)
from mem_mcp.logging_setup import get_logger, setup_logging
from mem_mcp.mcp.registry import ToolRegistry
from mem_mcp.mcp.tools._deps import make_default_deps
from mem_mcp.mcp.tools.add_team_member import AddTeamMemberTool
from mem_mcp.mcp.tools.assign_role import AssignRoleTool
from mem_mcp.mcp.tools.create_team import CreateTeamTool
from mem_mcp.mcp.tools.delete import MemoryDeleteTool
from mem_mcp.mcp.tools.export import MemoryExportTool
from mem_mcp.mcp.tools.feedback import MemoryFeedbackTool
from mem_mcp.mcp.tools.get import MemoryGetTool
from mem_mcp.mcp.tools.get_batch import MemoryGetBatchTool
from mem_mcp.mcp.tools.list import MemoryListTool
from mem_mcp.mcp.tools.list_my_teams import ListMyTeamsTool
from mem_mcp.mcp.tools.refs_in import RefsInTool
from mem_mcp.mcp.tools.refs_out import RefsOutTool
from mem_mcp.mcp.tools.remove_team_member import RemoveTeamMemberTool
from mem_mcp.mcp.tools.search import MemorySearchTool
from mem_mcp.mcp.tools.slug_lookup import MemsysSlugLookupTool
from mem_mcp.mcp.tools.stats import MemoryStatsTool
from mem_mcp.mcp.tools.supersede import MemorySupersedeTool
from mem_mcp.mcp.tools.thread_get import MemoryThreadGetTool
from mem_mcp.mcp.tools.undelete import MemoryUndeleteTool
from mem_mcp.mcp.tools.update import MemoryUpdateTool
from mem_mcp.mcp.tools.write import MemoryWriteTool
from mem_mcp.mcp.tools.write_async import MemoryWriteAsyncTool
from mem_mcp.mcp.tools.write_batch import MemoryWriteBatchTool
from mem_mcp.mcp.transport import make_mcp_router
from mem_mcp.quotas.enforcer import QuotaEnforcer

# Kite enable tool extracted to mem-mcp-skill-kite plugin (Phase 3 refactor)
from mem_mcp.skills.vault import SkillVault, SkillVaultDisabledError
from mem_mcp.web.csrf import CsrfMiddleware
from mem_mcp.web.handlers.clients import make_clients_router
from mem_mcp.web.handlers.export import make_export_router
from mem_mcp.web.handlers.feedback import make_feedback_router
from mem_mcp.web.handlers.identities import make_identities_router
from mem_mcp.web.handlers.memories import make_memories_router
from mem_mcp.web.handlers.notices import make_notices_router
from mem_mcp.web.handlers.stats import make_stats_router
from mem_mcp.web.handlers.teams import make_teams_router
from mem_mcp.web.handlers.tenant import make_tenant_router
from mem_mcp.web.routes import make_web_router


def _build_default_checkers() -> list[HealthChecker]:
    """Construct the production set of HealthCheckers from current Settings."""
    s = get_settings()
    return [
        DbHealthChecker(),
        BedrockHealthChecker(region=s.region),
        CognitoJwksHealthChecker(
            region=s.region,
            user_pool_id=s.cognito_user_pool_id,
            url=s.cognito_jwks_url,
        ),
    ]


def _wire_skills(registry: ToolRegistry, log: Any) -> int:
    """If skill framework is configured, register enabled skills into the registry.

    Returns the number of skill tools registered (0 if framework disabled).
    """
    from mem_mcp.skills.loader import SkillLoader, parse_enabled_skills
    from mem_mcp.skills.vault import SkillVault, SkillVaultDisabledError

    s = get_settings()
    enabled = parse_enabled_skills(s.enabled_skills)
    if not enabled:
        log.info("skills_disabled", reason="enabled_skills_empty")
        return 0

    try:
        vault = SkillVault.from_settings(s)
    except SkillVaultDisabledError:
        log.warning("skills_disabled", reason="skill_vault_master_key_unset")
        return 0
    except Exception as exc:
        log.error("skills_vault_init_failed", error=str(exc))
        return 0

    loader = SkillLoader(vault=vault)
    total = 0
    for skill_name in enabled:
        skill = _instantiate_skill(skill_name, log)
        if skill is None:
            continue
        count = loader.register_skill(registry, skill)
        log.info(
            "skill_registered",
            skill_name=skill_name,
            tool_count=count,
        )
        total += count
    return total


def _instantiate_skill(skill_name: str, log: Any) -> Any:
    """Construct a Skill instance by name. Returns None if unknown.

    Phase 3 refactor: kite skill extracted to mem-mcp-skill-kite plugin repo.
    This function is kept for backward compatibility and potential future skills.
    """
    log.info(
        "skill_deprecated",
        skill_name=skill_name,
        reason="skills now use Plugin SDK (entry-point based discovery)",
    )
    return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup → init logging + DB pool. Shutdown → close pool."""
    s = get_settings()
    setup_logging(s.log_level)
    log = get_logger("mem_mcp.main")
    log.info("startup_begin", region=s.region, log_level=s.log_level)

    await init_pool()
    log.info("pool_initialized")

    # Bootstrap first system admin (idempotent, no-op if env var unset)
    try:
        from mem_mcp.bootstrap.system_admin import bootstrap_first_system_admin
        from mem_mcp.db import get_pool as _get_pool_for_bootstrap

        pool = _get_pool_for_bootstrap()
        bs_result = await bootstrap_first_system_admin(pool)
        log.info("bootstrap_first_system_admin", result=bs_result)
    except Exception:
        log.exception("bootstrap_first_system_admin failed (non-fatal)")

    # Plugin discovery — load entry-point plugins
    try:
        from mem_mcp.auth.plugin_perms import register_plugin_permissions
        from mem_mcp.plugins.registry import PluginRegistry

        plugin_registry = PluginRegistry()
        plugin_registry.discover()
        # Pull each plugin's declared permissions into the runtime grant catalog
        # BEFORE register_tools runs, so RBAC checks against the new perms work
        # from the first request onward.
        register_plugin_permissions(plugin_registry)
        log.info(
            "plugin_discovery_complete",
            extra={
                "count": len(plugin_registry.all()),
                "ids": [p.id for p in plugin_registry.all()],
            },
        )
        app.state.plugin_registry = plugin_registry
    except Exception:
        log.exception("plugin_discovery_failed (non-fatal)")
        app.state.plugin_registry = None

    # Wire routers AFTER pool is initialized but BEFORE yielding to uvicorn.
    # (Middleware is added separately in create_app() — Starlette requires
    # add_middleware() before the app starts serving.)
    if not getattr(app.state, "routers_wired", False):
        _wire_routers(app)
        app.state.routers_wired = True
        log.info("routers_wired", count=len(app.routes))

    # Phase 2.5: wire concrete plugin integration
    if app.state.plugin_registry is not None:
        try:
            from mem_mcp.db import get_pool
            from mem_mcp.plugins.integration import (
                collect_plugin_tools,
                mount_plugin_routes,
                run_plugin_jobs_setup,
                run_plugin_startup_hooks,
            )
            from mem_mcp.plugins.schema import ensure_plugin_schemas

            pool = get_pool()

            # 1. Create per-plugin PG schemas — uses short-lived maint pool for DDL privs
            maint_pool = await init_maint_pool()
            try:
                await ensure_plugin_schemas(maint_pool, app.state.plugin_registry)
            finally:
                await close_maint_pool()

            # 2. Mount plugin web routes under /skills/<id>
            mount_plugin_routes(app, app.state.plugin_registry)

            # 3. Collect tool declarations
            app.state.plugin_tools = collect_plugin_tools(app.state.plugin_registry)

            # 3b. Wire plugin tools into MCP ToolRegistry so MCP clients can call them
            if hasattr(app.state, "mcp_tool_registry"):
                mcp_registry = app.state.mcp_tool_registry
                for rt in app.state.plugin_tools:
                    mcp_registry.register_plugin_tool(rt)
                log.info(
                    "plugin_tools_wired_into_mcp_registry",
                    extra={"count": len(app.state.plugin_tools)},
                )
            else:
                log.warning("mcp_tool_registry_not_found_during_plugin_wiring")

            # 4. Collect job declarations (real firing is later PR)
            app.state.plugin_jobs = await run_plugin_jobs_setup(app.state.plugin_registry)

            # 5. Fire plugin on_startup hooks now that every plugin has been
            # registered, mounted, and had its tools + jobs collected. Done
            # after step 4 so a startup hook can safely assume the rest of
            # the plugin lifecycle is in place. Per-plugin try/except inside
            # the hook runner means one plugin's failure can't kill startup.
            # Closes memsys:36ac16a1 (on_startup never invoked).
            app.state.plugin_startup_results = await run_plugin_startup_hooks(
                app.state.plugin_registry, pool
            )

            log.info(
                "plugin_wiring_complete",
                extra={
                    "tool_count": len(app.state.plugin_tools),
                    "job_plugin_count": len(app.state.plugin_jobs),
                    "startup_results": app.state.plugin_startup_results,
                },
            )
        except Exception:
            log.exception("plugin_wiring_failed (non-fatal)")

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
        docs_url=None,  # no Swagger UI in production
        redoc_url=None,  # no ReDoc
        openapi_url=None,  # no public OpenAPI spec
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

    # Middleware is added here (must be before app starts).
    # Routers are wired inside `lifespan()` after init_pool().
    _wire_middleware(app)
    return app


def _wire_middleware(app: FastAPI) -> None:
    """Add middleware (CSRF + Bearer). Called from create_app() BEFORE app starts.

    Bearer middleware defers its construction to first request because the DB
    pool isn't initialized at create_app() time.
    """
    from starlette.middleware.base import BaseHTTPMiddleware

    app.add_middleware(CsrfMiddleware)

    class BearerMiddlewareAdapter(BaseHTTPMiddleware):
        def __init__(self, app):  # type: ignore[no-untyped-def]
            super().__init__(app)
            self._fn = None

        async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
            if self._fn is None:
                self._fn = _build_bearer_dispatch()
            return await self._fn(request, call_next)

    app.add_middleware(BearerMiddlewareAdapter)


def _build_bearer_dispatch() -> Any:
    """Construct the bearer middleware callable. Called lazily on first request."""
    from mem_mcp.db import get_pool

    s = get_settings()
    pool = get_pool()
    # Issuer + JWKS URLs may be overridden (e.g. cognito-local sidecar for
    # localhost dev). Both unset = legacy AWS form. They're allowed to diverge:
    # issuer is what tokens claim in `iss`; JWKS URL is where the validator
    # fetches signing keys. In the cognito-local case, browser-issued tokens
    # carry `iss=http://localhost:9229/...` while the API container fetches
    # JWKS via `http://cognito-local:9229/...`.
    issuer = s.cognito_issuer_url or (
        f"https://cognito-idp.{s.region}.amazonaws.com/{s.cognito_user_pool_id}"
    )
    jwks_url = s.cognito_jwks_url or f"{issuer}/.well-known/jwks.json"
    if s.cognito_jwks_url or s.cognito_issuer_url:
        jwks_fetcher = HttpxJwksFetcher.from_url(jwks_url)
    else:
        jwks_fetcher = HttpxJwksFetcher(region=s.region, user_pool_id=s.cognito_user_pool_id)
    jwks_cache = JwksCache(jwks_fetcher)
    jwt_validator = JwtValidator(jwks_cache=jwks_cache, issuer=issuer)
    tenant_resolver = DbTenantResolver(pool=pool)
    touch = DbTouch(pool=pool)
    return make_bearer_middleware(
        validator=jwt_validator,
        resolver=tenant_resolver,
        touch=touch,
        resource_metadata_url=s.resource_url,
    )


def _wire_routers(app: FastAPI) -> None:
    """Wire all routers into the FastAPI app."""
    from mem_mcp.db import get_pool

    s = get_settings()
    log = get_logger("mem_mcp.main")

    # Get the pool (initialized during lifespan startup)
    pool = get_pool()

    # Build Cognito URLs
    cognito_authorize_base_url = f"https://{s.cognito_domain}/login"
    cognito_token_url = f"https://{s.cognito_domain}/oauth2/token"
    callback_url = f"{s.web_url}/auth/callback"

    # Build Cognito Protocol implementations
    token_exchanger = HttpxTokenExchanger(
        cognito_token_url=cognito_token_url,
        client_id=s.web_client_id,
        client_secret=s.web_client_secret,
    )
    user_info_fetcher = IdTokenUserInfoFetcher()  # type: ignore[misc]
    admin_deleter = BotoAdminDeleter(
        user_pool_id=s.cognito_user_pool_id,
        region=s.region,
    )
    sign_outter = BotoGlobalSignOutter(
        user_pool_id=s.cognito_user_pool_id,
        region=s.region,
    )
    client_deleter = BotoClientDeleter(
        user_pool_id=s.cognito_user_pool_id,
        region=s.region,
    )

    # Build ToolDeps (embeddings, audit, quotas). Provider selected via env
    # (MEM_MCP_EMBEDDINGS_PROVIDER): defaults to bedrock (prod path); set to
    # "ollama" for localhost dev via the Ollama sidecar.
    from mem_mcp.embeddings.factory import make_embedding_client

    embeddings = make_embedding_client(s)
    audit = DbAuditLogger()
    quotas = QuotaEnforcer(pool=pool)
    deps = make_default_deps(
        embeddings=embeddings,
        audit=audit,
        quotas=quotas,
    )

    # Build ToolRegistry with 21 tools (14 memory + 1 onboarding + 6 team)
    registry = ToolRegistry()
    tools_list: list = [
        MemoryWriteTool,
        MemoryWriteAsyncTool,
        MemoryWriteBatchTool,
        MemorySearchTool,
        MemoryGetTool,
        MemoryGetBatchTool,
        MemoryListTool,
        MemoryUpdateTool,
        MemoryDeleteTool,
        MemoryUndeleteTool,
        MemorySupersedeTool,
        MemoryThreadGetTool,
        MemoryExportTool,
        MemoryFeedbackTool,
        MemoryStatsTool,
        # MemsysEnableKiteTool moved to mem-mcp-skill-kite plugin
        AddTeamMemberTool,
        AssignRoleTool,
        CreateTeamTool,
        ListMyTeamsTool,
        MemsysSlugLookupTool,
        RefsInTool,
        RefsOutTool,
        RemoveTeamMemberTool,
    ]
    for tool_cls in tools_list:  # type: ignore[attr-defined]
        registry.register(tool_cls)  # type: ignore[arg-type]

    # Register skill-framework tools (if any skills are enabled)
    skill_count = _wire_skills(registry, log)
    if skill_count > 0:
        log.info("skills_wired", total_skill_tools=skill_count)

    # Wire web auth router
    web_router = make_web_router(
        cognito_authorize_base_url=cognito_authorize_base_url,
        cognito_client_id=s.web_client_id,
        callback_url=callback_url,
        token_exchanger=token_exchanger,
        user_info_fetcher=user_info_fetcher,
        pool=pool,
        audit=audit,
    )
    app.include_router(web_router)

    # Wire web handler routers
    stats_router = make_stats_router(pool=pool, deps=deps)
    app.include_router(stats_router)

    feedback_router = make_feedback_router(pool=pool, deps=deps)
    app.include_router(feedback_router)

    notices_router = make_notices_router(pool=pool)
    app.include_router(notices_router)

    memories_router = make_memories_router(pool=pool, deps=deps)
    app.include_router(memories_router)

    tenant_router = make_tenant_router(
        pool=pool,
        audit=audit,
        sign_outter=sign_outter,
    )
    app.include_router(tenant_router)

    identities_router = make_identities_router(
        pool=pool,
        audit=audit,
        admin_deleter=admin_deleter,
        hmac_secret=s.link_state_secret,
        cognito_authorize_base_url=cognito_authorize_base_url,
        cognito_client_id=s.web_client_id,
        callback_url=callback_url,
    )
    app.include_router(identities_router)

    teams_router = make_teams_router(pool=pool, audit=audit)
    app.include_router(teams_router)

    clients_router = make_clients_router(
        pool=pool,
        audit=audit,
        client_deleter=client_deleter,
    )
    app.include_router(clients_router)

    export_router = make_export_router(pool=pool, deps=deps)
    app.include_router(export_router)

    # Wire signup request routers
    from mem_mcp.web.signup_requests.admin_routes import make_admin_signup_router
    from mem_mcp.web.signup_requests.public_routes import make_public_signup_router

    public_signup_router = make_public_signup_router(pool=pool, audit=audit)
    app.include_router(public_signup_router)
    admin_signup_router = make_admin_signup_router(pool=pool, audit=audit)
    app.include_router(admin_signup_router)

    # Wire dangling users admin router
    from mem_mcp.jobs.reconcile_signups import BotoCognitoUserLister
    from mem_mcp.web.admin.dangling_users import make_dangling_users_router

    cognito_lister = BotoCognitoUserLister(
        user_pool_id=s.cognito_user_pool_id,
        region=s.region,
    )
    dangling_users_router = make_dangling_users_router(
        pool=pool,
        cognito_lister=cognito_lister,
        audit=audit,
    )
    app.include_router(dangling_users_router)

    # Wire kite confirm router (user-facing intent confirmation)
    try:
        from mem_mcp.web.skills.kite_confirm import make_kite_confirm_router

        vault_for_confirm = SkillVault.from_settings(s)
        kite_confirm_router = make_kite_confirm_router(
            pool=pool,
            audit=audit,
            vault=vault_for_confirm,
        )
        app.include_router(kite_confirm_router)
    except SkillVaultDisabledError:
        log.warning("kite_confirm_router_disabled", reason="vault not configured")
    except Exception as exc:
        log.warning("kite_confirm_router_disabled", reason=str(exc))

    # Wire .well-known router
    well_known_router = make_well_known_router(
        resource_url=s.resource_url,
        cognito_domain=s.cognito_domain,
        region=s.region,
        user_pool_id=s.cognito_user_pool_id,
    )
    app.include_router(well_known_router)

    # Wire DCR router
    # Build DCR dependencies
    cognito_factory = BotoCognitoClientFactory(
        region=s.region,
        user_pool_id=s.cognito_user_pool_id,
        resource_server_identifier=s.resource_url,
    )
    client_store = DbOauthClientStore(pool=pool)
    software_lookup = DbAllowedSoftwareLookup(pool=pool)
    rate_limiter = InMemoryRateLimiter()

    dcr_router = make_dcr_router(
        cognito_factory=cognito_factory,
        client_store=client_store,
        software_lookup=software_lookup,
        rate_limiter=rate_limiter,
        resource_url=s.resource_url,
    )
    app.include_router(dcr_router)

    # Wire DCR admin router
    lookup = DbOauthClientLookup(pool=pool)
    db_deleter = DbOauthClientDeleter(pool=pool)
    dcr_admin_router = make_dcr_admin_router(
        lookup=lookup,
        db_deleter=db_deleter,
        cognito_deleter=client_deleter,
        resource_url=s.resource_url,
    )
    app.include_router(dcr_admin_router)

    # Wire internal_invite router
    invite_store = DbInviteStore(pool=pool)
    internal_invite_router = make_internal_invite_router(
        store=invite_store,
        shared_secret=s.internal_lambda_secret,
    )
    app.include_router(internal_invite_router)

    # Wire kite intent router (S2S credential handoff)
    try:
        vault = SkillVault.from_settings(s)
        # Honor MEM_MCP_COGNITO_ISSUER_URL / MEM_MCP_COGNITO_JWKS_URL overrides
        # the same way the bearer middleware does — keeps the kite intent
        # router auth flow consistent in localhost dev.
        issuer = s.cognito_issuer_url or (
            f"https://cognito-idp.{s.region}.amazonaws.com/{s.cognito_user_pool_id}"
        )
        jwks_url = s.cognito_jwks_url or f"{issuer}/.well-known/jwks.json"
        if s.cognito_jwks_url or s.cognito_issuer_url:
            jwks_fetcher = HttpxJwksFetcher.from_url(jwks_url)
        else:
            jwks_fetcher = HttpxJwksFetcher(region=s.region, user_pool_id=s.cognito_user_pool_id)
        jwks_cache = JwksCache(jwks_fetcher)
        jwt_validator = JwtValidator(jwks_cache=jwks_cache, issuer=issuer)
        from mem_mcp.web.admin.kite_intents import make_kite_intent_router

        kite_intent_router = make_kite_intent_router(
            pool=pool,
            audit=audit,
            vault=vault,
            jwt_validator=jwt_validator,
            settings=s,
        )
        app.include_router(kite_intent_router)
    except Exception as exc:
        log.warning("kite_intent_router_disabled", reason=str(exc))

    # Wire MCP transport router
    mcp_router = make_mcp_router(
        registry=registry,
        db_pool=pool,
        deps=deps,
    )
    app.include_router(mcp_router)

    # Stash registry on app state so lifespan can wire plugin tools into it
    app.state.mcp_tool_registry = registry

    log.info("app_wiring_complete", routes_count=len(app.routes))


# Module-level ASGI app for uvicorn
app = create_app()
