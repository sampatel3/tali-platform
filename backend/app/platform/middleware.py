import hashlib
import logging
import ipaddress
import time
import uuid
from urllib.parse import parse_qs
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from .logging import safe_http_route
from .request_context import normalize_request_id, set_client_meta, set_request_id
from .config import settings
from ..domains.identity_access.access_policy import SAML_SSO_AVAILABLE, evaluate_login_access
from ..models.api_key import KEY_PREFIX_LIVE, KEY_PREFIX_TEST
from ..services.rate_limit import check_rate_limit

logger = logging.getLogger("tali.middleware")

_RATE_WINDOW_SEC = 60
_LEGACY_FIREFLIES_PATH = "/api/v1/webhooks/fireflies"


_API_KEY_PREFIXES = (KEY_PREFIX_LIVE, KEY_PREFIX_TEST)
# Bucket a tali_* key on a stable slice of the token (never the whole secret,
# which we do NOT verify here). Mirrors the displayed key prefix length in
# api_key_service (prefix label + 6 chars) so a bucket maps to one key.
_MCP_KEY_BUCKET_LEN = len(KEY_PREFIX_LIVE) + 6


def resolve_client_ip(request: Request) -> str:
    """Resolve one spoof-resistant client address for every limiter/log sink.

    Railway's canonical ``X-Real-IP`` is trusted only when the deployment
    explicitly opts in. Generic forwarded chains are accepted only when the
    socket peer belongs to a configured trusted proxy network.
    """
    peer = request.client.host if request.client else "unknown"
    if bool(getattr(settings, "TRUST_RAILWAY_X_REAL_IP", False)):
        railway_real_ip = (request.headers.get("x-real-ip") or "").strip()
        try:
            return str(ipaddress.ip_address(railway_real_ip))
        except ValueError:
            # Missing/malformed edge metadata must never become a caller-
            # controlled bucket key. Fall back to the socket peer (and then to
            # an explicitly trusted generic proxy chain below).
            if railway_real_ip:
                logger.warning("Ignoring invalid Railway X-Real-IP header")

    configured = str(getattr(settings, "TRUSTED_PROXY_CIDRS", "") or "")
    networks = []
    for raw in configured.split(","):
        value = raw.strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            logger.error("Ignoring invalid TRUSTED_PROXY_CIDRS entry: %s", value)
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if not networks or not any(peer_ip in network for network in networks):
        return peer

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer
    # Walk from the trusted peer towards the client and return the first
    # untrusted address. Never trust the attacker-controlled leftmost value
    # merely because a proxy header exists.
    for raw in reversed(forwarded.split(",")):
        try:
            candidate = ipaddress.ip_address(raw.strip())
        except ValueError:
            continue
        if any(candidate in network for network in networks):
            continue
        return str(candidate)
    return peer


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


def _legacy_fireflies_buckets(ip: str, path: str) -> list[tuple[str, int]]:
    """Bound only the ambiguous compatibility route; scoped URLs stay O(1)."""
    if path.rstrip("/") != _LEGACY_FIREFLIES_PATH:
        return []
    return [
        (
            f"fireflies_legacy:ip:{ip}",
            settings.FIREFLIES_LEGACY_RATE_LIMIT_PER_MINUTE,
        ),
    ]


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


def _rate_limit_category(key: str) -> str:
    if key.startswith("mcp:key:"):
        return "mcp_key"
    if key.startswith("mcp:ip:"):
        return "mcp_ip"
    if key.startswith("candidate_token:"):
        return "candidate_token"
    if key.startswith("assessment:"):
        return "assessment"
    if key.startswith("auth:"):
        return "auth"
    if key.startswith("fireflies_legacy:"):
        return "fireflies_legacy"
    return "other"


def _opaque_rate_limit_bucket(key: str) -> str:
    return f"bucket-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce the configured abuse budgets for public request surfaces."""

    async def dispatch(self, request: Request, call_next):
        ip = resolve_client_ip(request)
        path = request.url.path
        if path == "/mcp" or path.startswith("/mcp/"):
            # Public MCP mount (JWT or tali_* API key). Multi-bucket: per
            # unverified-key-prefix scoped by IP, plus a per-IP guard.
            buckets = _mcp_buckets(request, ip)
        elif legacy_fireflies_buckets := _legacy_fireflies_buckets(ip, path):
            buckets = legacy_fireflies_buckets
        else:
            key = _rate_limit_key(ip, path)
            buckets = [(key, _rate_limit_max(key))] if key else []

        for key, max_allowed in buckets:
            if max_allowed <= 0:
                # Disabled bucket (e.g. MCP_RATE_LIMIT_PER_MINUTE=0).
                continue
            if not check_rate_limit(
                key,
                limit=max_allowed,
                window_seconds=_RATE_WINDOW_SEC,
            ):
                logger.warning(
                    "rate_limit_exceeded category=%s bucket=%s",
                    _rate_limit_category(key),
                    _opaque_rate_limit_bucket(key),
                )
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Please try again later."},
                    headers={"Retry-After": str(_RATE_WINDOW_SEC)},
                )
        return await call_next(request)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        supplied_request_id = request.headers.get("X-Request-ID")
        request_id = normalize_request_id(supplied_request_id) or str(uuid.uuid4())
        request.state.request_id = request_id
        set_request_id(request_id)
        set_client_meta(resolve_client_ip(request), request.headers.get("user-agent"))
        response = await call_next(request)

        duration_ms = (time.time() - start_time) * 1000

        if request.url.path != "/health":
            logger.info(
                "method=%s route=%s status=%d duration=%.1fms",
                request.method,
                safe_http_route(request),
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
        # The former implementation treated a metadata XML URL as a login URL
        # even though no SAML assertion consumer existed. Until the complete
        # protocol is implemented, stale database flags must not lock users out.
        if not SAML_SSO_AVAILABLE:
            return await call_next(request)
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
