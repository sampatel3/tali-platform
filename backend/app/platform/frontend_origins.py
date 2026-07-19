"""Canonical trusted frontend-origin and redirect validation helpers."""

from __future__ import annotations

import re
from urllib.parse import urlparse


def normalize_origin(origin: str | None) -> str | None:
    cleaned = (origin or "").strip().rstrip("/")
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return cleaned


def frontend_origins(frontend_url: str | None) -> list[str]:
    primary = normalize_origin(frontend_url)
    if not primary:
        return []

    origins = [primary]
    parsed = urlparse(primary)
    host = parsed.hostname or ""
    if host.startswith("www."):
        port = f":{parsed.port}" if parsed.port else ""
        origins.append(f"{parsed.scheme}://{host[4:]}{port}")
    return origins


def build_cors_origins(
    frontend_url: str | None,
    extra_origins: str | None,
) -> list[str]:
    origins = [
        *frontend_origins(frontend_url),
        "http://localhost:5173",
        "http://localhost:3000",
    ]
    if extra_origins:
        origins.extend(normalize_origin(origin) for origin in extra_origins.split(","))

    deduped = []
    seen = set()
    for origin in origins:
        if not origin or origin in seen:
            continue
        seen.add(origin)
        deduped.append(origin)
    return deduped


def trusted_frontend_redirect_url(
    value: str,
    *,
    frontend_url: str | None,
    extra_origins: str | None,
    origin_regex: str | None,
) -> str:
    """Return an absolute redirect URL only when its origin is frontend-trusted.

    Stripe follows these URLs after a hosted payment/account flow. Accepting an
    arbitrary caller-provided origin would turn an authenticated Taali endpoint
    into a convincing post-payment phishing redirect.
    """

    candidate = (value or "").strip()
    if not candidate or any(ord(char) < 32 for char in candidate):
        raise ValueError("redirect_url_invalid")
    parsed = urlparse(candidate)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "\\" in candidate
    ):
        raise ValueError("redirect_url_invalid")
    try:
        origin = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port is not None:
            origin = f"{origin}:{parsed.port}"
    except ValueError as exc:
        raise ValueError("redirect_url_invalid") from exc

    trusted = set(build_cors_origins(frontend_url, extra_origins))
    if origin in trusted:
        return candidate

    regex = (origin_regex or "").strip()
    if not regex and "vercel.app" in (frontend_url or ""):
        regex = r"https://.*\.vercel\.app"
    try:
        if regex and re.fullmatch(regex, origin):
            return candidate
    except re.error:
        # Startup validation owns malformed CORS configuration. Redirects stay
        # fail-closed even if this helper is called before startup completes.
        pass
    raise ValueError("redirect_origin_not_allowed")


# Preserve the private names imported by existing CORS tests/callers while the
# implementation lives in a small platform module shared with billing.
_normalize_origin = normalize_origin
_frontend_origins = frontend_origins
_build_cors_origins = build_cors_origins


__all__ = [
    "_build_cors_origins",
    "_frontend_origins",
    "_normalize_origin",
    "build_cors_origins",
    "frontend_origins",
    "normalize_origin",
    "trusted_frontend_redirect_url",
]
