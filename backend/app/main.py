import logging as _logging
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
# Friendly messages for API error codes (returned to frontend)
_API_ERROR_MESSAGES = {
    "REGISTER_USER_ALREADY_EXISTS": "An account with this email already exists. Sign in instead or use a different email.",
}
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse
from .platform.brand import BRAND_APP_DESCRIPTION, BRAND_NAME
from .platform.config import settings
from .platform.admin_auth import require_admin_secret, verify_admin_secret
from .platform import health_contracts as _health_contracts
from .platform.logging import safe_http_route, sanitize_validation_errors, setup_logging
from .platform.middleware import RequestLoggingMiddleware, RateLimitMiddleware, EnterpriseAccessMiddleware
from .platform.request_context import normalize_request_id
from .platform.frontend_origins import _build_cors_origins
from .platform.startup_validation import collect_startup_failures, is_production_like

# Set up logging
logger = setup_logging()

# ---------------------------------------------------------------------------
# Production safety: fail-fast if SECRET_KEY is the insecure default
# ---------------------------------------------------------------------------
_is_production = is_production_like(settings)
_startup_failures = collect_startup_failures(settings)
if _startup_failures:
    raise RuntimeError(_startup_failures[0])

# ---------------------------------------------------------------------------
# Disable interactive API docs in production (information disclosure)
# ---------------------------------------------------------------------------
_docs_url = None if _is_production else "/api/docs"
_openapi_url = None if _is_production else "/api/openapi.json"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Startup
    logger.info("%s API started | env=%s", BRAND_NAME, "production" if settings.SENTRY_DSN else "development")
    # Install the transport-level Anthropic wire-tap FIRST, before any
    # client is constructed, so every /v1/messages request — wrapped,
    # bare, Graphiti, gateway, or SDK retry — writes a ground-truth
    # anthropic_wire_log row. This is what reconciliation diffs against
    # claude_call_log to locate metering bypasses.
    try:
        from .services.anthropic_wire_tap import install as _install_wire_tap

        _install_wire_tap()
    except Exception as exc:  # pragma: no cover — instrumentation must never block boot
        logger.error("Failed to install Anthropic wire-tap error_type=%s", type(exc).__name__)
    # Wire candidate_graph SQLAlchemy listeners (no-op when Graphiti is unset).
    try:
        from .candidate_graph.listeners import register_listeners

        register_listeners()
    except Exception as exc:  # pragma: no cover — listener install must never block boot
        logger.error("Failed to register candidate_graph listeners error_type=%s", type(exc).__name__)
    # Kick off Graphiti init in a background thread so Neo4j async resources
    # are created on the shared background event loop before the first real
    # request arrives. The healthcheck returns "initializing" until ready.
    try:
        from .candidate_graph import client as _gc
        if _gc.is_configured():
            import threading as _t
            _t.Thread(target=_gc.get_graphiti, name="graphiti-init", daemon=True).start()
    except Exception as exc:
        logger.error("Failed to start Graphiti background init error_type=%s", type(exc).__name__)
    # FastAPI's custom-lifespan path skips Starlette's auto-propagation to
    # mounted sub-apps, so the MCP server's StreamableHTTPSessionManager
    # task group has to be started explicitly here.
    from .mcp import mcp_app as _mcp_server

    # Lazy-create the session manager (no-op when the streamable_http_app
    # mount has already done so on first import).  ``streamable_http_app``
    # captures the manager instance in its ASGI endpoint, so after a previous
    # lifespan reset we must also replace the mounted sub-app.  Otherwise a
    # second TestClient/lifespan starts the new manager while /mcp requests are
    # still routed to the retired manager whose task group has shut down.
    if _mcp_server._session_manager is None:
        restarted_mcp_app = _mcp_server.streamable_http_app()
        for route in _app.routes:
            if getattr(route, "path", None) == "/mcp":
                route.app = restarted_mcp_app
                break
    try:
        async with _mcp_server.session_manager.run():
            yield
    finally:
        # The session manager refuses to .run() twice, so drop the singleton
        # on shutdown so a follow-up lifespan (typical in tests with multiple
        # TestClient instances) can re-initialize cleanly.
        _mcp_server._session_manager = None
    # Shutdown — close Graphiti's driver and stop its background loop.
    try:
        from .candidate_graph.client import close

        close()
    except Exception as exc:  # pragma: no cover — defensive
        logger.error("Failed to close Graphiti on shutdown error_type=%s", type(exc).__name__)


app = FastAPI(
    title=f"{BRAND_NAME} API",
    description=BRAND_APP_DESCRIPTION,
    version="1.0.0",
    docs_url=_docs_url,
    openapi_url=_openapi_url,
    lifespan=_lifespan,
)

_val_logger = _logging.getLogger("taali.validation")


def _is_configured_secret(value: str | None) -> bool:
    cleaned = (value or "").strip().lower()
    return cleaned not in {"", "skip", "changeme"}


