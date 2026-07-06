import logging as _logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
# Friendly messages for API error codes (returned to frontend)
_API_ERROR_MESSAGES = {
    "REGISTER_USER_ALREADY_EXISTS": "An account with this email already exists. Sign in instead or use a different email.",
}
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse
from .platform.brand import BRAND_APP_DESCRIPTION, BRAND_NAME
from .platform.config import settings
from .platform.logging import setup_logging
from .platform.middleware import RequestLoggingMiddleware, RateLimitMiddleware, EnterpriseAccessMiddleware
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
    except Exception:  # pragma: no cover — instrumentation must never block boot
        logger.exception("Failed to install Anthropic wire-tap")
    # Wire candidate_graph SQLAlchemy listeners (no-op when Graphiti is unset).
    try:
        from .candidate_graph.listeners import register_listeners

        register_listeners()
    except Exception:  # pragma: no cover — listener install must never block boot
        logger.exception("Failed to register candidate_graph listeners")
    # Kick off Graphiti init in a background thread so Neo4j async resources
    # are created on the shared background event loop before the first real
    # request arrives. The healthcheck returns "initializing" until ready.
    try:
        from .candidate_graph import client as _gc
        if _gc.is_configured():
            import threading as _t
            _t.Thread(target=_gc.get_graphiti, name="graphiti-init", daemon=True).start()
    except Exception:
        logger.exception("Failed to start Graphiti background init")
    # FastAPI's custom-lifespan path skips Starlette's auto-propagation to
    # mounted sub-apps, so the MCP server's StreamableHTTPSessionManager
    # task group has to be started explicitly here.
    from .mcp import mcp_app as _mcp_server

    # Lazy-create the session manager (no-op when the streamable_http_app
    # mount has already done so on first import).
    if _mcp_server._session_manager is None:
        _mcp_server.streamable_http_app()
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
    except Exception:  # pragma: no cover — defensive
        logger.exception("Failed to close Graphiti on shutdown")


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


