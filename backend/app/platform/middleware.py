import time
import uuid
import logging
import re
from collections import defaultdict
from urllib.parse import parse_qs, urlparse, urlsplit, urlunsplit
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

_CANDIDATE_TOKEN_PATH_RE = re.compile(
    r"(/api/v1/assessments/token/)[^/]+",
    flags=re.IGNORECASE,
)
_CANDIDATE_RUNTIME_PATH_RE = re.compile(
    r"^/api/v1/assessments/\d+/(?:execute|repo-file|runtime-event|submit|upload-cv|claude/chat)(?:/)?$",
    flags=re.IGNORECASE,
)


def redact_sensitive_request_path(path: str) -> str:
    """Remove bearer-style candidate tokens before a path reaches logs."""
    return _CANDIDATE_TOKEN_PATH_RE.sub(r"\1[REDACTED]", str(path or ""))


def redact_candidate_request_url(raw_url: str) -> str:
    """Strip candidate query/fragment data and redact token path segments."""
    value = str(raw_url or "")
    split = urlsplit(value)
    safe_path = redact_sensitive_request_path(split.path)
    if split.scheme or split.netloc:
        return urlunsplit((split.scheme, split.netloc, safe_path, "", ""))
    return safe_path


def is_candidate_assessment_path(path: str) -> bool:
    """Whether a request is part of the unauthenticated candidate surface."""
    normalized = str(path or "")
    return bool(
        _CANDIDATE_TOKEN_PATH_RE.search(normalized)
        or _CANDIDATE_RUNTIME_PATH_RE.match(normalized)
        or normalized in {
            "/api/v1/assessments/demo/start",
            "/api/v1/assessments/demo/request",
        }
    )


def apply_candidate_security_headers(response):
    """Apply the cache/browser policy shared by candidate API responses."""
    response.headers["Cache-Control"] = "private, no-store, max-age=0, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
        "serial=(), bluetooth=(), browsing-topics=(), clipboard-read=(), "
        "clipboard-write=(), display-capture=()"
    )
    return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add global headers plus stricter cache/browser policy to candidate APIs."""

    def __init__(self, app, *, production: bool = False):
        super().__init__(app)
        self.production = bool(production)

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if is_candidate_assessment_path(request.url.path):
            apply_candidate_security_headers(response)
        if self.production:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


def scrub_sentry_candidate_request(event: dict, _hint: dict | None = None) -> dict:
    """Remove candidate bearer/session secrets and bodies from observability events."""
    request_data = event.get("request")
    if not isinstance(request_data, dict):
        return event
    raw_url = str(request_data.get("url") or "")
    request_path = urlparse(raw_url).path if raw_url else ""
    if not is_candidate_assessment_path(request_path):
        return event

    request_data["url"] = redact_candidate_request_url(raw_url)
    request_data.pop("data", None)
    request_data.pop("cookies", None)
    sensitive_headers = {
        "x-assessment-token",
        "x-assessment-session",
        "x-assessment-key-id",
        "x-assessment-proof-timestamp",
        "x-assessment-proof-nonce",
        "x-assessment-proof",
        "cookie",
        "authorization",
    }
    raw_headers = request_data.get("headers")
    if isinstance(raw_headers, dict):
        request_data["headers"] = {
            key: value
            for key, value in raw_headers.items()
            if str(key).casefold() not in sensitive_headers
        }
    elif isinstance(raw_headers, list):
        request_data["headers"] = [
            pair
            for pair in raw_headers
            if not (
                isinstance(pair, (list, tuple))
                and pair
                and str(pair[0]).casefold() in sensitive_headers
            )
        ]
    transaction = event.get("transaction")
    if isinstance(transaction, str):
        event["transaction"] = redact_sensitive_request_path(transaction)

    breadcrumbs = event.get("breadcrumbs")
    values = breadcrumbs.get("values") if isinstance(breadcrumbs, dict) else None
    if isinstance(values, list):
        for crumb in values:
            if not isinstance(crumb, dict):
                continue
            data = crumb.get("data")
            if isinstance(data, dict):
                crumb_url = str(data.get("url") or "")
                crumb_path = urlparse(crumb_url).path if crumb_url else ""
                if is_candidate_assessment_path(crumb_path):
                    data["url"] = redact_candidate_request_url(crumb_url)
                    data.pop("query", None)
                    data.pop("body", None)
                    data.pop("headers", None)
            message = crumb.get("message")
            if isinstance(message, str) and "/api/v1/assessments/" in message:
                crumb["message"] = redact_sensitive_request_path(message.split("?", 1)[0])
    return event


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
    if "/api/v1/assessments/" in path and any(
        segment in path
        for segment in (
            "/start",
            "/execute",
            "/submit",
            "/claude",
            "/upload-cv",
            "/repo-file",
            "/runtime-event",
        )
    ):
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
                logger.warning(
                    "Rate limit exceeded key=%s path=%s",
                    key,
                    redact_sensitive_request_path(path),
                )
                response = JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Please try again later."},
                )
                if is_candidate_assessment_path(path):
                    apply_candidate_security_headers(response)
                return response
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
                redact_sensitive_request_path(request.url.path),
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
