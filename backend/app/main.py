import logging as _logging
from contextlib import asynccontextmanager
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

# Set up logging
logger = setup_logging()

# ---------------------------------------------------------------------------
# Production safety: fail-fast if SECRET_KEY is the insecure default
# ---------------------------------------------------------------------------
_INSECURE_DEFAULTS = {"dev-secret-key-change-in-production", "changeme", "secret", ""}
_is_production = bool(settings.SENTRY_DSN) or "localhost" not in settings.FRONTEND_URL
if _is_production and settings.SECRET_KEY in _INSECURE_DEFAULTS:
    raise RuntimeError(
        "CRITICAL: SECRET_KEY is set to an insecure default. "
        "Set a strong SECRET_KEY in your .env before running in production."
    )

# ---------------------------------------------------------------------------
# Claude/assessment runtime policy enforcement
# ---------------------------------------------------------------------------
# CLAUDE_MODEL defaults to claude-3-5-haiku-latest in config; no startup check needed.
if not settings.ASSESSMENT_TERMINAL_ENABLED:
    raise RuntimeError(
        "CRITICAL: ASSESSMENT_TERMINAL_ENABLED must be true. "
        "Assessments are terminal-only (Claude CLI) in production mode."
    )
if (settings.ASSESSMENT_TERMINAL_DEFAULT_MODE or "").strip().lower() != "claude_cli_terminal":
    raise RuntimeError(
        "CRITICAL: ASSESSMENT_TERMINAL_DEFAULT_MODE must be claude_cli_terminal."
    )

# ---------------------------------------------------------------------------
# Disable interactive API docs in production (information disclosure)
# ---------------------------------------------------------------------------
_docs_url = None if _is_production else "/api/docs"
_openapi_url = None if _is_production else "/api/openapi.json"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Startup
    logger.info("%s API started | env=%s", BRAND_NAME, "production" if settings.SENTRY_DSN else "development")
    yield
    # Shutdown (none needed currently)


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

# CORS: frontend URL + localhost + any extra origins (e.g. Vercel production URL)
_cors_origins = [
    settings.FRONTEND_URL,
    "http://localhost:5173",
    "http://localhost:3000",
]
if getattr(settings, "CORS_EXTRA_ORIGINS", None):
    _cors_origins.extend(o.strip() for o in settings.CORS_EXTRA_ORIGINS.split(",") if o.strip())
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
    allow_headers=["Authorization", "Content-Type", "X-Assessment-Token", "X-Requested-With"],
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
app.include_router(webhooks_router, prefix="/api/v1")
app.include_router(tasks_router, prefix="/api/v1")
app.include_router(analytics_router, prefix="/api/v1")
app.include_router(billing_router, prefix="/api/v1")
app.include_router(candidates_router, prefix="/api/v1")
app.include_router(roles_router, prefix="/api/v1")
app.include_router(scoring_router, prefix="/api/v1")
app.include_router(workable_router, prefix="/api/v1")


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

    status_str = "healthy" if db_ok and redis_ok else "degraded"
    return {
        "status": status_str,
        "service": "taali-api",
        "database": db_ok,
        "redis": redis_ok,
        "integrations": integrations,
    }
