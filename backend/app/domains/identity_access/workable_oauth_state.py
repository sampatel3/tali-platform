"""Signed, one-use state receipts for the recruiter Workable OAuth flow."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timezone
from functools import lru_cache

from fastapi_users.jwt import decode_jwt, generate_jwt

from ...platform.config import settings

logger = logging.getLogger("taali.workable.oauth")

WORKABLE_OAUTH_STATE_AUDIENCE = "workable-oauth"
WORKABLE_OAUTH_STATE_LIFETIME_SECONDS = 10 * 60
_WORKABLE_OAUTH_STATE_PURPOSE = "workable-oauth-state-v1"
_WORKABLE_OAUTH_STATE_KEY_PREFIX = "workable:oauth-state:"
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_CONSUME_MATCHING_RECEIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class InvalidWorkableOAuthState(ValueError):
    """The state is malformed, mismatched, expired, consumed, or unknown."""


class WorkableOAuthStateStoreUnavailable(RuntimeError):
    """The shared one-use receipt store cannot safely serve the OAuth flow."""


@lru_cache(maxsize=1)
def _redis_client():
    redis_url = (getattr(settings, "REDIS_URL", "") or "").strip()
    if not redis_url:
        return None
    try:
        import redis  # type: ignore

        return redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
    except Exception:
        logger.warning("Unable to initialize the Workable OAuth state store")
        return None


def _receipt_key(*, user_id: int, organization_id: int) -> str:
    # Keep identifiers out of Redis diagnostics while bounding receipt
    # cardinality to one outstanding flow for each user and workspace.
    identity = f"{organization_id}:{user_id}".encode("ascii")
    return f"{_WORKABLE_OAUTH_STATE_KEY_PREFIX}{hashlib.sha256(identity).hexdigest()}"


def _receipt_value(nonce: str) -> str:
    # Redis never needs the bearer-like nonce itself. The callback proves
    # possession by presenting a signed state whose nonce hashes to this value.
    return hashlib.sha256(nonce.encode("ascii")).hexdigest()


def _store_unavailable() -> WorkableOAuthStateStoreUnavailable:
    return WorkableOAuthStateStoreUnavailable(
        "Workable connection is temporarily unavailable. Please try again."
    )


def mint_workable_oauth_state(*, user_id: int, organization_id: int) -> str:
    """Create a short-lived signed state and persist its one-use receipt.

    Redis is deliberately mandatory here: silently falling back to process
    memory would make a callback fail or become replayable whenever the request
    lands on another web replica.
    """

    client = _redis_client()
    if client is None:
        raise _store_unavailable()

    nonce = secrets.token_urlsafe(32)
    try:
        stored = client.set(
            _receipt_key(user_id=user_id, organization_id=organization_id),
            _receipt_value(nonce),
            ex=WORKABLE_OAUTH_STATE_LIFETIME_SECONDS,
        )
    except Exception as exc:
        logger.warning("Unable to persist a Workable OAuth state receipt")
        raise _store_unavailable() from exc
    if not stored:
        raise _store_unavailable()

    return generate_jwt(
        {
            "sub": str(user_id),
            "organization_id": str(organization_id),
            "aud": WORKABLE_OAUTH_STATE_AUDIENCE,
            "purpose": _WORKABLE_OAUTH_STATE_PURPOSE,
            "nonce": nonce,
            "iat": int(datetime.now(timezone.utc).timestamp()),
        },
        settings.SECRET_KEY,
        WORKABLE_OAUTH_STATE_LIFETIME_SECONDS,
    )


def consume_workable_oauth_state(
    state: str,
    *,
    user_id: int,
    organization_id: int,
) -> None:
    """Validate identity/workspace binding and atomically consume the receipt."""

    try:
        claims = decode_jwt(
            state,
            settings.SECRET_KEY,
            [WORKABLE_OAUTH_STATE_AUDIENCE],
        )
        nonce = claims.get("nonce")
        valid = (
            isinstance(nonce, str)
            and bool(_NONCE_RE.fullmatch(nonce))
            and claims.get("purpose") == _WORKABLE_OAUTH_STATE_PURPOSE
            and hmac.compare_digest(str(claims.get("sub") or ""), str(user_id))
            and hmac.compare_digest(
                str(claims.get("organization_id") or ""),
                str(organization_id),
            )
        )
    except Exception as exc:
        raise InvalidWorkableOAuthState from exc
    if not valid:
        raise InvalidWorkableOAuthState

    client = _redis_client()
    if client is None:
        raise _store_unavailable()
    try:
        consumed = int(
            client.eval(
                _CONSUME_MATCHING_RECEIPT,
                1,
                _receipt_key(
                    user_id=user_id,
                    organization_id=organization_id,
                ),
                _receipt_value(nonce),
            )
        )
    except Exception as exc:
        logger.warning("Unable to consume a Workable OAuth state receipt")
        raise _store_unavailable() from exc
    if consumed != 1:
        raise InvalidWorkableOAuthState
