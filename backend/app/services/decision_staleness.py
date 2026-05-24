"""A2: read-time decision-staleness detection.

Given an ``AgentDecision`` row whose A1 ``input_fingerprint`` was captured
at queue time, this service answers: "have any of the inputs the agent
cited shifted since then?". Used by the Decision Hub on every fetch so
recruiters see an "Inputs changed" badge instead of approving stale
decisions.

Resolved applications (per A6) are always treated as fresh — their
snapshot is frozen by design and must not be re-evaluated.

Pre-A1 decisions (empty fingerprint) are also always fresh — we have
no baseline to compare against, so we don't flag them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from ..domains.assessments_runtime.role_support import is_resolved
from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.role_criterion import RoleCriterion
from ..models.role_feedback_note import RoleFeedbackNote


# Score-shift threshold. Pre-screen and cv-match live on a 0-100 scale
# where the recruiter cutoff is 50; 5 points is one decision-meaningful
# tier (the gap between "borderline reject" and "borderline accept").
# Below the band we treat the score as unchanged so noise from
# re-scoring (different prompt seed etc.) doesn't churn the queue.
SCORE_DRIFT_BAND = 5.0


@dataclass(frozen=True)
class StalenessReport:
    is_stale: bool
    reasons: list[str] = field(default_factory=list)
    summary: Optional[str] = None
    details: dict = field(default_factory=dict)


@dataclass
class StalenessCache:
    """Per-request memo for batch ``evaluate`` calls.

    A list of pending decisions usually shares a handful of roles, so the
    per-role criteria fingerprint and latest-note lookups would otherwise
    re-run once per decision (N+1). Pass a single instance to every
    ``evaluate`` call in the batch to collapse those to one query per
    distinct role. Scope it to a single request — it holds no TTL.
    """

    criteria_fp: dict[int, str | None] = field(default_factory=dict)
    latest_note_id: dict[int, int | None] = field(default_factory=dict)


def _score_drift(old: Optional[float], new: Optional[float]) -> bool:
    """True if both sides have a value and they differ by >= the band.

    Asymmetric None handling is deliberate: a score going from None to a
    value (or vice versa) is a NEW input, not drift — the
    'cv_replaced' / 'assessment_score_shifted' reasons cover those.
    We only flag here when both old and new are concrete.
    """
    if old is None or new is None:
        return False
    try:
        return abs(float(new) - float(old)) >= SCORE_DRIFT_BAND
    except (TypeError, ValueError):
        return False


def criteria_content_fingerprint(
    db: Session, role_id: int, *, cache: "StalenessCache | None" = None
) -> str | None:
    """Content-only fingerprint of a role's active criteria.

    Hashes the SORTED ``text:bucket:weight:must_have`` of each criterion and
    deliberately EXCLUDES the volatile row ``id``. ``sync_derived_criteria``
    hard-deletes + re-inserts the derived criteria with fresh ids on every
    sync; an id-based hash therefore churned on each tick and spuriously marked
    every pending decision stale even when the job spec (and thus the criteria
    text) was unchanged. Content-only means re-deriving identical criteria is a
    no-op for staleness — only a real text/bucket/weight/must-have change flips
    it.

    This is the single source of truth shared by the queue-time capture
    (``queue_decision``) and the staleness recompute so the two can never
    diverge. Returns None when the role has no criteria.
    """
    if cache is not None and role_id in cache.criteria_fp:
        return cache.criteria_fp[role_id]
    rows = (
        db.query(RoleCriterion)
        .filter(
            RoleCriterion.role_id == role_id,
            RoleCriterion.deleted_at.is_(None),
        )
        .all()
    )
    if not rows:
        result: str | None = None
    else:
        parts = sorted(
            f"{(c.text or '').strip()}:{c.bucket or ''}:{c.weight or 0}:{int(bool(getattr(c, 'must_have', False)))}"
            for c in rows
        )
        result = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    if cache is not None:
        cache.criteria_fp[role_id] = result
    return result


# Back-compat alias: existing internal call sites used this name.
_recompute_criteria_fingerprint = criteria_content_fingerprint


def _latest_recruiter_note_id(
    db: Session, role_id: int, *, cache: "StalenessCache | None" = None
) -> int | None:
    if cache is not None and role_id in cache.latest_note_id:
        return cache.latest_note_id[role_id]
    row = (
        db.query(RoleFeedbackNote.id)
        .filter(RoleFeedbackNote.role_id == role_id)
        .order_by(RoleFeedbackNote.id.desc())
        .first()
    )
    result = int(row[0]) if row else None
    if cache is not None:
        cache.latest_note_id[role_id] = result
    return result


def evaluate(
    db: Session,
    decision: AgentDecision,
    *,
    application: CandidateApplication | None = None,
    role: Role | None = None,
    cache: "StalenessCache | None" = None,
) -> StalenessReport:
    """Return a StalenessReport for the given decision.

    ``application`` and ``role`` may be passed in to avoid a re-query
    when the caller has already loaded them (batch path on the Hub).
    ``cache`` (a StalenessCache) memoizes the per-role criteria/note
    lookups across a batch of calls so a list of decisions sharing roles
    doesn't re-query per row.

    Resolved decisions are never stale by design — they're the frozen
    audit record. Empty fingerprint = pre-A1 decision = also fresh
    (no baseline to compare).
    """
    # Resolved => frozen snapshot, never stale by definition.
    if application is None:
        application = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == int(decision.application_id))
            .one_or_none()
        )
    if application is None or is_resolved(application):
        return StalenessReport(is_stale=False)

    fingerprint = decision.input_fingerprint or {}
    if not fingerprint:
        return StalenessReport(is_stale=False)

    if role is None:
        role = db.query(Role).filter(Role.id == int(decision.role_id)).one_or_none()
    if role is None:
        return StalenessReport(is_stale=False)

    reasons: list[str] = []
    details: dict = {}

    # 1. Role criteria edited
    current_criteria_fp = _recompute_criteria_fingerprint(
        db, int(decision.role_id), cache=cache
    )
    if (
        decision.criteria_fingerprint
        and current_criteria_fp
        and current_criteria_fp != decision.criteria_fingerprint
    ):
        reasons.append("criteria_changed")
        details["criteria_changed"] = True

    # 2. CV re-uploaded (timestamp shifted forward) OR cv_fingerprint mismatch
    cv_uploaded_at_at_emit = fingerprint.get("cv_uploaded_at")
    if (
        application.cv_uploaded_at is not None
        and cv_uploaded_at_at_emit is not None
        and application.cv_uploaded_at.isoformat() != cv_uploaded_at_at_emit
    ):
        reasons.append("cv_replaced")
        details["cv_replaced"] = {
            "at_emit": cv_uploaded_at_at_emit,
            "current": application.cv_uploaded_at.isoformat(),
        }
    elif decision.cv_fingerprint and (application.cv_text or "").strip():
        current_cv_fp = hashlib.sha256(
            (application.cv_text or "").strip().encode("utf-8")
        ).hexdigest()
        if current_cv_fp != decision.cv_fingerprint:
            reasons.append("cv_replaced")
            details["cv_replaced"] = {"hash_mismatch": True}

    # 3. Pre-screen score shifted by >= 5pts
    if _score_drift(
        fingerprint.get("pre_screen_score_at_emit"),
        getattr(application, "pre_screen_score_100", None),
    ):
        reasons.append("pre_screen_score_shifted")
        details["pre_screen_score_shifted"] = {
            "at_emit": fingerprint.get("pre_screen_score_at_emit"),
            "current": float(application.pre_screen_score_100 or 0),
        }

    # 4. Assessment score shifted by >= 5pts
    if _score_drift(
        fingerprint.get("assessment_score_at_emit"),
        getattr(application, "assessment_score_cache_100", None),
    ):
        reasons.append("assessment_score_shifted")
        details["assessment_score_shifted"] = {
            "at_emit": fingerprint.get("assessment_score_at_emit"),
            "current": float(application.assessment_score_cache_100 or 0),
        }

    # 5. Pre-screen cutoff changed (currently a constant; future-proofs
    # for when the cutoff becomes role-configurable).
    cutoff_at_emit = fingerprint.get("pre_screen_cutoff_at_emit")
    current_cutoff = getattr(role, "pre_screen_cutoff_score_100", None)
    if (
        cutoff_at_emit is not None
        and current_cutoff is not None
        and float(cutoff_at_emit) != float(current_cutoff)
    ):
        reasons.append("cutoff_changed")
        details["cutoff_changed"] = {
            "at_emit": cutoff_at_emit,
            "current": float(current_cutoff),
        }

    # 6. Recruiter note added since emit
    last_note_at_emit = fingerprint.get("last_recruiter_note_id")
    current_last_note = _latest_recruiter_note_id(
        db, int(decision.role_id), cache=cache
    )
    if (
        current_last_note is not None
        and (last_note_at_emit is None or current_last_note > int(last_note_at_emit))
    ):
        reasons.append("recruiter_note_added")
        details["recruiter_note_added"] = {
            "at_emit": last_note_at_emit,
            "current": current_last_note,
        }

    is_stale = bool(reasons)
    summary = _summarize(reasons) if is_stale else None
    return StalenessReport(
        is_stale=is_stale,
        reasons=reasons,
        summary=summary,
        details=details,
    )


_REASON_LABELS = {
    "criteria_changed": "Role criteria edited",
    "cv_replaced": "Candidate uploaded a new CV",
    "pre_screen_score_shifted": "Pre-screen score changed",
    "assessment_score_shifted": "Assessment score changed",
    "cutoff_changed": "Pre-screen cutoff changed",
    "recruiter_note_added": "Recruiter note added",
}


def _summarize(reasons: list[str]) -> str:
    """One-line human summary for the Decision Hub badge."""
    if not reasons:
        return ""
    if len(reasons) == 1:
        return _REASON_LABELS.get(reasons[0], reasons[0])
    primary = _REASON_LABELS.get(reasons[0], reasons[0])
    return f"{primary} (+{len(reasons) - 1} more)"
