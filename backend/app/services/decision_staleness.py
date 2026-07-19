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

from ..cv_matching.holistic import HOLISTIC_ENGINE_VERSION, resolve_engine_version
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
    # Related-role decisions resolve their send/reject boundary independently
    # from the owner application. Keep that potentially DB-backed lookup at
    # O(distinct roles) on a Hub page, just like criteria and recruiter notes.
    role_fit_threshold: dict[int, float | None] = field(default_factory=dict)


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


def rebaseline_pending_criteria_fingerprint(db: Session, *, role_id: int) -> int:
    """Re-point every pending decision's criteria fingerprint at the role's
    CURRENT content fingerprint, without re-running the agent.

    Used when criteria change in a way that must NOT invalidate in-flight
    decisions — e.g. an immaterial Workable spec edit, or the one-time
    backfill after the id->content fingerprint migration. Touches ONLY the
    criteria dimension; cv/score/note/cutoff drift on a decision stays as
    captured, so a decision with a genuine other-input change remains stale.

    Returns the number of decisions updated.
    """
    from ..models.agent_decision import AgentDecision

    new_fp = criteria_content_fingerprint(db, int(role_id))
    pending = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == int(role_id),
            AgentDecision.status == "pending",
        )
        .all()
    )
    updated = 0
    for decision in pending:
        fp = decision.input_fingerprint or {}
        if not fp:
            continue  # pre-A1: no baseline, leave alone
        if (
            decision.criteria_fingerprint == new_fp
            and fp.get("criteria_fingerprint") == new_fp
        ):
            continue
        decision.criteria_fingerprint = new_fp
        decision.input_fingerprint = {**fp, "criteria_fingerprint": new_fp}
        db.add(decision)
        updated += 1
    return updated


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


def _engine_outdated(application: CandidateApplication) -> bool:
    """True when the application's stored score is from a superseded engine AND
    re-scoring would actually upgrade it (the org is on the current holistic
    engine). Lazy import keeps the heavy scoring stack off this module's import
    path and avoids a cycle; any error fails safe to 'not stale' so a staleness
    read never crashes the Hub."""
    try:
        from .cv_score_orchestrator import score_is_outdated

        return bool(score_is_outdated(application))
    except Exception:  # noqa: BLE001 — fail safe: don't crash the read
        return False


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

    reasons: list[str] = []
    details: dict = {}

    # Engine-version staleness — independent of the queue-time fingerprint.
    # A superseded-engine score is stale regardless of when the decision
    # queued, and we know it from the stored blob alone, so it flags even
    # pre-A1 (fingerprint-less) rows. Held aside and appended LAST so genuine
    # input drift leads the one-line summary when both are present.
    engine_stale = _engine_outdated(application)
    engine_details = (
        {
            "engine_outdated": {
                "engine_version": resolve_engine_version(
                    application.cv_match_details
                    if isinstance(application.cv_match_details, dict)
                    else {}
                ),
                "current": HOLISTIC_ENGINE_VERSION,
            }
        }
        if engine_stale
        else {}
    )

    def _finish() -> StalenessReport:
        all_reasons = reasons + (["engine_outdated"] if engine_stale else [])
        all_details = {**details, **engine_details}
        is_stale = bool(all_reasons)
        return StalenessReport(
            is_stale=is_stale,
            reasons=all_reasons,
            summary=_summarize(all_reasons) if is_stale else None,
            details=all_details,
        )

    fingerprint = decision.input_fingerprint or {}
    if not fingerprint:
        return _finish()

    if role is None:
        role = db.query(Role).filter(Role.id == int(decision.role_id)).one_or_none()
    if role is None:
        return _finish()

    # 1. Role criteria edited
    current_criteria_fp = _recompute_criteria_fingerprint(
        db, int(decision.role_id), cache=cache
    )
    if (decision.criteria_fingerprint or current_criteria_fp) and str(
        decision.criteria_fingerprint or ""
    ) != str(current_criteria_fp or ""):
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
    if current_last_note is not None and (
        last_note_at_emit is None or current_last_note > int(last_note_at_emit)
    ):
        reasons.append("recruiter_note_added")
        details["recruiter_note_added"] = {
            "at_emit": last_note_at_emit,
            "current": current_last_note,
        }

    # Verdict-aware: a pure re-score that shifts the score but does NOT change
    # the deterministic rule's verdict is a "hold" — immaterial to the outcome.
    # Drop the score-shift reasons so a mass re-score doesn't re-banner every
    # unchanged decision; a genuine flip (or a now-ambiguous verdict) keeps them.
    # No LLM — reuses the same pure-rule engine the bulk decisioner runs.
    if any(r in reasons for r in _SCORE_SHIFT_REASONS) and _verdict_holds(
        db, decision=decision, application=application, role=role
    ):
        reasons = [r for r in reasons if r not in _SCORE_SHIFT_REASONS]
        for r in _SCORE_SHIFT_REASONS:
            details.pop(r, None)

    return _finish()