def _sanitize_errors(errors: list) -> list:
    """Ensure validation error details are JSON-serializable."""

    def _json_safe(value):
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(v) for v in value]
        if isinstance(value, BaseException):
            return str(value)
        return str(value)

    return [_json_safe(err) for err in errors]


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Log validation errors with detail so we can diagnose 422s."""
    _val_logger.warning(
        "Validation error on %s %s: %s | body=%s",
        request.method,
        request.url.path,
        exc.errors(),
        exc.body,
    )
    return JSONResponse(
        status_code=422,
        content={"detail": _sanitize_errors(exc.errors())},
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

def _normalize_origin(origin: str | None) -> str | None:
    cleaned = (origin or "").strip().rstrip("/")
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return cleaned


def _frontend_origins(frontend_url: str | None) -> list[str]:
    primary = _normalize_origin(frontend_url)
    if not primary:
        return []

    origins = [primary]
    parsed = urlparse(primary)
    host = parsed.hostname or ""
    if host.startswith("www."):
        port = f":{parsed.port}" if parsed.port else ""
        origins.append(f"{parsed.scheme}://{host[4:]}{port}")
    return origins


def _build_cors_origins(frontend_url: str | None, extra_origins: str | None) -> list[str]:
    origins = [
        *_frontend_origins(frontend_url),
        "http://localhost:5173",
        "http://localhost:3000",
    ]
    if extra_origins:
        origins.extend(_normalize_origin(origin) for origin in extra_origins.split(","))

    deduped = []
    seen = set()
    for origin in origins:
        if not origin or origin in seen:
            continue
        seen.add(origin)
        deduped.append(origin)
    return deduped


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
)

# Rate limiting (auth and assessment endpoints)
app.add_middleware(RateLimitMiddleware)

# Enterprise access controls (SSO enforcement on password-auth endpoints)
app.add_middleware(EnterpriseAccessMiddleware)

# Request logging
app.add_middleware(RequestLoggingMiddleware)

# Sentry (optional)
if settings.SENTRY_DSN and settings.SENTRY_DSN.startswith("https://"):
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=0.1,
        integrations=[FastApiIntegration(), SqlalchemyIntegration()],
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
from .domains.assessments_runtime.scoring_routes import router as scoring_router
from .domains.assessments_runtime.careers_routes import (
    router as careers_public_router,
)
from .domains.assessments_runtime.pipeline_stages_routes import (
    router as pipeline_stages_router,
)
from .domains.assessments_runtime.job_hiring_team_routes import (
    router as job_hiring_team_router,
)
from .domains.assessments_runtime.offer_template_routes import (
    router as offer_template_router,
)
from .domains.identity_access.user_routes import router as users_router
from .api.v1.workable import router as workable_router
from .api.v1.auth import router as auth_router
from .api.v1.background_jobs import router as background_jobs_router
from .domains.share_links import (
    public_router as share_links_public_router,
    router as share_links_router,
)
from .domains.top_reports.routes import public_router as top_reports_public_router

# FastAPI-Users auth routers
app.include_router(
    fastapi_users.get_auth_router(auth_backend),
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
app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/api/v1/users",
    tags=["users"],
)

app.include_router(assessments_router, prefix="/api/v1")
app.include_router(organizations_router, prefix="/api/v1")
app.include_router(org_criteria_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(webhooks_router, prefix="/api/v1")
app.include_router(tasks_router, prefix="/api/v1")
app.include_router(analytics_router, prefix="/api/v1")
app.include_router(billing_router, prefix="/api/v1")
app.include_router(candidates_router, prefix="/api/v1")
app.include_router(roles_router, prefix="/api/v1")
app.include_router(scoring_router, prefix="/api/v1")
app.include_router(pipeline_stages_router, prefix="/api/v1")
app.include_router(job_hiring_team_router, prefix="/api/v1")
app.include_router(offer_template_router, prefix="/api/v1")
app.include_router(workable_router, prefix="/api/v1")
app.include_router(background_jobs_router, prefix="/api/v1")
app.include_router(share_links_router, prefix="/api/v1")
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
app.include_router(careers_public_router)

# cv_match_v3.0 admin + override surface (gated server-side; flag controls runner)
from .cv_matching.routes import (
    admin_router as cv_match_admin_router,
    override_router as cv_match_override_router,
)
app.include_router(cv_match_admin_router, prefix="/api/v1")
app.include_router(cv_match_override_router, prefix="/api/v1")

# Taali Chat (in-product agentic chat that consumes the same tool surface
# as the public MCP server).
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
# MCP server (read-only) — mounted at /mcp. Bearer JWT auth, same secret as
# /api/v1/auth/jwt/login. See app/mcp/server.py for the tool surface.
# ---------------------------------------------------------------------------
from .mcp import mcp_app as _mcp_server  # noqa: E402

app.mount("/mcp", _mcp_server.streamable_http_app())


@app.get("/health")
def health_check():
    db_ok = False
    redis_ok = False
    try:
        from sqlalchemy import text
        from .platform.database import SessionLocal
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        db_ok = True
    except Exception:
        db_ok = False
    try:
        import redis
        r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        redis_ok = bool(r.ping())
    except Exception:
        redis_ok = False

    # Resend powers verification, password-reset, and team-invite emails.
    # When the key is missing or set to "skip", on_after_register silently
    # logs and returns — the user gets no email. Surface here so /health
    # is the one place to confirm transactional email is wired.
    resend_key = (settings.RESEND_API_KEY or "").strip().lower()
    integrations = {
        "e2b_configured": _is_configured_secret(settings.E2B_API_KEY),
        "claude_configured": _is_configured_secret(settings.ANTHROPIC_API_KEY),
        "workable_configured": _is_configured_secret(settings.WORKABLE_CLIENT_ID) and _is_configured_secret(settings.WORKABLE_CLIENT_SECRET),
        "stripe_configured": _is_configured_secret(settings.STRIPE_API_KEY),
        "resend_configured": _is_configured_secret(settings.RESEND_API_KEY) and resend_key != "skip",
    }

    try:
        from .services.s3_service import s3_status
        s3_health = s3_status()
    except Exception:
        s3_health = {"available": False, "reason": "probe_error"}

    # S3 down doesn't degrade the API: cv_text persists in Postgres
    # regardless. Surface the state for ops visibility but don't change
    # the top-level status_str unless DB or Redis is actually broken.
    status_str = "healthy" if db_ok and redis_ok else "degraded"
    return {
        "status": status_str,
        "service": "taali-api",
        "database": db_ok,
        "redis": redis_ok,
        "s3": s3_health,
        "integrations": integrations,
    }


@app.get("/healthz/graphiti")
def graphiti_health():
    """Per-component health probe used by the Railway setup verification step.

    Returns ``{status: ok|unconfigured|error}``. ``unconfigured`` means
    NEO4J_URI or VOYAGE_API_KEY is empty — graph features are disabled
    by design, not a fault.
    """
    from .candidate_graph.client import healthcheck

    return healthcheck()


@app.get("/admin/graphiti/stats")
def graphiti_stats(request: Request):
    """Return CV + graph sync counts for operational visibility."""
    from .platform.config import settings
    from .platform.database import SessionLocal
    from .models.candidate import Candidate
    from .models.graph_sync_state import GraphSyncState
    from sqlalchemy import func

    admin_secret = getattr(settings, "ADMIN_SECRET", "") or ""
    provided = request.headers.get("X-Admin-Secret", "")
    if not admin_secret or provided != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

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
            neo4j_node_count = f"error: {exc}"

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
            sample_companies = [f"error: {exc}"]

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
    from .platform.config import settings
    from .platform.database import SessionLocal
    from .candidate_graph.sync import sync_all_organizations
    import threading

    admin_secret = getattr(settings, "ADMIN_SECRET", "") or ""
    provided = request.headers.get("X-Admin-Secret", "")
    if not admin_secret or provided != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

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
            log.exception("Graphiti backfill failed: %s: %s", type(_exc).__name__, _exc)
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

    admin_secret = getattr(_settings, "ADMIN_SECRET", "") or ""
    provided = request.headers.get("X-Admin-Secret", "")
    if not admin_secret or provided != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

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
        except Exception as exc:
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
    from .platform.config import settings as _settings
    from .platform.database import SessionLocal
    from .services.pre_screen_decision_emitter import backfill_existing_below_threshold

    admin_secret = getattr(_settings, "ADMIN_SECRET", "") or ""
    provided = request.headers.get("X-Admin-Secret", "")
    if not admin_secret or provided != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

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
    from .platform.config import settings as _settings
    from .platform.database import SessionLocal
    from .services.pre_screen_decision_emitter import backfill_pre_screen_reject_reasoning

    admin_secret = getattr(_settings, "ADMIN_SECRET", "") or ""
    provided = request.headers.get("X-Admin-Secret", "")
    if not admin_secret or provided != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

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
    from .platform.config import settings as _settings
    from .platform.database import SessionLocal
    from .services.pre_screen_decision_emitter import (
        supersede_mislabeled_pre_screen_rejects,
    )

    admin_secret = getattr(_settings, "ADMIN_SECRET", "") or ""
    provided = request.headers.get("X-Admin-Secret", "")
    if not admin_secret or provided != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    db = SessionLocal()
    try:
        result = supersede_mislabeled_pre_screen_rejects(
            db, organization_id=organization_id, dry_run=dry_run
        )
        return {"ok": True, "dry_run": dry_run, **result}
    finally:
        db.close()


def _require_admin(request: Request) -> None:
    from .platform.config import settings as _settings

    admin_secret = getattr(_settings, "ADMIN_SECRET", "") or ""
    if not admin_secret or request.headers.get("X-Admin-Secret", "") != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")


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
    from .services.prescreen_calibration import sample_and_shadow_score_rejects

    _require_admin(request)
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
    from .platform.config import settings
    from .candidate_graph import client as graph_client

    admin_secret = getattr(settings, "ADMIN_SECRET", "") or ""
    if not admin_secret or request.headers.get("X-Admin-Secret", "") != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not graph_client.is_configured():
        return {"status": "unconfigured"}

    query = request.query_params.get("q", "full stack developer")
    try:
        org_id = int(request.query_params.get("org_id", "0"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="org_id must be an integer")
    group_id = graph_client.group_id_for_org(org_id) if org_id else None

    graphiti = graph_client.get_graphiti()
    try:
        results = graph_client.run_async(
            graphiti.search(
                query=query,
                group_ids=[group_id] if group_id else None,
                num_results=5,
            ),
            timeout=15.0,
        )
    except Exception as exc:
        return {"error": str(exc)}

    if results is None:
        return {"results": None, "count": 0}

    items = results if isinstance(results, (list, tuple)) else getattr(results, "edges", results) or []
    out = []
    for item in list(items)[:5]:
        source = getattr(item, "source_node", None)
        target = getattr(item, "target_node", None)
        out.append({
            "type": type(item).__name__,
            "uuid": getattr(item, "uuid", None),
            "fact": getattr(item, "fact", None),
            "has_source_node": source is not None,
            "has_target_node": target is not None,
            "source_uuid": getattr(source, "uuid", None) if source else None,
            "source_name": getattr(source, "name", None) if source else None,
            "target_uuid": getattr(target, "uuid", None) if target else None,
            "target_name": getattr(target, "name", None) if target else None,
            "group_id": getattr(item, "group_id", None),
        })
    return {"query": query, "group_id": group_id, "count": len(list(items)), "results": out}


@app.get("/admin/graphiti/cypher-debug")
def graphiti_cypher_debug(request: Request):
    """Run the actual Cypher subgraph query and show raw records."""
    from .platform.config import settings
    from .candidate_graph import client as graph_client

    admin_secret = getattr(settings, "ADMIN_SECRET", "") or ""
    if not admin_secret or request.headers.get("X-Admin-Secret", "") != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not graph_client.is_configured():
        return {"status": "unconfigured"}

    try:
        org_id = int(request.query_params.get("org_id", "2"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="org_id must be an integer")
    group_id = f"org-{org_id}"  # always org-{int}, no user-controlled chars

    raw_query = request.query_params.get("q", "full stack developer")
    # Cypher driver in use rejects parameterised calls — escape quotes and cap length
    # (same pattern as candidate_graph.search._cypher_subgraph_by_query).
    safe_q = raw_query.replace("\\", "\\\\").replace("'", "\\'")[:200]

    graphiti = graph_client.get_graphiti()
    out = {"group_id": group_id, "query": raw_query}

    def safe_records(r):
        rows = []
        for rec in (r.records or []):
            row = {}
            for k in rec.keys():
                v = rec[k]
                row[k] = str(v) if v is not None else None
            rows.append(row)
        return rows

    # What relationship types exist?
    try:
        r = graph_client.run_async(
            graphiti.driver.execute_query("MATCH ()-[e]->() RETURN DISTINCT type(e) AS t LIMIT 10"),
            timeout=10.0,
        )
        out["rel_types"] = [str(rec["t"]) for rec in (r.records or [])]
    except Exception as exc:
        out["rel_types_error"] = str(exc)

    # Sample edges for this org — any relationship type
    try:
        r = graph_client.run_async(
            graphiti.driver.execute_query(
                f"MATCH (s)-[e]->(t) WHERE e.group_id = '{group_id}' "
                f"RETURN type(e) AS rel, e.fact AS fact, s.name AS s, t.name AS t LIMIT 5"
            ), timeout=10.0,
        )
        out["org_edges_sample"] = safe_records(r)
    except Exception as exc:
        out["org_edges_error"] = str(exc)

    # Run the actual subgraph Cypher
    try:
        r = graph_client.run_async(
            graphiti.driver.execute_query(
                f"MATCH (s:Entity)-[e:RELATES_TO]->(t:Entity) "
                f"WHERE e.group_id = '{group_id}' "
                f"AND toLower(e.fact) CONTAINS toLower('{safe_q}') "
                f"RETURN s.uuid AS s_uuid, s.name AS s, t.uuid AS t_uuid, t.name AS t, "
                f"e.name AS e_name, e.fact AS fact LIMIT 10"
            ), timeout=10.0,
        )
        out["cypher_matches"] = safe_records(r)
    except Exception as exc:
        out["cypher_error"] = str(exc)

    return out


@app.post("/admin/graphiti/test-episode")
def graphiti_test_episode(request: Request):
    """Send one synthetic episode to Graphiti and return success or error detail.

    Used to verify the add_episode pipeline end-to-end after setup.
    """
    from .platform.config import settings
    from .candidate_graph import client as graph_client
    from .candidate_graph.episodes import Episode, dispatch

    admin_secret = getattr(settings, "ADMIN_SECRET", "") or ""
    provided = request.headers.get("X-Admin-Secret", "")
    if not admin_secret or provided != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not graph_client.is_configured():
        return {"status": "unconfigured"}

    import traceback
    from datetime import datetime, timezone
    from graphiti_core.nodes import EpisodeType  # type: ignore[import-not-found]

    ep = Episode(
        name="test-episode-debug",
        body="Subject candidate: Test Person (taali_id=0)\nThis is a test episode for connectivity verification.",
        source_description="admin.test",
        reference_time=datetime.now(timezone.utc),
        group_id="org-0",
    )
    try:
        graphiti = graph_client.get_graphiti()
        graph_client.run_async(
            graphiti.add_episode(
                name=ep.name,
                episode_body=ep.body,
                source=EpisodeType.text,
                source_description=ep.source_description,
                reference_time=ep.reference_time,
                group_id=ep.group_id,
            ),
            timeout=120.0,
        )
        return {"status": "ok", "episodes_sent": 1}
    except Exception as exc:
        return {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
