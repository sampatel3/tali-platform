"""Mint + verify API keys.

The token is a single opaque string ``{prefix}{random}``; we persist only its
SHA-256 hash. Verification hashes the presented token and looks the row up by
hash (uniformly distributed → effectively constant-time at the app layer),
then rejects revoked/expired keys. ``last_used_at`` is updated lazily (at most
once a minute) to avoid a write on every request.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from ..models.api_key import (
    API_KEY_SCOPES,
    DEFAULT_API_KEY_SCOPES,
    KEY_PREFIX_LIVE,
    KEY_PREFIX_TEST,
    ApiKey,
)

# Bytes of entropy in the random secret segment.
_SECRET_BYTES = 32
# Don't write last_used_at more than once per this window.
_LAST_USED_DEBOUNCE = timedelta(seconds=60)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: Optional[datetime]) -> Optional[datetime]:
    """Round-trip naive datetimes (SQLite default) back to UTC-aware so
    comparisons don't raise. No-op on Postgres (already aware)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_scopes(scopes: Optional[Iterable[str]]) -> list[str]:
    if scopes is None:
        return list(DEFAULT_API_KEY_SCOPES)
    cleaned: list[str] = []
    for s in scopes:
        if s not in API_KEY_SCOPES:
            raise ValueError(f"Unknown API key scope: {s}")
        if s not in cleaned:
            cleaned.append(s)
    return cleaned


@dataclass
class MintedKey:
    """The persisted row + the one-time plaintext secret."""

    api_key: ApiKey
    secret: str  # full token — return to caller ONCE, never stored


def mint_api_key(
    db: Session,
    *,
    organization_id: int,
    name: str,
    scopes: Optional[Iterable[str]] = None,
    is_test: bool = False,
    expires_at: Optional[datetime] = None,
    created_by_user_id: Optional[int] = None,
) -> MintedKey:
    prefix_label = KEY_PREFIX_TEST if is_test else KEY_PREFIX_LIVE
    random_part = secrets.token_urlsafe(_SECRET_BYTES)
    token = f"{prefix_label}{random_part}"
    display_prefix = token[: len(prefix_label) + 6]

    key = ApiKey(
        organization_id=organization_id,
        created_by_user_id=created_by_user_id,
        name=name,
        prefix=display_prefix,
        is_test=is_test,
        hashed_secret=hash_token(token),
        scopes=normalize_scopes(scopes),
        expires_at=expires_at,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return MintedKey(api_key=key, secret=token)


def verify_api_key(db: Session, token: str) -> Optional[ApiKey]:
    """Resolve a presented token to a live ApiKey, or None.

    None covers: unknown hash, revoked, or expired. Updates ``last_used_at``
    lazily (debounced) on success.
    """
    if not token or not token.startswith((KEY_PREFIX_LIVE, KEY_PREFIX_TEST)):
        return None
    key = (
        db.query(ApiKey)
        .filter(ApiKey.hashed_secret == hash_token(token))
        .first()
    )
    if key is None:
        return None
    if key.revoked_at is not None:
        return None
    now = _utcnow()
    expires_at = _as_aware(key.expires_at)
    if expires_at is not None and expires_at <= now:
        return None
    last_used = _as_aware(key.last_used_at)
    if last_used is None or (now - last_used) > _LAST_USED_DEBOUNCE:
        key.last_used_at = now
        db.commit()
    return key