_SCORE_SHIFT_REASONS = ("pre_screen_score_shifted", "assessment_score_shifted")


def _verdict_holds(
    db: Session,
    *,
    decision: AgentDecision,
    application: CandidateApplication,
    role: Role,
) -> bool:
    """True iff the deterministic policy verdict against the CURRENT scores
    equals the decision's queued ``decision_type`` — i.e. the score drift is
    immaterial to the outcome. Conservative: any error, or a now-ambiguous
    (escalate / skip) verdict, returns False so the banner stays. Lazy import
    avoids a circular dependency (bulk_decision_service → queue_decision →
    decision_staleness)."""
    try:
        from .bulk_decision_service import recompute_persisted_verdict

        recomputed = recompute_persisted_verdict(db, role=role, app=application)
        return recomputed is not None and recomputed == decision.decision_type
    except Exception:  # noqa: BLE001 — fail safe: keep the banner
        return False


def is_human_suppression_live(
    db: Session,
    decision: AgentDecision,
    *,
    application: CandidateApplication | None = None,
    role: Role | None = None,
    cache: "StalenessCache | None" = None,
) -> bool:
    """Whether a discarded/overridden decision should still suppress re-emit.

    A discard or override is an explicit human "no". It must hold until the
    inputs the decision was based on materially change — otherwise the agent
    re-queues the same verdict next cycle and silently overrides the human
    signal. Any staleness reason (new pre-screen / assessment score, new CV,
    edited criteria, recruiter note, cutoff change) counts as a material
    change that releases the suppression so the agent can legitimately
    re-decide on fresh inputs.

    Returns:
      True  — inputs unchanged → keep suppressing the re-emit.
      False — inputs drifted, OR there's no fingerprint baseline to compare
              against (pre-A1 rows). The caller may still apply its own
              cooldown for the no-baseline case.
    """
    if not (decision.input_fingerprint or {}):
        # No baseline captured — can't tell if inputs changed. Don't let an
        # ancient, fingerprint-less row suppress forever; defer to the caller.
        return False
    return not evaluate(
        db, decision, application=application, role=role, cache=cache
    ).is_stale


_REASON_LABELS = {
    "criteria_changed": "Role criteria edited",
    "cv_replaced": "Candidate uploaded a new CV",
    "pre_screen_score_shifted": "Pre-screen score changed",
    "assessment_score_shifted": "Assessment score changed",
    "cutoff_changed": "Pre-screen cutoff changed",
    "recruiter_note_added": "Recruiter note added",
    "engine_outdated": "Scored by an older model",
}


def _summarize(reasons: list[str]) -> str:
    """One-line human summary for the Decision Hub badge."""
    if not reasons:
        return ""
    if len(reasons) == 1:
        return _REASON_LABELS.get(reasons[0], reasons[0])
    primary = _REASON_LABELS.get(reasons[0], reasons[0])
    return f"{primary} (+{len(reasons) - 1} more)"
