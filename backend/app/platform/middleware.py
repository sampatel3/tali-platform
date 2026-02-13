import time
import uuid
import logging
from collections import defaultdict
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from .request_context import set_request_id

logger = logging.getLogger("tali.middleware")

# In-memory rate limit: key -> list of request timestamps (pruned to last window_sec)
_rate_limit_store = defaultdict(list)
_RATE_WINDOW_SEC = 60


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit_key(ip: str, path: str) -> str:
    if "/api/v1/auth/login" in path or "/api/v1/auth/register" in path or "/api/v1/auth/forgot-password" in path:
        return f"auth:{ip}"
    # Token-based candidate endpoints (start, upload-cv) — tighter limit
    if "/api/v1/assessments/token/" in path:
        return f"candidate_token:{ip}"
    if "/api/v1/assessments/" in path and ("/start" in path or "/execute" in path or "/submit" in path or "/claude" in path or "/upload-cv" in path):
        return f"assessment:{ip}"
    return ""


def _rate_limit_max(key: str) -> int:
    if key.startswith("auth:"):
        return 10
    if key.startswith("candidate_token:"):
        return 15  # Candidate token endpoints: 15 per 60s (prevent brute-force)
    if key.startswith("assessment:"):
        return 30
    return 0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Return 429 when too many requests per IP for auth and assessment endpoints."""

    async def dispatch(self, request: Request, call_next):
        ip = _get_client_ip(request)
        path = request.url.path
        key = _rate_limit_key(ip, path)
        if not key:
            return await call_next(request)

        now = time.time()
        window_start = now - _RATE_WINDOW_SEC
        store = _rate_limit_store[key]
        store[:] = [t for t in store if t > window_start]
        max_allowed = _rate_limit_max(key)
        if len(store) >= max_allowed:
            logger.warning("Rate limit exceeded key=%s path=%s", key, path)
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please try again later."},
            )
        store.append(now)
        return await call_next(request)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        set_request_id(request_id)
        response = await call_next(request)

        duration_ms = (time.time() - start_time) * 1000

        if request.url.path != "/health":
            logger.info(
                "method=%s path=%s status=%d duration=%.1fms",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                extra={"request_id": request_id},
            )

        response.headers["X-Process-Time-Ms"] = f"{duration_ms:.1f}"
        response.headers["X-Request-ID"] = request_id

        return response
