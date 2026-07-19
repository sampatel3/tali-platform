import hashlib
import re
from contextvars import ContextVar
from typing import Optional


_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_SENSITIVE_REQUEST_ID_PREFIXES = (
    "api-key",
    "api_key",
    "bearer-",
    "bearer_",
    "gho_",
    "ghp_",
    "ghr_",
    "ghs_",
    "ghu_",
    "github_pat_",
    "secret-",
    "secret_",
    "sk-",
    "sk_",
    "tali_live_",
    "tali_test_",
    "whsec_",
)
_LONG_SENSITIVE_REQUEST_ID_RE = re.compile(
    r"(?:(?:api|e2b|eeo|re|rk|rpt|shr|sub)_[A-Za-z0-9_-]{16,}"
    r"|pa-[A-Za-z0-9_-]{16,})",
    re.IGNORECASE,
)
_AWS_ACCESS_KEY_ID_RE = re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}")
_OPAQUE_REQUEST_ID_DIGEST_LENGTH = 24

_request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def normalize_request_id(request_id: object) -> Optional[str]:
    """Keep safe correlation IDs and deterministically obscure unsafe ones."""

    if request_id is None:
        return None
    if isinstance(request_id, str):
        normalized = request_id.strip()
        lowered = normalized.lower()
        credential_shaped = (
            lowered.startswith(_SENSITIVE_REQUEST_ID_PREFIXES)
            or _LONG_SENSITIVE_REQUEST_ID_RE.fullmatch(normalized) is not None
            or _AWS_ACCESS_KEY_ID_RE.fullmatch(normalized) is not None
        )
        if _REQUEST_ID_RE.fullmatch(normalized) and not credential_shaped:
            return normalized
        raw = normalized.encode("utf-8", errors="replace")
    else:
        raw = type(request_id).__name__.encode("ascii", errors="replace")
    digest = hashlib.sha256(raw).hexdigest()[:_OPAQUE_REQUEST_ID_DIGEST_LENGTH]
    return f"opaque-{digest}"


def set_request_id(request_id: object):
    return _request_id_ctx.set(normalize_request_id(request_id))


def get_request_id() -> Optional[str]:
    return _request_id_ctx.get()


# Client IP + user agent for the current request, set by RequestLoggingMiddleware.
# Lets deep call sites (e.g. the auth audit trail inside UserManager.authenticate,
# which never sees the Request object) attribute events without plumbing the
# request through every layer.
_client_meta_ctx: ContextVar[Optional[dict]] = ContextVar("client_meta", default=None)


def set_client_meta(ip: Optional[str], user_agent: Optional[str]):
    return _client_meta_ctx.set({"ip": ip, "user_agent": user_agent})


def get_client_meta() -> dict:
    return _client_meta_ctx.get() or {}
