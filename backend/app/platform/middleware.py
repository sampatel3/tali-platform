import time
import uuid
import logging
from collections import defaultdict
from urllib.parse import parse_qs
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from .request_context import set_client_meta, set_request_id
from .config import settings
from ..domains.identity_access.access_policy import evaluate_login_access
from ..models.api_key import KEY_PREFIX_LIVE, KEY_PREFIX_TEST

logger = logging.getLogger("tali.middleware")

# In-memory rate limit: key -> list of request timestamps (pruned to last window_sec)
_rate_limit_store = defaultdict(list)
_RATE_WINDOW_SEC = 60

_API_KEY_PREFIXES = (KEY_PREFIX_LIVE, KEY_PREFIX_TEST)
# Bucket a tali_* key on a stable slice of the token (never the whole secret,
# which we do NOT verify here). Mirrors the displayed key prefix length in
# api_key_service (prefix label + 6 chars) so a bucket maps to one key.
_MCP_KEY_BUCKET_LEN = len(KEY_PREFIX_LIVE) + 6


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _mcp_buckets(request: Request, ip: str) -> list[tuple[str, int]]:
    """(key, max) rate buckets for one /mcp request.

    The token prefix is UNVERIFIED here, so it can never be the sole bucket:
    a spoofer rotating prefixes would mint a fresh bucket per request (limit
    bypass + unbounded store growth), and a known display prefix could burn a
    real key's bucket. So the key bucket is scoped by IP, and every request
    also counts against a per-IP guard at 4x the key limit (allows a few keys
    behind one NAT at full rate while bounding spoof rotation and memory).
    """
    per_key = settings.MCP_RATE_LIMIT_PER_MINUTE
    candidates = []
    authz = request.headers.get("authorization")
    if authz:
        parts = authz.split()
        candidates.append(parts[1] if len(parts) == 2 else authz)
    x_api_key = request.headers.get("x-api-key")
    if x_api_key:
        candidates.append(x_api_key)
    for token in candidates:
        if token.startswith(_API_KEY_PREFIXES):
            return [
                (f"mcp:key:{token[:_MCP_KEY_BUCKET_LEN]}:{ip}", per_key),
                (f"mcp:ip:{ip}", per_key * 4),
            ]
    return [(f"mcp:ip:{ip}", per_key)]


def _rate_limit_key(ip: str, path: str) -> str:
    if (
        "/api/v1/auth/login" in path
        or "/api/v1/auth/jwt/login" in path
        or "/api/v1/auth/register" in path
        or "/api/v1/auth/forgot-password" in path
        or "/api/v1/auth/accept-invite" in path
    ):
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
        if path == "/mcp" or path.startswith("/mcp/"):
            # Public MCP mount (JWT or tali_* API key). Multi-bucket: per
            # unverified-key-prefix scoped by IP, plus a per-IP guard.
            buckets = _mcp_buckets(request, ip)
        else:
            key = _rate_limit_key(ip, path)
            buckets = [(key, _rate_limit_max(key))] if key else []

        now = time.time()
        window_start = now - _RATE_WINDOW_SEC
        counted = []
        for key, max_allowed in buckets:
            if max_allowed <= 0:
                # Disabled bucket (e.g. MCP_RATE_LIMIT_PER_MINUTE=0).
                continue
            store = _rate_limit_store[key]
            store[:] = [t for t in store if t > window_start]
            if len(store) >= max_allowed:
                logger.warning("Rate limit exceeded key=%s path=%s", key, path)
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Please try again later."},
                )
            counted.append(store)
        for store in counted:
            store.append(now)
        return await call_next(request)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        set_request_id(request_id)
        set_client_meta(_get_client_ip(request), request.headers.get("user-agent"))
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


class EnterpriseAccessMiddleware(BaseHTTPMiddleware):
    """Enforce org-level SSO policy for password-based auth endpoints."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method != "POST":
            return await call_next(request)

        if path not in {"/api/v1/auth/jwt/login", "/api/v1/auth/forgot-password"}:
            return await call_next(request)

        email = ""
        try:
            body = await request.body()
            if not body:
                return await call_next(request)
            if path.endswith("/jwt/login"):
                params = parse_qs(body.decode("utf-8", errors="ignore"))
                email = (params.get("username") or [""])[0].strip().lower()
            else:
                import json as _json

                payload = _json.loads(body.decode("utf-8", errors="ignore"))
                if isinstance(payload, dict):
                    email = str(payload.get("email") or "").strip().lower()
        except Exception:
            # If parsing fails, don't block endpoint behavior.
            return await call_next(request)

        if not email:
            return await call_next(request)

        from ..platform.database import SessionLocal
        from ..models.user import User
        from ..models.organization import Organization
        from ..domains.identity_access.access_policy import email_domain, normalize_allowed_domains

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email).first()
            if user and user.organization_id:
                org = db.query(Organization).filter(Organization.id == user.organization_id).first()
                decision = evaluate_login_access(
                    email=email,
                    sso_enforced=bool(getattr(org, "sso_enforced", False)) if org else False,
                    organization_id=user.organization_id,
                )
                if not decision.allowed:
                    return JSONResponse(
                        status_code=403,
                        content={"detail": decision.reason or "Access denied by organization policy."},
                    )
            else:
                domain = email_domain(email)
                if domain:
                    sso_orgs = db.query(Organization).filter(Organization.sso_enforced == True).all()  # noqa: E712
                    for org in sso_orgs:
                        allowed_domains = normalize_allowed_domains(getattr(org, "allowed_email_domains", None))
                        if allowed_domains and domain in allowed_domains:
                            decision = evaluate_login_access(
                                email=email,
                                sso_enforced=True,
                                organization_id=org.id,
                            )
                            return JSONResponse(
                                status_code=403,
                                content={"detail": decision.reason or "Access denied by organization policy."},
                            )
        finally:
            db.close()

        return await call_next(request)
