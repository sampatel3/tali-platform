import logging as _logging
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse
from .platform.config import settings
from .platform.logging import setup_logging
from .platform.middleware import RequestLoggingMiddleware, RateLimitMiddleware

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
# Disable interactive API docs in production (information disclosure)
# ---------------------------------------------------------------------------
_docs_url = None if _is_production else "/api/docs"
_openapi_url = None if _is_production else "/api/openapi.json"

app = FastAPI(
    title="TALI API",
    description="AI-augmented technical assessment platform",
    version="1.0.0",
    docs_url=_docs_url,
    openapi_url=_openapi_url,
)

_val_logger = _logging.getLogger("tali.validation")


def _sanitize_errors(errors: list) -> list:
    """Ensure validation error details are JSON-serializable (bytes → str)."""
    sanitized = []
    for err in errors:
        clean = {}
        for k, v in err.items():
            if isinstance(v, bytes):
                clean[k] = v.decode("utf-8", errors="replace")
            else:
                clean[k] = v
        sanitized.append(clean)
    return sanitized


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
from .api.v1.auth import router as auth_router
from .api.v1.assessments import router as assessments_router
from .api.v1.organizations import router as organizations_router
from .api.v1.webhooks import router as webhooks_router
from .api.v1.tasks import router as tasks_router
from .api.v1.analytics import router as analytics_router
from .api.v1.billing import router as billing_router
from .api.v1.candidates import router as candidates_router
from .api.v1.users import router as users_router

app.include_router(auth_router, prefix="/api/v1")
app.include_router(assessments_router, prefix="/api/v1")
app.include_router(organizations_router, prefix="/api/v1")
app.include_router(webhooks_router, prefix="/api/v1")
app.include_router(tasks_router, prefix="/api/v1")
app.include_router(analytics_router, prefix="/api/v1")
app.include_router(billing_router, prefix="/api/v1")
app.include_router(candidates_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")


@app.on_event("startup")
def startup():
    logger.info("TALI API started | env=%s", "production" if settings.SENTRY_DSN else "development")


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
    status_str = "healthy" if db_ok and redis_ok else "degraded"
    return {"status": status_str, "service": "tali-api", "database": db_ok, "redis": redis_ok}
