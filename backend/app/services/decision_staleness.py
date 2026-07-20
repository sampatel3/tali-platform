"""Read-time detection of changed inputs behind queued decisions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from sqlalchemy.orm import Session

from ..cv_matching.holistic import HOLISTIC_ENGINE_VERSION, resolve_engine_version
from ..domains.assessments_runtime.role_support import is_resolved
from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.role_criterion import RoleCriterion
from ..models.role_feedback_note import RoleFeedbackNote
from .decision_policy_generation import (
    DecisionPolicyGeneration,
    policy_generation_drift,
)
from .decision_staleness_labels import summarize_staleness
from .knockout_generation import is_knockout_decision, knockout_generation_drift

if TYPE_CHECKING:
    from ..components.scoring.freshness import ScoreAttempt


# Five points is one decision-meaningful tier on the 0-100 score scale;
# smaller re-score noise must not churn the review queue.
SCORE_DRIFT_BAND = 5.0


@dataclass(frozen=True)
class StalenessReport:
    is_stale: bool
    reasons: list[str] = field(default_factory=list)
    summary: Optional[str] = None
    details: dict = field(default_factory=dict)


@dataclass
class StalenessCache:
    """Request-scoped memo that keeps batch evaluation at O(distinct roles)."""

    criteria_fp: dict[int, str | None] = field(default_factory=dict)
    latest_note_id: dict[int, int | None] = field(default_factory=dict)
    # Related-role thresholds are independently DB-backed per acting role.
    role_fit_threshold: dict[int, float | None] = field(default_factory=dict)
    latest_score_attempt: dict[int, "ScoreAttempt | None"] = field(default_factory=dict)
    role_intent_fp: dict[int, str] = field(default_factory=dict)
    criteria_rows: dict[int, tuple[RoleCriterion, ...]] = field(default_factory=dict)
    policy_generation: dict[int, DecisionPolicyGeneration] = field(default_factory=dict)
    knockout_generation: dict[int, str] = field(default_factory=dict)


def _score_drift(old: Optional[float], new: Optional[float]) -> bool:
    """Whether two concrete scores differ by a decision-meaningful band."""
    if old is None or new is None:
        return False
    try:
        return abs(float(new) - float(old)) >= SCORE_DRIFT_BAND
    except (TypeError, ValueError):
        return False


def criteria_content_fingerprint(
    db: Session, role_id: int, *, cache: "StalenessCache | None" = None
) -> str | None:
    """Hash active criteria content, excluding volatile database row IDs."""
    if cache is not None and role_id in cache.criteria_fp:
        return cache.criteria_fp[role_id]
    if cache is not None and role_id in cache.criteria_rows:
        rows = cache.criteria_rows[role_id]
    else:
        rows = tuple(
            db.query(RoleCriterion)
            .filter(
                RoleCriterion.role_id == role_id,
                RoleCriterion.deleted_at.is_(None),
            )
            .all()
        )
        if cache is not None:
            cache.criteria_rows[role_id] = rows
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
    """Re-point pending decisions at an explicitly accepted role generation.

    Used when criteria change in a way that must NOT invalidate in-flight
    decisions — e.g. an immaterial Workable spec edit, or the one-time
    backfill after the id->content fingerprint migration. The criteria hash
    and the role-intent component of the score token move together because
    both describe the same accepted role edit. Candidate, score-job, note,
    and cutoff state remain untouched.

    Returns the number of decisions updated.
    """
    from ..models.agent_decision import AgentDecision

    role = db.query(Role).filter(Role.id == int(role_id)).one_or_none()
    if role is None:
        return 0
    from .role_intent_fingerprint import role_intent_fingerprint

    cache = StalenessCache()
    new_fp = criteria_content_fingerprint(db, int(role_id), cache=cache)
    new_role_fp = role_intent_fingerprint(
        role, db=db, criteria_rows=cache.criteria_rows[int(role_id)]
    )
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
        score_generation = fp.get("score_generation")
        next_generation = (
            {
                **score_generation,
                "role_intent_fingerprint": new_role_fp,
            }
            if isinstance(score_generation, dict)
            else score_generation
        )
        if (
            decision.criteria_fingerprint == new_fp
            and fp.get("criteria_fingerprint") == new_fp
            and next_generation == score_generation
        ):
            continue
        decision.criteria_fingerprint = new_fp
        next_fp = {**fp, "criteria_fingerprint": new_fp}
        if isinstance(next_generation, dict):
            next_fp["score_generation"] = next_generation
        decision.input_fingerprint = next_fp
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
    """Whether the stored score would upgrade to the current holistic engine.

    Lazy import avoids a cycle; read errors must not crash the Hub."""
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
    fingerprint = decision.input_fingerprint or {}
    if role is None:
        role = db.query(Role).filter(Role.id == int(decision.role_id)).one_or_none()

    # Standard owner-role cards require a completed current score generation;
    # related-role and deterministic knockout cards have separate lifecycles.
    if role is not None and str(getattr(decision, "status", "")) in {
        "pending",
        "processing",
        "reverted_for_feedback",
    }:
        policy_drift = policy_generation_drift(
            db,
            decision,
            role,
            cache.policy_generation if cache is not None else None,
        )
        if policy_drift is not None:
            reasons.append("policy_generation_changed")
            details["policy_generation_changed"] = policy_drift
        knockout_drift = knockout_generation_drift(
            db, decision, cache.knockout_generation if cache is not None else None
        )
        if knockout_drift is not None:
            reason, drift = knockout_drift
            reasons.append(reason)
            details[reason] = drift
        from ..components.scoring.freshness import (
            application_score_status_allows_decision,
            latest_score_attempts,
            score_generation_from_fingerprint,
            score_generation_matches_observed,
            standard_owner_score_guard_applies,
        )

        if (
            not is_knockout_decision(decision)
            and application.role_id is not None
            and standard_owner_score_guard_applies(
                application_role_id=int(application.role_id),
                decision_role_id=int(decision.role_id),
                role_kind=getattr(role, "role_kind", None),
                decision_type=str(decision.decision_type),
            )
        ):
            application_id = int(application.id)
            if cache is not None and application_id in cache.latest_score_attempt:
                latest_attempt = cache.latest_score_attempt[application_id]
            else:
                latest_attempt = latest_score_attempts(db, [application_id]).get(
                    application_id
                )
                if cache is not None:
                    cache.latest_score_attempt[application_id] = latest_attempt
            latest_status = (
                latest_attempt.status if latest_attempt is not None else None
            )
            if not application_score_status_allows_decision(
                application, latest_status
            ):
                reasons.append("score_refresh_required")
                details["score_refresh_required"] = {
                    "latest_score_job_status": latest_status,
                }
            else:
                generation = score_generation_from_fingerprint(fingerprint)
                if generation is not None:
                    role_id = int(role.id)
                    if cache is not None and role_id in cache.role_intent_fp:
                        role_intent_fp = cache.role_intent_fp[role_id]
                    else:
                        from .role_intent_fingerprint import role_intent_fingerprint

                        criteria_rows = None
                        if cache is not None:
                            criteria_content_fingerprint(db, role_id, cache=cache)
                            criteria_rows = cache.criteria_rows[role_id]
                        role_intent_fp = role_intent_fingerprint(
                            role, db=db, criteria_rows=criteria_rows
                        )
                        if cache is not None:
                            cache.role_intent_fp[role_id] = role_intent_fp
                if generation is not None and not score_generation_matches_observed(
                    db, expected=generation, role=role, application=application,
                    current_attempt=latest_attempt,
                    current_role_intent_fingerprint=role_intent_fp,
                ):
                    current_job_id = (
                        latest_attempt.job_id if latest_attempt is not None else None
                    )
                    reasons.append("score_generation_changed")
                    details["score_generation_changed"] = {
                        "at_emit": generation.job_id,
                        "current": current_job_id,
                    }

    # Engine staleness is independent of fingerprints and sorts after input drift.
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
            summary=summarize_staleness(all_reasons) if is_stale else None,
            details=all_details,
        )

    if not fingerprint:
        return _finish()

    if role is None:
        return _finish()

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
    """Whether current scores preserve the queued deterministic verdict.

    Errors/ambiguous verdicts retain the stale banner; lazy import avoids a cycle."""
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
