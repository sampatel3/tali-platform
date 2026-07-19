"""Capability-flag client — Section 3 of capability_flags_addendum.md.

This is the ONLY module that reads ``capability_flags``. Every agent,
the orchestrator, the policy engine, and any v10 capability stub
imports it and calls ``is_active(...)`` or ``snapshot(...)``.

Adapted to our stack:
- Sync (matches FastAPI + SQLAlchemy throughout this codebase).
- Reads via SQLAlchemy Session, not raw asyncpg.
- Cache is process-local, refreshed every ``refresh_seconds`` (default 30).
  The addendum's "read-through, not read-once" property is preserved —
  flag changes propagate within the refresh window without restarts.

Scope evaluation order matches the addendum exactly:
  1. enabled check
  2. time window (starts_at / ends_at)
  3. org filter (org_ids on scope — distinct from the row's
     organization_id column which is the *lookup key*)
  4. role / role_family filter
  5. cohort_tags filter
  6. deterministic percentage rollout (sha256(capability:decision_id))
  7. dependency check (recursive — every required capability must
     itself be active in the same context)
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from ..models.capability_flag import CapabilityFlag
from .registry import CAPABILITIES


logger = logging.getLogger("taali.capabilities.flags")


DEFAULT_REFRESH_SECONDS = 30


# ---------------------------------------------------------------------------
# In-memory scope shape (parallel to the JSON written to scope_json)
# ---------------------------------------------------------------------------


@dataclass
class FlagScope:
    """Mirror of the JSON blob in ``capability_flags.scope_json``.

    All fields are ``None`` by default → "no restriction on this axis".
    """

    org_ids: list[int] | None = None
    role_ids: list[int] | None = None
    role_families: list[str] | None = None
    percentage: float = 100.0
    cohort_tags: list[str] | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    @classmethod
    def from_dict(cls, raw: dict | None) -> "FlagScope":
        raw = raw or {}
        return cls(
            org_ids=raw.get("org_ids"),
            role_ids=raw.get("role_ids"),
            role_families=raw.get("role_families"),
            percentage=float(raw.get("percentage", 100.0)),
            cohort_tags=raw.get("cohort_tags"),
            starts_at=_parse_dt(raw.get("starts_at")),
            ends_at=_parse_dt(raw.get("ends_at")),
        )


def _parse_dt(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


@dataclass
class _FlagRow:
    """Decoded ``capability_flags`` row — what the cache holds."""

    capability: str
    organization_id: int | None
    enabled: bool
    scope: FlagScope
    requires: tuple[str, ...]


def _decode(row: CapabilityFlag) -> _FlagRow:
    return _FlagRow(
        capability=str(row.capability),
        organization_id=int(row.organization_id) if row.organization_id is not None else None,
        enabled=bool(row.enabled),
        scope=FlagScope.from_dict(row.scope_json or {}),
        requires=tuple(row.requires_json or []),
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class CapabilityFlags:
    """Process-local, periodically refreshed flag client.

    Construct one per process (or per request — refreshes are cheap and
    the cache is shared via the underlying Session pool reading the
    same Postgres rows). Tests construct a fresh one per test.
    """

    def __init__(
        self,
        refresh_seconds: int = DEFAULT_REFRESH_SECONDS,
        *,
        respect_availability: bool = True,
    ):
        self._refresh_seconds = max(1, int(refresh_seconds))
        # Production must never activate a registry entry whose implementation
        # is still a scaffold.  The explicit override exists only so the flag
        # substrate tests can exercise rollout/dependency semantics against the
        # canonical (currently unavailable) capability names.
        self._respect_availability = bool(respect_availability)
        self._cache: dict[tuple[str, int | None], _FlagRow] = {}
        self._last_refresh: datetime | None = None
        self._lock = threading.Lock()

    # ----- internal -----

    def _maybe_refresh(self, db: Session) -> None:
        now = datetime.now(timezone.utc)
        with self._lock:
            if (
                self._last_refresh is None
                or (now - self._last_refresh).total_seconds() > self._refresh_seconds
            ):
                self._refresh_locked(db)

    def _refresh_locked(self, db: Session) -> None:
        rows = db.query(CapabilityFlag).all()
        self._cache = {(r.capability, _key_org(r.organization_id)): _decode(r) for r in rows}
        self._last_refresh = datetime.now(timezone.utc)

    def invalidate(self) -> None:
        """Force the next ``is_active`` call to re-read from Postgres."""
        with self._lock:
            self._last_refresh = None

    # ----- public -----

    def is_active(
        self,
        capability: str,
        *,
        db: Session,
        organization_id: int,
        decision_id: str,
        role_id: int | None = None,
        role_family: str | None = None,
        cohort_tags: Iterable[str] | None = None,
        now: datetime | None = None,
        _visited: set[str] | None = None,
    ) -> bool:
        """Is the named capability active for this specific decision context?

        Returns False on any unknown capability — invariant: callers
        always go through this function, never check the cache directly.
        """
        definition = CAPABILITIES.get(capability)
        if definition is None or (
            self._respect_availability and not definition.available
        ):
            return False
        self._maybe_refresh(db)
        # Prefer org-scoped row, else global default.
        flag = self._cache.get((capability, organization_id)) or self._cache.get(
            (capability, None)
        )
        if flag is None or not flag.enabled:
            return False

        now = now or datetime.now(timezone.utc)
        if flag.scope.starts_at and now < flag.scope.starts_at:
            return False
        if flag.scope.ends_at and now > flag.scope.ends_at:
            return False
        if flag.scope.org_ids and organization_id not in flag.scope.org_ids:
            return False
        if flag.scope.role_ids and (role_id is None or role_id not in flag.scope.role_ids):
            return False
        if flag.scope.role_families and (
            role_family is None or role_family not in flag.scope.role_families
        ):
            return False
        if flag.scope.cohort_tags:
            tags = set(cohort_tags or [])
            if not (tags & set(flag.scope.cohort_tags)):
                return False

        if flag.scope.percentage < 100.0:
            bucket = int(
                hashlib.sha256(f"{capability}:{decision_id}".encode()).hexdigest(), 16
            ) % 10000
            if bucket >= flag.scope.percentage * 100:
                return False

        # Dependency check — recursive, with cycle protection.
        visited = set(_visited or ())
        visited.add(capability)
        for dep in flag.requires:
            if dep in visited:
                logger.warning("capability dependency cycle at %s -> %s", capability, dep)
                return False
            if not self.is_active(
                dep,
                db=db,
                organization_id=organization_id,
                decision_id=decision_id,
                role_id=role_id,
                role_family=role_family,
                cohort_tags=cohort_tags,
                now=now,
                _visited=visited,
            ):
                return False

        return True

    def snapshot(
        self,
        capabilities: Iterable[str],
        *,
        db: Session,
        organization_id: int,
        decision_id: str,
        role_id: int | None = None,
        role_family: str | None = None,
        cohort_tags: Iterable[str] | None = None,
        now: datetime | None = None,
    ) -> dict[str, bool]:
        """Build the per-decision audit snapshot.

        Called once at the top of a decision; the dict is persisted on
        ``agent_decisions.active_capabilities``.
        """
        return {
            cap: self.is_active(
                cap,
                db=db,
                organization_id=organization_id,
                decision_id=decision_id,
                role_id=role_id,
                role_family=role_family,
                cohort_tags=cohort_tags,
                now=now,
            )
            for cap in capabilities
        }


def _key_org(value) -> int | None:
    if value is None:
        return None
    return int(value)


# ---------------------------------------------------------------------------
# Module-level shared instance
# ---------------------------------------------------------------------------


_SHARED: CapabilityFlags | None = None
_SHARED_LOCK = threading.Lock()


def get_shared() -> CapabilityFlags:
    """Return the process-wide ``CapabilityFlags`` client (initialised lazily).

    Tests should NOT use this — construct a fresh ``CapabilityFlags()`` so
    no state leaks between tests. Production code uses the shared client.
    """
    global _SHARED
    if _SHARED is None:
        with _SHARED_LOCK:
            if _SHARED is None:
                _SHARED = CapabilityFlags()
    return _SHARED


__all__ = [
    "CapabilityFlags",
    "DEFAULT_REFRESH_SECONDS",
    "FlagScope",
    "get_shared",
]