def _require_admin(request: Request) -> None:
    verify_admin_secret(request.headers.get("X-Admin-Secret"))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Log field locations/types only; request bodies can contain credentials."""
    errors = exc.errors()
    safe_errors = sanitize_validation_errors(errors, for_log=False)
    _val_logger.warning(
        "validation_error method=%s route=%s errors=%s",
        request.method,
        safe_http_route(request),
        sanitize_validation_errors(errors, for_log=True),
        extra={
            "request_id": normalize_request_id(
                getattr(request.state, "request_id", None)
            )
        },
    )
    return JSONResponse(
        status_code=422,
        content={"detail": safe_errors},
    )


# ---------------------------------------------------------------------------
# Friendly API error messages (rewrite raw codes for frontend)
# ---------------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, str) and detail in _API_ERROR_MESSAGES:
        detail = _API_ERROR_MESSAGES[detail]
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security-hardening HTTP headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response: StarletteResponse = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if _is_production:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


app.add_middleware(SecurityHeadersMiddleware)

# Compress large JSON responses. UAE users hit a us-east4 API, so the network
# hop is the documented bottleneck; repetitive list JSON compresses ~80-90%.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# CORS: frontend URL + localhost + any extra origins (e.g. Vercel production URL)
_cors_origins = _build_cors_origins(
    settings.FRONTEND_URL,
    getattr(settings, "CORS_EXTRA_ORIGINS", None),
)
_cors_origin_regex = settings.CORS_ALLOW_ORIGIN_REGEX
# If frontend is on Vercel, allow preview/production subdomains by default.
if not _cors_origin_regex and "vercel.app" in (settings.FRONTEND_URL or ""):
    _cors_origin_regex = r"https://.*\.vercel\.app"
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in _cors_origins if o],
    allow_origin_regex=_cors_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    # Include tracing headers used by browser SDKs (Sentry/OpenTelemetry) so
    # preflight requests do not block API calls such as /applications.
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Assessment-Token",
        "X-Requested-With",
        "Baggage",
        "Sentry-Trace",
        "Traceparent",
        "Tracestate",
    ],
    # Collection endpoints expose bounded page totals without inflating every
    # JSON row. Browsers may read this non-sensitive header across the
    # Vercel-to-Railway origin boundary.
    expose_headers=["X-Total-Count"],
)

# Rate limiting for public auth, assessment, MCP, and legacy webhook surfaces.
app.add_middleware(RateLimitMiddleware)

# Enterprise access controls (SSO enforcement on password-auth endpoints)
app.add_middleware(EnterpriseAccessMiddleware)

# Request logging
app.add_middleware(RequestLoggingMiddleware)

# Sentry (optional). The boundary owns every privacy- and cost-sensitive option.
# Keep this import lazy so local/test processes with no DSN avoid SDK startup.
# Processes without observability configured do not import the SDK at all.
if settings.SENTRY_DSN:
    from .platform.sentry_privacy import initialize_sentry
    # Invalid or unsupported DSNs fail closed without enabling telemetry.
    initialize_sentry(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=0.1,
    )

# Include routers
from .domains.identity_access.users_fastapi import (
    UserRead,
    UserCreate,
    UserUpdate,
    auth_backend,
    fastapi_users,
)
from .domains.assessments_runtime.routes import router as assessments_router
from .domains.identity_access.organization_routes import router as organizations_router
from .domains.identity_access.org_criteria_routes import router as org_criteria_router
from .domains.billing_webhooks.webhook_routes import router as webhooks_router
from .domains.tasks_repository.routes import router as tasks_router
from .domains.assessments_runtime.analytics_routes import router as analytics_router
from .domains.billing_webhooks.billing_routes import router as billing_router
from .domains.candidates_documents.routes import router as candidates_router
from .domains.assessments_runtime.roles_routes import router as roles_router
from .domains.assessments_runtime.requisition_routes import (
    router as requisition_router,
)
from .domains.assessments_runtime.clients_routes import router as clients_router
from .domains.identity_access.user_routes import router as users_router
from .api.v1.workable import router as workable_router
from .api.v1.bullhorn import router as bullhorn_router
from .api.v1.auth import router as auth_router
from .api.v1.background_jobs import router as background_jobs_router
from .domains.share_links import (
    public_router as share_links_public_router,
    router as share_links_router,
)
from .domains.top_reports.routes import public_router as top_reports_public_router
from .domains.submittal_packs import (
    public_router as submittal_packs_public_router,
    router as submittal_packs_router,
)
from .domains.outreach import (
    campaigns_router,
    interest_public_router,
    prospects_router,
    unsubscribe_public_router,
)
from .domains.assessments_runtime.pool_rescore_routes import router as pool_rescore_router
from .domains.outreach import router as sourcing_assist_router
from .domains.assessments_runtime.job_hiring_team_routes import (
    router as hiring_team_router,
)
from .domains.assessments_runtime.pipeline_analytics_routes import (
    router as pipeline_analytics_router,
)

# FastAPI-Users auth routers
app.include_router(
    fastapi_users.get_auth_router(auth_backend, requires_verification=True),
    prefix="/api/v1/auth/jwt",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/api/v1/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_reset_password_router(),
    prefix="/api/v1/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_verify_router(UserRead),
    prefix="/api/v1/auth",
    tags=["auth"],
)
# Team-invite routes (invite/list/resend/DELETE) mount BEFORE the
# FastAPI-Users users router so our org-scoped ``DELETE /users/{id}``
# (soft-remove) wins over FastAPI-Users' superuser-only hard delete at the
# same path. The remaining FastAPI-Users routes (GET/PATCH /{id}, /me) don't
# collide and are still served below.
app.include_router(users_router, prefix="/api/v1")
app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/api/v1/users",
    tags=["users"],
)

app.include_router(assessments_router, prefix="/api/v1")
app.include_router(pool_rescore_router, prefix="/api/v1")
app.include_router(organizations_router, prefix="/api/v1")
app.include_router(org_criteria_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(webhooks_router, prefix="/api/v1")
app.include_router(tasks_router, prefix="/api/v1")
app.include_router(analytics_router, prefix="/api/v1")
app.include_router(billing_router, prefix="/api/v1")
app.include_router(candidates_router, prefix="/api/v1")
app.include_router(roles_router, prefix="/api/v1")
app.include_router(requisition_router, prefix="/api/v1")
app.include_router(clients_router, prefix="/api/v1")
app.include_router(workable_router, prefix="/api/v1")
app.include_router(bullhorn_router, prefix="/api/v1")
app.include_router(background_jobs_router, prefix="/api/v1")
app.include_router(share_links_router, prefix="/api/v1")
app.include_router(submittal_packs_router, prefix="/api/v1")
app.include_router(prospects_router, prefix="/api/v1")
app.include_router(sourcing_assist_router, prefix="/api/v1")
app.include_router(campaigns_router, prefix="/api/v1")
from .domains.compliance import router as compliance_router  # noqa: E402

# GDPR data-subject requests + aggregate EEO report (org-owner-gated).
app.include_router(compliance_router, prefix="/api/v1")
app.include_router(hiring_team_router, prefix="/api/v1")
app.include_router(pipeline_analytics_router, prefix="/api/v1")
from .decision_policy.routes import router as decision_policy_router  # noqa: E402
from .domains.capabilities.routes import router as capability_flags_router  # noqa: E402
from .services.threshold_calibration.routes import router as threshold_calibration_router  # noqa: E402

app.include_router(decision_policy_router, prefix="/api/v1")
app.include_router(capability_flags_router, prefix="/api/v1")
app.include_router(threshold_calibration_router, prefix="/api/v1")

# Public API: per-org API-key management (Developers settings) + the curated
# /public/v1 surface external services + the Workable provider consume.
from .domains.identity_access.api_key_routes import router as api_keys_router  # noqa: E402
from .domains.public_api import router as public_api_router  # noqa: E402

app.include_router(api_keys_router, prefix="/api/v1")
app.include_router(public_api_router)

# Workable Assessments-Provider marketplace add-on (Workable calls these,
# authed with the org's Taali API key). Inert until WORKABLE_PROVIDER_ENABLED.
from .domains.workable_provider.router import router as workable_provider_router  # noqa: E402

app.include_router(workable_provider_router)
# HANDOFF v2 §3 — public share viewer is mounted at /share/:token
# (no /api/v1 prefix) so the URL the recruiter copy-pastes works in
# any browser without auth and without exposing the API surface.
app.include_router(share_links_public_router)
app.include_router(top_reports_public_router)
# Public curated client submittal: GET /submittal/{token} (no auth) — the
# agency shortlist a recruiter shares with their client for one role.
app.include_router(submittal_packs_public_router)
# Public job page: GET /api/v1/public/job/{token} (no auth) — the shareable
# listing minted when a requisition is published. Also carries the native public
# apply (POST /api/v1/public/job-pages/{token}/apply, flag-gated off).
from .domains.job_pages import public_router as job_pages_public_router  # noqa: E402
from .domains.job_pages import screening_router as job_pages_screening_router  # noqa: E402

app.include_router(job_pages_public_router)
# Recruiter-facing screening-question CRUD (authed) for the apply form.
app.include_router(job_pages_screening_router, prefix="/api/v1")

# Role distribution: GET /api/v1/roles/{id}/distribution (authed) returns the
# copy-paste artefacts (LinkedIn post + share URLs + feed URL); GET
# /api/v1/public/careers/{slug}/feed.xml (no auth) is the JobPosting XML feed the
# boards pull — same open pages as the public careers board. No LinkedIn API.
from .domains.distribution import public_router as distribution_public_router  # noqa: E402
from .domains.distribution import router as distribution_router  # noqa: E402

app.include_router(distribution_router, prefix="/api/v1")
app.include_router(distribution_public_router)

# Public client intake: GET/POST /api/v1/public/intake/{token} (no auth) — the
# scoped share link a consultancy sends to its client to describe the role.
from .domains.client_intake import public_router as client_intake_public_router  # noqa: E402

app.include_router(client_intake_public_router)

# Public demo-lead capture: POST /api/v1/public/demo-lead (no auth) — the
# marketing "book a demo" form; forwards the lead to hello@ by email.
from .domains.marketing_leads import public_router as marketing_leads_public_router  # noqa: E402

app.include_router(marketing_leads_public_router)

# Public one-click unsubscribe: GET/POST /api/v1/public/unsubscribe/{token}
# (no auth) — the outreach opt-out. GET is read-only; POST records suppression.
app.include_router(unsubscribe_public_router)
# (no auth) — the outreach interest-capture CTA. GET ratchets the message to
# 'interested' (idempotent) and 302s to the job page / thanks page.
app.include_router(interest_public_router)

# cv_match_v3.0 admin + override surface (gated server-side; flag controls runner)
from .cv_matching.routes import (
    admin_router as cv_match_admin_router,
    override_router as cv_match_override_router,
)
app.include_router(cv_match_admin_router, prefix="/api/v1")
app.include_router(cv_match_override_router, prefix="/api/v1")

# Taali Chat (in-product agentic chat that shares the public MCP read subset
# and adds chat-only tools).
from .domains.taali_chat import router as taali_chat_router  # noqa: E402

app.include_router(taali_chat_router, prefix="/api/v1")

# Role-agent chat: conversational steering of a role's autonomous agent
# (constraint/threshold edits + impact analysis), with the role's HITL
# questions + decisions merged into one timeline.
from .domains.agent_chat import router as agent_chat_router  # noqa: E402

app.include_router(agent_chat_router, prefix="/api/v1")

# Agentic recruiting: per-job autonomous agent + recruiter approval queue.
# The package's ``router`` already bundles routes/usage/cohort_signals.
from .domains.agentic import router as agentic_router  # noqa: E402

app.include_router(agentic_router, prefix="/api/v1")

# Agent needs-input: recruiter-facing questions raised by the orchestrator
# during a cohort tick. Listed + answered inline on the role page.
from .agent_runtime.needs_input_routes import router as agent_needs_input_router  # noqa: E402

app.include_router(agent_needs_input_router, prefix="/api/v1")

# ---------------------------------------------------------------------------
# MCP server (read-only) — mounted at /mcp. Accepts a Tali API key or a
# fastapi-users JWT. See app/mcp/server.py for the tool surface.
# ---------------------------------------------------------------------------
from .mcp import mcp_app as _mcp_server  # noqa: E402

app.mount("/mcp", _mcp_server.streamable_http_app())


def _health_payload(*, include_s3: bool = False) -> dict:
    """Build protected readiness diagnostics for operators."""
    db_ok = False
    redis_ok = False
    db = None
    try:
        from sqlalchemy import text
        from .platform.database import SessionLocal

        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    finally:
        if db is not None:
            db.close()
    try:
        import redis
        r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        redis_ok = bool(r.ping())
    except Exception:
        redis_ok = False
        r = None

    try:
        from .services.agent_worker_health import worker_beat_status

        agent_worker = worker_beat_status(client=r) if r is not None else {
            "ready": False,
            "reason": "redis_unavailable",
            "age_seconds": None,
        }
    except Exception:
        agent_worker = {
            "ready": False,
            "reason": "health_probe_error",
            "age_seconds": None,
        }

    # Resend powers verification, password-reset, and team-invite emails. When
    # disabled, on_after_register silently returns; surface that on authenticated
    # operator health so missing transactional email configuration is visible.
    resend_key = (settings.RESEND_API_KEY or "").strip().lower()
    workable_connector_enabled = not bool(settings.MVP_DISABLE_WORKABLE)
    workable_oauth_app_configured = _is_configured_secret(
        settings.WORKABLE_CLIENT_ID
    ) and _is_configured_secret(settings.WORKABLE_CLIENT_SECRET)
    integrations = {
        "e2b_configured": _is_configured_secret(settings.E2B_API_KEY),
        "claude_configured": _is_configured_secret(settings.ANTHROPIC_API_KEY),
        # Report connector capability only. Tenant connection truth is org-scoped
        # on OrgResponse.active_ats; exposing counts here leaks business metadata.
        # Keep the legacy key about connector availability so a direct-token tenant
        # is not called "unconfigured" merely because global OAuth is absent.
        "workable_configured": workable_connector_enabled,
        "workable_connector_enabled": workable_connector_enabled,
        "workable_oauth_app_configured": workable_oauth_app_configured,
        "bullhorn_connector_enabled": bool(settings.BULLHORN_ENABLED),
        "stripe_configured": _is_configured_secret(settings.STRIPE_API_KEY),
        "resend_configured": _is_configured_secret(settings.RESEND_API_KEY) and resend_key != "skip",
    }
    s3_health = None
    if include_s3:
        from .services.s3_health_diagnostics import sanitize_status_payload, status_payload

        try:
            from .services.s3_service import s3_status
            s3_health = sanitize_status_payload(s3_status())
        except Exception:
            s3_health = status_payload(False, "probe_error")
    production_like = is_production_like(settings)
    usage_meter_live = bool(settings.USAGE_METER_LIVE)
    usage_meter_emergency_override = bool(
        settings.USAGE_METER_ALLOW_PRODUCTION_SHADOW_EMERGENCY
    )
    usage_meter_override_active = bool(
        production_like
        and not usage_meter_live
        and usage_meter_emergency_override
    )
    usage_meter_ready = not production_like or usage_meter_live
    if usage_meter_live:
        usage_meter_mode = "live"
    elif usage_meter_override_active:
        usage_meter_mode = "shadow_emergency_override"
    else:
        usage_meter_mode = "shadow"

    # S3 down doesn't degrade the API: cv_text persists in Postgres
    # regardless. Production shadow metering does degrade readiness because
    # credit debits and spend gates are disabled; the emergency override only
    # permits boot, it must remain operationally visible.
    status_str = (
        "healthy"
        if (
            db_ok
            and redis_ok
            and usage_meter_ready
            and (not production_like or bool(agent_worker.get("ready")))
        )
        else "degraded"
    )
    payload = {
        "status": status_str,
        "service": "taali-api",
        "database": db_ok,
        "redis": redis_ok,
        "agent_worker": agent_worker,
        "s3": s3_health,
        "usage_meter": {
            "mode": usage_meter_mode,
            "live": usage_meter_live,
            "ready": usage_meter_ready,
            "production_emergency_override": usage_meter_override_active,
        },
        "integrations": integrations,
    }
    if r is not None:
        try:
            r.close()
        except Exception:
            pass
    return payload


@app.get("/health")
def health_check():
    # Cheap public liveness only: no database, Redis, provider calls, model
    # names, queue ages, or deployment configuration are exposed.
    return {"status": "ok", "service": "taali-api"}


@app.get("/ready")
def readiness_check():
    payload = _health_payload(include_s3=False)
    return JSONResponse(
        status_code=200 if payload.get("status") == "healthy" else 503,
        content={"status": payload.get("status"), "service": "taali-api"},
    )


@app.get("/admin/health", **_health_contracts.ADMIN_HEALTH_OPENAPI)
def admin_health(_admin: None = Depends(require_admin_secret)):
    return _health_payload(include_s3=True)


@app.get("/healthz/graphiti", deprecated=True, include_in_schema=False)
@app.get("/admin/health/graphiti", **_health_contracts.GRAPHITI_HEALTH_OPENAPI)
def admin_graphiti_health(_admin: None = Depends(require_admin_secret)):
    """Run the authenticated Graphiti component health probe.

    ``unconfigured`` means Neo4j or Voyage credentials are absent. Initializing
    and error states use HTTP 503; configured healthy probes return HTTP 200.
    """
    from .candidate_graph.client import healthcheck

    payload = healthcheck()
    return JSONResponse(
        status_code=503 if payload.get("status") in {"initializing", "error"} else 200,
        content=payload,
    )


@app.get("/healthz/github", deprecated=True, include_in_schema=False)
@app.get("/admin/health/github", **_health_contracts.GITHUB_HEALTH_OPENAPI)
def admin_github_provisioning_health(
    _admin: None = Depends(require_admin_secret),
):
    """On-demand probe of the GitHub credential assessment repo provisioning needs.

    Returns ``{ok, status_code, detail, org}``. ``ok=false`` (e.g. a 401) means an
    expired/invalid GITHUB_TOKEN — candidates cannot start assessments until it is
    rotated on all services. Mirrors the proactive
    ``assessment_provisioning_healthcheck`` beat; handy to curl right after a token
    rotation. Not the Railway healthcheck (that's ``/health``) — this makes a live
    GitHub call.
    """
    # Kept out of the public health surface: each request consumes GitHub API
    # quota and reveals credential/organization state. /healthz/github remains
    # a hidden compatibility alias for existing operator monitors.
    from .services.github_credentials import verify_github_credentials

    return verify_github_credentials(org=settings.GITHUB_ORG, token=settings.GITHUB_TOKEN)


@app.get("/admin/graphiti/stats")
def graphiti_stats(request: Request):
    """Return CV + graph sync counts for operational visibility."""
    from .platform.database import SessionLocal
    from .models.candidate import Candidate
    from .models.graph_sync_state import GraphSyncState
    from sqlalchemy import func

    _require_admin(request)

    db = SessionLocal()
    try:
        total_candidates = db.query(func.count(Candidate.id)).filter(Candidate.deleted_at.is_(None)).scalar()
        with_cv = db.query(func.count(Candidate.id)).filter(
            Candidate.deleted_at.is_(None),
            Candidate.cv_text.isnot(None),
            Candidate.cv_text != "",
        ).scalar()
        synced_to_graph = db.query(func.count(GraphSyncState.candidate_id)).scalar()
    finally:
        db.close()

    from .candidate_graph import client as graph_client
    neo4j_ok = False
    neo4j_node_count = None
    if graph_client.is_configured():
        try:
            graphiti = graph_client.get_graphiti()
            result = graph_client.run_async(
                graphiti.driver.execute_query("MATCH (n) RETURN count(n) AS c"),
                timeout=10.0,
            )
            # neo4j driver returns EagerResult; records[0]["c"] gives the count
            neo4j_node_count = result.records[0]["c"] if result and result.records else None
            neo4j_ok = True
        except Exception as exc:
            logger.error("Failed to query Graphiti node count error_type=%s", type(exc).__name__)
            neo4j_node_count = "unavailable"

    sample_companies = []
    sample_facts = []
    if neo4j_ok:
        try:
            r2 = graph_client.run_async(
                graphiti.driver.execute_query(
                    "MATCH (n:Entity) WHERE n.name IS NOT NULL RETURN DISTINCT n.name AS name LIMIT 30"
                ),
                timeout=10.0,
            )
            sample_companies = [rec["name"] for rec in (r2.records or [])]
            r3 = graph_client.run_async(
                graphiti.driver.execute_query(
                    "MATCH ()-[e:RELATES_TO]->() WHERE e.fact IS NOT NULL RETURN e.fact AS fact LIMIT 10"
                ),
                timeout=10.0,
            )
            sample_facts = [rec["fact"] for rec in (r3.records or [])]
        except Exception as exc:
            logger.error("Failed to query Graphiti diagnostic samples error_type=%s", type(exc).__name__)
            sample_companies = []

    return {
        "candidates": {"total": total_candidates, "with_cv_text": with_cv},
        "graph_sync_state": {"synced_candidates": synced_to_graph},
        "neo4j": {"ok": neo4j_ok, "total_nodes": neo4j_node_count, "sample_entities": sample_companies, "sample_facts": sample_facts},
    }


@app.post("/admin/graphiti/backfill")
def graphiti_backfill_all(request: Request):
    """Trigger a full Graphiti backfill for all organisations.

    Optional query param: ``since_year=2026`` limits to candidates created
    on or after 1 Jan of that year. Returns 202 immediately; backfill runs
    as a background thread. Check Railway logs for progress and final summary.
    """
    from .platform.database import SessionLocal
    from .candidate_graph.sync import sync_all_organizations
    import threading

    _require_admin(request)

    since_year_str = request.query_params.get("since_year")
    since_year = int(since_year_str) if since_year_str and since_year_str.isdigit() else None
    cv_only = request.query_params.get("cv_only", "false").lower() == "true"

    def _run():
        import logging
        log = logging.getLogger("taali.candidate_graph.backfill")
        db = SessionLocal()
        try:
            result = sync_all_organizations(db, since_year=since_year, cv_only=cv_only)
            log.info("Graphiti backfill complete: %s", result)
        except Exception as _exc:
            log.error("Graphiti backfill failed error_type=%s", type(_exc).__name__)
        finally:
            db.close()

    threading.Thread(target=_run, name="graphiti-backfill", daemon=True).start()
    return {
        "status": "started",
        "since_year": since_year,
        "cv_only": cv_only,
        "message": "Backfill running in background — check Railway logs for progress",
    }



@app.post("/admin/cv-score/cancel-all")
def admin_cancel_all_scoring(request: Request):
    """Emergency: cancel ALL pending/running cv_score_jobs across every role.

    Sets Redis cancel flags for every role that has active jobs, then bulk-marks
    all PENDING and RUNNING jobs as error=cancelled_by_recruiter.

    Uses X-Admin-Secret header for auth.
    """
    from .platform.config import settings as _settings
    from .platform.database import SessionLocal
    from .models.cv_score_job import CvScoreJob, SCORE_JOB_PENDING, SCORE_JOB_RUNNING, SCORE_JOB_ERROR
    from datetime import datetime, timezone

    _require_admin(request)

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        # Find every role with active jobs
        active_role_ids = [
            row[0]
            for row in db.query(CvScoreJob.role_id)
            .filter(CvScoreJob.status.in_([SCORE_JOB_PENDING, SCORE_JOB_RUNNING]))
            .distinct()
            .all()
        ]

        # Set Redis cancel flags
        redis_ok = []
        redis_fail = []
        try:
            import redis as _redis  # type: ignore
            r = _redis.Redis.from_url(_settings.REDIS_URL)
            for rid in active_role_ids:
                try:
                    r.set(f"batch_score:cancel:{rid}", "1", ex=3600)
                    redis_ok.append(rid)
                except Exception:
                    redis_fail.append(rid)
        except Exception:
            redis_fail = active_role_ids
            pass

        # Bulk-mark all active jobs as cancelled
        cancelled_count = (
            db.query(CvScoreJob)
            .filter(CvScoreJob.status.in_([SCORE_JOB_PENDING, SCORE_JOB_RUNNING]))
            .update(
                {"status": SCORE_JOB_ERROR, "error_message": "cancelled_by_recruiter", "finished_at": now},
                synchronize_session=False,
            )
        )
        db.commit()

        return {
            "ok": True,
            "roles_cancelled": active_role_ids,
            "jobs_cancelled": cancelled_count,
            "redis_flags_set": redis_ok,
            "redis_flags_failed": redis_fail,
        }
    finally:
        db.close()


@app.post("/admin/pre-screen-rejects/backfill")
def admin_backfill_pre_screen_rejects(request: Request, organization_id: int | None = None):
    """Surface stranded below-threshold candidates as Decision Hub cards.

    Calls ``backfill_existing_below_threshold`` (idempotent — re-running
    creates at most one decision per application). Optional
    ``organization_id`` query param scopes to a single org; omit to run
    org-wide. Auth via ``X-Admin-Secret``.

    Returns ``{created, skipped_existing, failed}``.
    """
    from .platform.database import SessionLocal
    from .services.pre_screen_decision_emitter import backfill_existing_below_threshold

    _require_admin(request)

    db = SessionLocal()
    try:
        result = backfill_existing_below_threshold(
            db, organization_id=organization_id
        )
        return {"ok": True, **result}
    finally:
        db.close()


@app.post("/admin/pre-screen-rejects/rewrite-reasoning")
def admin_rewrite_pre_screen_reject_reasoning(request: Request, organization_id: int | None = None):
    """Rewrite stale pre-screen reject card text to the qualitative format.

    Existing pending ``skip_assessment_reject`` cards created before the
    reasoning dropped the numeric "(score: X, threshold: Y)" template keep
    that text until revived. This rewrites them in place. Idempotent.
    Optional ``organization_id`` scopes to one org. Auth via ``X-Admin-Secret``.

    Returns ``{updated, scanned}``.
    """
    from .platform.database import SessionLocal
    from .services.pre_screen_decision_emitter import backfill_pre_screen_reject_reasoning

    _require_admin(request)

    db = SessionLocal()
    try:
        result = backfill_pre_screen_reject_reasoning(
            db, organization_id=organization_id
        )
        return {"ok": True, **result}
    finally:
        db.close()


@app.post("/admin/pre-screen-rejects/supersede-mislabeled")
def admin_supersede_mislabeled_pre_screen_rejects(
    request: Request, organization_id: int | None = None, dry_run: bool = False
):
    """Discard pending pre-screen reject cards that should never have been
    pre-screen rejects because the candidate was fully cv_match-scored.

    Discards the A∪B cohorts (passed pre-screen, or cleared by the full
    score) and leaves genuine pre-screen rejects (C) alone; the agent
    re-triages the discarded ones on the authoritative cv_match score. Pass
    ``dry_run=true`` to preview counts without writing. Auth via
    ``X-Admin-Secret``. Returns ``{discarded, scanned, skipped_human}``.
    """
    from .platform.database import SessionLocal
    from .services.pre_screen_decision_emitter import (
        supersede_mislabeled_pre_screen_rejects,
    )

    _require_admin(request)

    db = SessionLocal()
    try:
        result = supersede_mislabeled_pre_screen_rejects(
            db, organization_id=organization_id, dry_run=dry_run
        )
        return {"ok": True, "dry_run": dry_run, **result}
    finally:
        db.close()


@app.post("/admin/decisions/discard-on-closed")
def admin_discard_decisions_on_closed(
    request: Request, organization_id: int | None = None, dry_run: bool = False
):
    """P1: discard pending agent decisions whose application is already closed."""
    from .platform.database import SessionLocal
    from .services.pre_screen_decision_emitter import (
        backfill_discard_decisions_on_closed_apps,
    )

    _require_admin(request)
    db = SessionLocal()
    try:
        result = backfill_discard_decisions_on_closed_apps(
            db, organization_id=organization_id, dry_run=dry_run
        )
        return {"ok": True, "dry_run": dry_run, **result}
    finally:
        db.close()


@app.post("/admin/scores/rederive-recommendations")
def admin_rederive_recommendations(
    request: Request, organization_id: int | None = None, dry_run: bool = False
):
    """P2: re-derive pre_screen_recommendation labels to match current scores."""
    from .platform.database import SessionLocal
    from .services.pre_screen_decision_emitter import (
        backfill_recommendations_from_cvmatch,
    )

    _require_admin(request)
    db = SessionLocal()
    try:
        result = backfill_recommendations_from_cvmatch(
            db, organization_id=organization_id, dry_run=dry_run
        )
        return {"ok": True, "dry_run": dry_run, **result}
    finally:
        db.close()


@app.post("/admin/scores/backfill-summaries")
def admin_backfill_summaries(
    request: Request, organization_id: int | None = None, dry_run: bool = False
):
    """P3: fill missing pre_screen_evidence.summary from cv_match_details."""
    from .platform.database import SessionLocal
    from .services.pre_screen_decision_emitter import backfill_summaries_from_cvmatch

    _require_admin(request)
    db = SessionLocal()
    try:
        result = backfill_summaries_from_cvmatch(
            db, organization_id=organization_id, dry_run=dry_run
        )
        return {"ok": True, "dry_run": dry_run, **result}
    finally:
        db.close()


@app.get("/admin/scores/gate-divergence")
def admin_gate_divergence(request: Request, organization_id: int | None = None):
    """P4 monitor: pre-screen gate vs full cv_match score disagreement."""
    from .platform.database import SessionLocal
    from .services.pre_screen_decision_emitter import pre_screen_gate_divergence_report

    _require_admin(request)
    db = SessionLocal()
    try:
        return {"ok": True, **pre_screen_gate_divergence_report(db, organization_id=organization_id)}
    finally:
        db.close()


@app.post("/admin/pre-screen-rejects/repair-passed")
def admin_repair_passed_prescreen(
    request: Request, organization_id: int | None = None, dry_run: bool = False
):
    """Discard reject cards + clear false 'Below threshold' labels for
    candidates the pre-screen gate actually passed (decision='yes')."""
    from .platform.database import SessionLocal
    from .services.pre_screen_decision_emitter import (
        repair_passed_prescreen_contamination,
    )

    _require_admin(request)
    db = SessionLocal()
    try:
        result = repair_passed_prescreen_contamination(
            db, organization_id=organization_id, dry_run=dry_run
        )
        return {"ok": True, "dry_run": dry_run, **result}
    finally:
        db.close()


@app.post("/admin/decisions/discard-on-agent-off")
def admin_discard_on_agent_off(
    request: Request, organization_id: int | None = None, dry_run: bool = False
):
    """Discard pending agent decisions on roles whose agent is disabled."""
    from .platform.database import SessionLocal
    from .services.pre_screen_decision_emitter import (
        backfill_discard_decisions_on_agent_off_roles,
    )

    _require_admin(request)
    db = SessionLocal()
    try:
        result = backfill_discard_decisions_on_agent_off_roles(
            db, organization_id=organization_id, dry_run=dry_run
        )
        return {"ok": True, "dry_run": dry_run, **result}
    finally:
        db.close()


@app.post("/admin/scores/normalize-recommendation-labels")
def admin_normalize_recommendation_labels(
    request: Request, organization_id: int | None = None, dry_run: bool = False
):
    """Replace raw cv_match recommendation enums leaked into
    pre_screen_recommendation ('no'/'lean_no'/...) with proper labels."""
    from .platform.database import SessionLocal
    from .services.pre_screen_decision_emitter import (
        backfill_normalize_raw_recommendation_labels,
    )

    _require_admin(request)
    db = SessionLocal()
    try:
        result = backfill_normalize_raw_recommendation_labels(
            db, organization_id=organization_id, dry_run=dry_run
        )
        return {"ok": True, "dry_run": dry_run, **result}
    finally:
        db.close()


@app.post("/admin/scores/sample-prescreen-calibration")
def admin_sample_prescreen_calibration(
    request: Request, organization_id: int | None = None, limit: int = 50
):
    """Backend-only: shadow-score a random sample of pre-screen rejects with
    full cv_match to build reject-inference training data. Results go to
    prescreen_calibration_samples — never to the application or the UI. This
    is the manual trigger for the weekly job."""
    from .platform.database import SessionLocal
    from .services.prescreen_calibration import (
        PRESCREEN_SHADOW_SCORE_MAX_LIMIT,
        sample_and_shadow_score_rejects,
    )

    _require_admin(request)
    if not 1 <= limit <= PRESCREEN_SHADOW_SCORE_MAX_LIMIT:
        raise HTTPException(
            status_code=422,
            detail=f"limit must be between 1 and {PRESCREEN_SHADOW_SCORE_MAX_LIMIT}",
        )
    db = SessionLocal()
    try:
        result = sample_and_shadow_score_rejects(
            db, limit=int(limit), organization_id=organization_id
        )
        return {"ok": True, **result}
    finally:
        db.close()


@app.post("/admin/scores/rescore-wrongly-filtered")
def admin_rescore_wrongly_filtered(
    request: Request, organization_id: int | None = None, dry_run: bool = False
):
    """Re-score apps the pre-screen gate wrongly filtered (passed pre-screen
    but skipped full scoring on a contaminated score)."""
    from .platform.database import SessionLocal
    from .services.cv_score_orchestrator import rescore_wrongly_filtered_prescreen

    _require_admin(request)
    db = SessionLocal()
    try:
        result = rescore_wrongly_filtered_prescreen(
            db, organization_id=organization_id, dry_run=dry_run
        )
        return {"ok": True, "dry_run": dry_run, **result}
    finally:
        db.close()


@app.get("/admin/graphiti/search-debug")
def graphiti_search_debug(request: Request):
    """Raw Graphiti search result shape for debugging the graph view."""
    from .candidate_graph.admin_routes import search_debug_response

    _require_admin(request)
    return search_debug_response(request)


@app.get("/admin/graphiti/cypher-debug")
def graphiti_cypher_debug(request: Request):
    """Run the actual Cypher subgraph query and show raw records."""
    from .candidate_graph.admin_routes import cypher_debug_response

    _require_admin(request)
    return cypher_debug_response(request)


@app.post("/admin/graphiti/test-episode")
def graphiti_test_episode(request: Request):
    """Send one synthetic episode to Graphiti and return success or error detail.

    Used to verify the add_episode pipeline end-to-end after setup.
    """
    from .candidate_graph.admin_routes import test_episode_response

    _require_admin(request)
    return test_episode_response(request)
