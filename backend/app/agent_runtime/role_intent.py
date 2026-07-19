"""``role_intent`` — fetch + author + drift detection.

Amendment A1 surface. Functions here are pure-Python over SQLAlchemy
Sessions — no Cypher, no Graphiti calls. A new version writes a durable
``graph_episode_outbox`` row in the caller's transaction; the background
drain mirrors it to Graphiti after commit. Graph failures never roll back
the canonical Postgres write.

Public API:
- ``fetch_active_intent(db, role_id, t)`` — the row that was active at
  time ``t``. Used by all four sub-agents at score time.
- ``author_new_version(db, ...)`` — write a new version, supersede the
  prior, stamp the prior's ``valid_to``.
- ``drift_detect(db, role_id, since=..)`` — compare stated intent
  against override reasons (A1.7.2). Returns the novel dimensions
  observed in overrides but not in intent.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from ..models.decision_feedback import DecisionFeedback
from ..models.role_intent import RoleIntent
from .contracts import RoleIntentRecord, StructuredIntent


logger = logging.getLogger("taali.agent_runtime.role_intent")


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def fetch_active_intent(
    db: Session, *, role_id: int, t: datetime | None = None
) -> RoleIntentRecord | None:
    """Return the RoleIntent row active for ``role_id`` at time ``t``.

    ``t`` defaults to now. The "active" row is the one with
    ``valid_from <= t`` and ``(valid_to IS NULL OR valid_to > t)``.

    Returns None when the role has never had an intent authored — that
    case is normal during rollout and the calling sub-agents must
    handle it (treat as "no intent overlay").
    """
    target = t or datetime.now(timezone.utc)
    row = (
        db.query(RoleIntent)
        .filter(
            RoleIntent.role_id == role_id,
            RoleIntent.valid_from <= target,
        )
        .filter(
            (RoleIntent.valid_to.is_(None)) | (RoleIntent.valid_to > target)
        )
        .order_by(RoleIntent.version.desc())
        .first()
    )
    if row is None:
        return None
    try:
        structured = StructuredIntent.model_validate(row.structured_fields or {})
    except Exception:
        # Defensive: a malformed older row shouldn't crash the cycle.
        structured = StructuredIntent()
    return RoleIntentRecord(
        intent_id=int(row.id),
        role_id=int(row.role_id),
        version=int(row.version),
        structured=structured,
        free_text=row.free_text,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        authored_by_user_id=(
            int(row.authored_by_user_id) if row.authored_by_user_id else None
        ),
        authored_at=row.authored_at,
    )


# ---------------------------------------------------------------------------
# Author
# ---------------------------------------------------------------------------


def author_new_version(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    structured: StructuredIntent,
    free_text: str | None = None,
    authored_by_user_id: int | None = None,
    now: datetime | None = None,
) -> RoleIntent:
    """Write a new ``RoleIntent`` version atomically.

    Steps:
      1. Stamp the existing active row's ``valid_to`` (if any).
      2. Insert the new row with ``version = prior.version + 1``,
         ``valid_from = now``, ``superseded_id = prior.id``.
      3. db.flush() so the caller sees the new id.
      4. Best-effort enqueue the optional graph mirror in an isolated savepoint.

    Caller commits. Canonical RoleIntent failures bubble up; graph outbox failures
    are logged without rolling back the successfully-flushed intent.
    """
    now = now or datetime.now(timezone.utc)
    prior = (
        db.query(RoleIntent)
        .filter(RoleIntent.role_id == role_id, RoleIntent.valid_to.is_(None))
        .order_by(RoleIntent.version.desc())
        .first()
    )
    if prior is not None:
        prior.valid_to = now
    new_version = (prior.version + 1) if prior else 1
    row = RoleIntent(
        organization_id=organization_id,
        role_id=role_id,
        version=new_version,
        structured_fields=structured.model_dump(),
        free_text=free_text,
        superseded_id=int(prior.id) if prior else None,
        valid_from=now,
        valid_to=None,
        authored_by_user_id=authored_by_user_id,
        authored_at=now,
    )
    db.add(row)
    db.flush()

    _enqueue_role_intent_episode(db, row=row)
    return row


def _enqueue_role_intent_episode(db: Session, *, row: RoleIntent) -> None:
    """Best-effort enqueue; successfully inserted rows remain durably retryable."""
    try:
        # The graph mirror is optional. Isolate its query/insert so a malformed
        # legacy role or an outbox constraint failure cannot poison the caller's
        # canonical RoleIntent transaction.
        with db.begin_nested():
            payload = _role_intent_episode_payload(db, row=row)
            if payload is None:
                return
            from ..candidate_graph import episode_outbox

            episode_outbox.enqueue_role_intent(db, **payload)
    except Exception as exc:
        logger.warning(
            "role intent outbox enqueue failed for role_id=%s v%s error_type=%s",
            getattr(row, "role_id", None),
            getattr(row, "version", None),
            type(exc).__name__,
        )


def _role_intent_episode_payload(
    db: Session, *, row: RoleIntent
) -> dict | None:
    """Build the durable graph outbox payload for a new RoleIntent.

    Returns None (and logs) if anything goes wrong, so the caller's
    commit is never blocked.
    """
    try:
        from ..models.role import Role

        structured = StructuredIntent.model_validate(row.structured_fields or {})
        summary_parts: list[str] = []
        if structured.soft_signals:
            summary_parts.append(f"Soft signals: {', '.join(structured.soft_signals)}")
        if structured.deal_breakers:
            summary_parts.append(f"Deal-breakers: {', '.join(structured.deal_breakers)}")
        if structured.growth_expectations:
            summary_parts.append(f"Growth: {structured.growth_expectations}")
        if structured.context_for_opening:
            summary_parts.append(f"Context: {structured.context_for_opening}")
        if structured.weighting_notes:
            summary_parts.append(f"Weighting: {structured.weighting_notes}")
        if structured.must_haves_missing_from_spec:
            summary_parts.append(
                f"Must-haves missing from spec: "
                f"{', '.join(structured.must_haves_missing_from_spec)}"
            )
        role = db.query(Role).filter(Role.id == int(row.role_id)).one_or_none()
        return {
            "organization_id": int(row.organization_id),
            "role_id": int(row.role_id),
            "role_name": str(role.name) if role else None,
            "intent_version": int(row.version),
            "structured_summary": " · ".join(summary_parts) or "(no structured fields)",
            "free_text": row.free_text,
            "authored_by_user_id": (
                int(row.authored_by_user_id) if row.authored_by_user_id else None
            ),
            "authored_at": row.authored_at,
        }
    except Exception:
        logger.warning(
            "role intent episode payload build failed for role_id=%s v%s",
            getattr(row, "role_id", None),
            getattr(row, "version", None),
        )
        return None


# ---------------------------------------------------------------------------
# Drift detection (A1.7.2)
# ---------------------------------------------------------------------------


@dataclass
class DriftReport:
    role_id: int
    stated_dimensions: set[str]
    observed_dimensions: set[str]
    novel_dimensions: set[str]
    sample_size: int
    drift_threshold: int

    def is_drifting(self) -> bool:
        return len(self.novel_dimensions) >= self.drift_threshold


_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "of", "to", "for", "with", "without",
    "in", "on", "this", "that", "is", "was", "are", "were", "be", "by", "as",
    "we", "they", "i", "you", "it", "had", "have", "has", "did", "do", "does",
    "not", "no", "from", "at", "would", "should", "could", "must",
})


def _extract_dimensions(text: str) -> set[str]:
    """Heuristic dimension extractor.

    Returns lowercase noun-phrase-ish bigrams as the rough dimension set.
    Pre-pilot we don't yet have a structured "override taxonomy"; the
    nightly job uses this approximation and gets replaced when the
    capability_auditor lands (A1.7.2 explicitly calls this the v1 form).
    """
    if not text:
        return set()
    cleaned = re.sub(r"[^a-zA-Z\s]", " ", text.lower())
    tokens = [t for t in cleaned.split() if t and t not in _STOPWORDS and len(t) > 2]
    bigrams = {
        f"{a} {b}" for a, b in zip(tokens, tokens[1:])
        if a not in _STOPWORDS and b not in _STOPWORDS
    }
    # Single tokens too, so "resilience" alone counts as a dimension.
    singletons = {t for t in tokens if len(t) > 4}
    return bigrams | singletons


def _intent_dimensions(intent: RoleIntentRecord | None) -> set[str]:
    if intent is None:
        return set()
    parts: list[str] = []
    parts.extend(intent.structured.soft_signals)
    parts.extend(intent.structured.deal_breakers)
    parts.extend(intent.structured.must_haves_missing_from_spec)
    if intent.structured.weighting_notes:
        parts.append(intent.structured.weighting_notes)
    if intent.structured.context_for_opening:
        parts.append(intent.structured.context_for_opening)
    out: set[str] = set()
    for p in parts:
        out |= _extract_dimensions(p)
    return out


def drift_detect(
    db: Session,
    *,
    role_id: int,
    since: datetime | None = None,
    drift_threshold: int = 3,
) -> DriftReport:
    """Compare stated intent against override reasons in the window.

    Returns the dimensions observed in override reasons that aren't in
    the active intent. Caller decides whether to surface them as
    drift-review prompts on the recruiter dashboard.
    """
    intent = fetch_active_intent(db, role_id=role_id)
    since_dt = since or datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # Override / teach feedback rows for this role.
    fb_rows = (
        db.query(DecisionFeedback)
        .filter(
            DecisionFeedback.role_id == role_id,
            DecisionFeedback.created_at >= since_dt,
        )
        .all()
    )
    observed: set[str] = set()
    for fb in fb_rows:
        observed |= _extract_dimensions(fb.correction_text or "")

    stated = _intent_dimensions(intent)
    novel = observed - stated

    return DriftReport(
        role_id=role_id,
        stated_dimensions=stated,
        observed_dimensions=observed,
        novel_dimensions=novel,
        sample_size=len(fb_rows),
        drift_threshold=int(drift_threshold),
    )


__all__ = [
    "DriftReport",
    "author_new_version",
    "drift_detect",
    "fetch_active_intent",
]
