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
    # Wire candidate_graph SQLAlchemy listeners (no-op when Graphiti is unset).
    try:
        from .candidate_graph.listeners import register_listeners

        register_listeners()
    except Exception:  # pragma: no cover — listener install must never block boot
        logger.exception("Failed to register candidate_graph listeners")
    yield
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
from .api.v1.users_fastapi import (
    UserRead,
    UserCreate,
    UserUpdate,
    auth_backend,
    fastapi_users,
)
from .api.v1.assessments import router as assessments_router
from .api.v1.organizations import router as organizations_router
from .api.v1.webhooks import router as webhooks_router
from .api.v1.tasks import router as tasks_router
from .api.v1.analytics import router as analytics_router
from .api.v1.billing import router as billing_router
from .api.v1.candidates import router as candidates_router
from .api.v1.roles import router as roles_router
from .api.v1.scoring import router as scoring_router
from .api.v1.users import router as users_router
from .api.v1.workable import router as workable_router
from .api.v1.auth import router as auth_router

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
app.include_router(users_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(webhooks_router, prefix="/api/v1")
app.include_router(tasks_router, prefix="/api/v1")
app.include_router(analytics_router, prefix="/api/v1")
app.include_router(billing_router, prefix="/api/v1")
app.include_router(candidates_router, prefix="/api/v1")
app.include_router(roles_router, prefix="/api/v1")
app.include_router(scoring_router, prefix="/api/v1")
app.include_router(workable_router, prefix="/api/v1")

# cv_match_v3.0 admin + override surface (gated server-side; flag controls runner)
from .cv_matching.routes import (
    admin_router as cv_match_admin_router,
    override_router as cv_match_override_router,
)
app.include_router(cv_match_admin_router, prefix="/api/v1")
app.include_router(cv_match_override_router, prefix="/api/v1")


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

    integrations = {
        "e2b_configured": _is_configured_secret(settings.E2B_API_KEY),
        "claude_configured": _is_configured_secret(settings.ANTHROPIC_API_KEY),
        "workable_configured": _is_configured_secret(settings.WORKABLE_CLIENT_ID) and _is_configured_secret(settings.WORKABLE_CLIENT_SECRET),
        "stripe_configured": _is_configured_secret(settings.STRIPE_API_KEY),
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


@app.post("/admin/graphiti/backfill")
def graphiti_backfill_all(request: Request):
    """Trigger a full Graphiti backfill for all organisations.

    Protected by ``ADMIN_SECRET`` env var. Returns a summary dict.
    """
    from .platform.config import settings
    from .platform.database import SessionLocal
    from .candidate_graph.sync import sync_all_organizations

    admin_secret = getattr(settings, "ADMIN_SECRET", "") or ""
    provided = request.headers.get("X-Admin-Secret", "")
    if not admin_secret or provided != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    db = SessionLocal()
    try:
        return sync_all_organizations(db)
    finally:
        db.close()
