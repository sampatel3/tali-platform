from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from ..platform.config import settings
from .document_service import sanitize_json_for_storage, sanitize_text_for_storage
from .fraud_detection import (
    apply_fraud_penalty,
    apply_unverified_claim_prescreen_penalty,
    build_fraud_signals_payload,
    detect_cv_copy_paste,
    persist_fraud_filtered_prescreen,
)
from .pricing_service import Feature
from .pre_screen_retry_policy import (
    PRE_SCREEN_DETERMINISTIC_ERROR_BACKOFF as _DETERMINISTIC_ERROR_BACKOFF,
    PRE_SCREEN_TRANSIENT_ERROR_BACKOFF as _TRANSIENT_ERROR_BACKOFF,
    build_pre_screen_error_retry_metadata,
    pre_screen_error_retry_due,
)
from .provider_error_evidence import safe_provider_error_code
from .usage_credit_reservations import InsufficientRoleBudgetError
from .usage_metering_service import InsufficientCreditsError
from .usage_metering_service import record_event as _meter_record_event
from .workable_actions_service import render_workable_note_template
from .workable_context_service import format_workable_context
# Compatibility re-exports for callers; implementations live in the snapshot module.
from .pre_screening_snapshot import (  # noqa: F401
    build_pre_screen_evidence,
    normalize_score_100,
    pre_screen_recommendation_label,
    pre_screen_snapshot,
    refresh_pre_screening_fields,
)

logger = logging.getLogger("taali.pre_screening_service")
PRE_SCREEN_ERROR_BACKOFF = _DETERMINISTIC_ERROR_BACKOFF
PRE_SCREEN_TRANSIENT_ERROR_BACKOFF = _TRANSIENT_ERROR_BACKOFF


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def resolved_auto_reject_config(
    org: Organization | None,
    role: Role | None,
    *,
    db: Session | None = None,
) -> dict[str, Any]:
    # Workspace-level switches still live in ``org.workable_config`` (the
    # Recruiter Settings → Workable section). Per-role overrides for these
    # four keys were dropped in alembic 076 — every role inherits the
    # org defaults now. The canonical per-role knobs are
    # ``role.score_threshold`` (cutoff) and ``role.auto_reject_pre_screen`` (HITL).
    org_config = org.workable_config if org and isinstance(org.workable_config, dict) else {}
    enabled = bool(org_config.get("auto_reject_enabled"))
    # ``auto`` mode delegates threshold selection to the agent's algorithm
    # (see ``services.auto_threshold_service``). Requires a session — when
    # the caller doesn't have one, fall through to the recruiter's manual
    # value, which is still the better answer than a hard-coded number.
    mode = getattr(role, "auto_reject_threshold_mode", None) or "manual"
    threshold: Any
    if mode == "auto" and role is not None and db is not None:
        from .auto_threshold_service import compute_recommended_threshold

        threshold = compute_recommended_threshold(db, role=role).value
    else:
        threshold = role.score_threshold if role is not None else None
    return {
        "enabled": bool(enabled),
        "threshold_100": normalize_score_100(threshold),
        "workable_actor_member_id": sanitize_text_for_storage(
            str(
                (role.workable_actor_member_id if role and role.workable_actor_member_id else None)
                or org_config.get("workable_actor_member_id")
                or ""
            ).strip()
        ) or None,
        "workable_disqualify_reason_id": sanitize_text_for_storage(
            str(org_config.get("workable_disqualify_reason_id") or "").strip()
        ) or None,
        "auto_reject_note_template": sanitize_text_for_storage(
            str(org_config.get("auto_reject_note_template") or "").strip()
        ) or None,
    }


def render_auto_reject_note(
    template: str | None,
    *,
    candidate_name: str | None,
    role_name: str | None,
    pre_screen_score: float | None,
    threshold_100: float | None,
    recommendation: str | None,
) -> str | None:
    candidate_label = sanitize_text_for_storage(str(candidate_name or "Candidate").strip()) or "Candidate"
    role_label = sanitize_text_for_storage(str(role_name or "Role").strip()) or "Role"
    mapping = {
        "candidate_name": candidate_label,
        "role_name": role_label,
        "pre_screen_score": f"{pre_screen_score:.1f}" if pre_screen_score is not None else "n/a",
        "threshold_100": f"{threshold_100:.1f}" if threshold_100 is not None else "n/a",
        "recommendation": sanitize_text_for_storage(str(recommendation or "").strip()) or "Below threshold",
    }
    rendered = render_workable_note_template(template, **mapping)
    if rendered:
        return rendered
    return (
        f"Auto-rejected from Workable sync. {candidate_label} scored {mapping['pre_screen_score']}/100 "
        f"for {role_label} against a threshold of {mapping['threshold_100']}/100. "
        f"Recommendation: {mapping['recommendation']}."
    )[:256]


def _persist_pre_screen_error(
    app: CandidateApplication,
    *,
    reason: str,
    trace_id: str | None = None,
    prompt_version: str | None = None,
) -> None:
    """Record a pre-screen LLM failure on the application.

    Leaves both ``pre_screen_score_100`` and ``cv_match_score`` as NULL
    so the UI surfaces "needs rescore" instead of a fabricated score.
    Populates ``pre_screen_error_reason`` so the recruiter can see why
    the agent couldn't decide, and writes an evidence row with
    ``decision: 'error'`` for the existing rendering logic.
    """
    retry_metadata = build_pre_screen_error_retry_metadata(app, reason=reason)
    app.pre_screen_score_100 = None
    app.genuine_pre_screen_score_100 = None
    app.requirements_fit_score_100 = None
    app.cv_match_score = None
    app.cv_match_details = None
    app.cv_match_scored_at = None
    app.pre_screen_recommendation = None
    app.pre_screen_error_reason = sanitize_text_for_storage(reason or "")[:500] or None
    app.pre_screen_evidence = sanitize_json_for_storage(
        {
            "summary": sanitize_text_for_storage(reason or "")[:240] or None,
            "matching_skills": [],
            "missing_skills": [],
            "concerns": [],
            "score_rationale_bullets": [],
            "requirements_coverage": {},
            "requirements_assessment": [],
            "decision": "error",
            "trace_id": trace_id,
            "prompt_version": prompt_version,
            "cache_hit": False,
            "llm_score_100": None,
            **retry_metadata,
        }
    )
    # Stamp ``pre_screen_run_at`` even on error so the retry-backoff in
    # ``application_needs_pre_screen`` knows when the last attempt was.
    # Leaving this NULL once produced 7,668 repeated Anthropic calls. Explicit
    # transient failures now receive one short retry; repeated transient and
    # deterministic failures retain the six-hour cost guard.
    # New-CV upload still beats the timestamp via the staleness check,
    # so a re-uploaded CV always retries immediately.
    app.pre_screen_run_at = _utcnow()
    # rank_score falls back to workable_score so the directory still
    # has *some* ordering signal — but never the stale agent score.
    app.rank_score = app.workable_score


def mark_auto_reject_state(
    app: CandidateApplication,
    *,
    state: str,
    reason: str | None,
    triggered: bool,
) -> None:
    app.auto_reject_state = sanitize_text_for_storage(str(state or "").strip()) or None
    app.auto_reject_reason = sanitize_text_for_storage(str(reason or "").strip()) or None
    app.auto_reject_triggered_at = _utcnow() if triggered else None


# Standalone pre-screen — runs the cheap pre-screen LLM and persists results
# without triggering the v3 full-score path. Used by the "Pre-screen new" and
# "Refresh pre-screen" batch actions, where we explicitly want to decouple
# pre-screen from scoring.

def execute_pre_screen_only(
    app: CandidateApplication,
    *,
    db: Session | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """Canonical Stage 1 engine: run pre-screen LLM + fraud detection.

    This is the one place pre-screen scoring and fraud detection live; Stage 2
    calls it before full scoring when a candidate has not been pre-screened.

    Args:
      app: The application to pre-screen.
      db: Optional session — when provided, usage metering is recorded.
      client: Optional org-scoped Anthropic client for billing routing.

    Returns ``{status, score, recommendation, decision, reason, ...}`` where
    ``status`` is ``ok | skipped | error``. Does NOT touch
    ``cv_match_score`` / ``cv_match_details`` / ``cv_match_scored_at`` so
    a subsequent score job can still run cleanly.

    Callers filter by ``pre_screen_run_at``; ``run_pre_screen`` also caches.
    """
    if app is None or app.id is None:
        return {"status": "skipped", "reason": "no_application"}

    cv_text = (app.cv_text or "").strip()
    role = app.role
    from .role_requirement_service import (
        build_pre_screen_requirements,
        resolve_role_job_spec,
    )

    job_spec_text = resolve_role_job_spec(
        role,
        db=db,
        agent_name="pre_screen",
    )
    if not cv_text:
        return {"status": "skipped", "reason": "no_cv"}
    if not job_spec_text:
        return {"status": "skipped", "reason": "no_job_spec"}

    from ..cv_matching import MODEL_VERSION as PRE_SCREEN_MODEL_VERSION
    from ..cv_matching.runner_pre_screen import run_pre_screen
    from .pre_screen_usage_admission import run_with_pre_screen_admission
    requirements = build_pre_screen_requirements(role)

    # Workable metadata can carry hard-constraint signal absent from the CV.
    workable_context = ""
    try:
        workable_context = format_workable_context(
            candidate=getattr(app, "candidate", None),
            application=app,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "format_workable_context failed app_id=%s error_type=%s",
            app.id, type(exc).__name__,
        )

    # Deterministic CV↔JD overlap detection needs no LLM. Always compute and
    # persist the signal, but only short-circuit/cap when an operator has
    # explicitly enabled the score-changing policy. Flag-only is the safe
    # default because copied phrasing alone is not proof of candidate fraud.
    copy_paste_action = settings.FRAUD_COPY_PASTE_ACTION
    fraud = detect_cv_copy_paste(
        cv_text,
        job_spec_text,
        threshold=settings.FRAUD_COPY_PASTE_THRESHOLD,
        min_block_words=settings.FRAUD_COPY_PASTE_MIN_BLOCK_WORDS,
    )
    if fraud.triggered and copy_paste_action == "cap":
        return persist_fraud_filtered_prescreen(app, fraud, cap_score=settings.FRAUD_PENALTY_CAP_SCORE)

    # Meter actual calls (including failures); the wrapper owns its sessions.
    pre_screen_metering_context = None
    if db is not None and getattr(app, "organization_id", None):
        pre_screen_metering_context = {
            "organization_id": int(app.organization_id),
            "role_id": getattr(app, "role_id", None),
            "entity_id": f"application:{app.id}",
            "candidate_id": getattr(app, "candidate_id", None),
        }
    try:
        pre, credit_reservation = run_with_pre_screen_admission(
            lambda admitted: run_pre_screen(
                cv_text, job_spec_text, requirements, client=client,
                workable_context=workable_context or None,
                metering_context=admitted,
                cache_read_session=db,
            ),
            metering_context=pre_screen_metering_context,
            trace_id=f"pre-screen:application:{int(app.id)}",
            model=PRE_SCREEN_MODEL_VERSION,
        )
    except Exception as exc:  # noqa: BLE001 — guard the admission/LLM boundary
        is_budget_failure = isinstance(
            exc, (InsufficientCreditsError, InsufficientRoleBudgetError)
        )
        failure_code = safe_provider_error_code(
            exc,
            operation=(
                "budget_admission_failed" if is_budget_failure else "pre_screen_failed"
            ),
        )
        logger.log(
            logging.INFO if is_budget_failure else logging.WARNING,
            "Pre-screen execution failed app=%s error_code=%s",
            app.id,
            failure_code,
        )
        _persist_pre_screen_error(app, reason=failure_code)
        return {"status": "error", "reason": failure_code}

    # CACHE HITS ONLY. An actual Anthropic call is metered by the wrapper
    # above (per call, including errors/retries). A cache hit makes no
    # call, so the wrapper never runs — record it here to preserve cached-
    # result billing. Recording a cache MISS here too would double-count.
    if (
        pre.cache_hit
        and db is not None
        and getattr(app, "organization_id", None)
        and (
            pre.input_tokens or pre.output_tokens or pre.cache_read_tokens
            or pre.cache_creation_tokens or credit_reservation is not None
        )
    ):
        try:
            _meter_record_event(
                db,
                organization_id=int(app.organization_id),
                role_id=getattr(app, "role_id", None),
                feature=Feature.PRESCREEN,
                model=PRE_SCREEN_MODEL_VERSION,
                input_tokens=pre.input_tokens,
                output_tokens=pre.output_tokens,
                cache_read_tokens=pre.cache_read_tokens,
                cache_creation_tokens=pre.cache_creation_tokens,
                cache_hit=True,
                entity_id=f"application:{app.id}",
                credit_reservation=(
                    credit_reservation.as_metering_payload()
                    if credit_reservation is not None else None
                ),
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "usage_metering record_event failed app_id=%s feature=prescreen error_type=%s",
                app.id, type(exc).__name__,
            )

    # When the LLM call itself returned ``decision == "error"`` (credit
    # exhaustion, network timeout, JSON parse failure, etc.) we MUST NOT
    # fall through to v3 cv_match scoring. Doing so used to mirror a
    # high CV-fit score into ``pre_screen_score_100`` via the refresh
    # helpers, hiding the error from the recruiter and making it look
    # like pre-screen passed when it never ran.
    if pre.decision == "error" or pre.score is None:
        _persist_pre_screen_error(
            app,
            reason=(pre.reason or "pre_screen_unknown_error")[:500],
            trace_id=pre.trace_id,
            prompt_version=pre.prompt_version,
        )
        return {
            "status": "error",
            "reason": (pre.reason or "pre_screen_unknown_error")[:200],
            "trace_id": pre.trace_id,
            "prompt_version": pre.prompt_version,
        }

    # ``fraud`` was computed above. In cap mode, a triggered result already
    # short-circuited; flag/off modes deliberately leave the LLM score intact.
    if copy_paste_action == "cap":
        score, fraud_capped = apply_fraud_penalty(
            pre.score,
            fraud,
            cap_score=settings.FRAUD_PENALTY_CAP_SCORE,
        )
    else:
        score, fraud_capped = pre.score, False
    # Soft penalty when the gate flags an extraordinary CV-uncorroborated claim (skipped if copy-paste already capped).
    score, unverified_penalised = apply_unverified_claim_prescreen_penalty(
        score,
        pre.unverified_claim and not fraud_capped,
        penalty=settings.FRAUD_PRESCREEN_UNVERIFIED_PENALTY,
    )
    fraud_signals = build_fraud_signals_payload(fraud, action=copy_paste_action)
    fraud_signals["unverified_claim"] = {
        "flagged": pre.unverified_claim,
        "penalty_applied": unverified_penalised,
    }
    # Cross-candidate duplicate / mass-apply tells (flag-only, bounded, no LLM).
    from .fraud_cross_candidate import detect_cross_candidate_signals

    fraud_signals.update(detect_cross_candidate_signals(db, app))
    if fraud_capped:
        # Replace the LLM rationale with a fraud-specific one so the
        # directory and report copy doesn't claim the candidate is a poor
        # skills match when the real reason is plagiarism.
        recommendation = "Below threshold"
        summary = (
            f"Pre-screen filtered: CV contains {fraud.score:.0%} text copied "
            f"verbatim from the job description (threshold {fraud.threshold:.0%})."
        )
        decision = "no"
    else:
        # Stage-1 labels use the Stage-1 cutoff; the role send threshold is a
        # downstream full-score policy, not a second pre-screen gate.
        from .prescreen_gate_calibration import resolve_enforced_gate_threshold

        threshold_100 = resolve_enforced_gate_threshold(db, role=role)
        recommendation = pre_screen_recommendation_label(score, threshold_100)
        summary = sanitize_text_for_storage(str(pre.reason or "").strip()) or None
        decision = pre.decision

    # Persist; don't touch cv_match_* so a later score job still runs.
    app.pre_screen_score_100 = score
    # Durable genuine pre-screen score — never overwritten by cv_match scoring.
    app.genuine_pre_screen_score_100 = score
    app.requirements_fit_score_100 = score  # parity with snapshot fallback
    app.pre_screen_recommendation = recommendation
    app.pre_screen_error_reason = None
    app.pre_screen_evidence = sanitize_json_for_storage(
        {
            "summary": summary,
            "matching_skills": [],
            "missing_skills": [],
            "concerns": [],
            "score_rationale_bullets": [],
            "requirements_coverage": {},
            "requirements_assessment": [],
            "decision": decision,
            "trace_id": pre.trace_id,
            "prompt_version": pre.prompt_version,
            "cache_hit": pre.cache_hit,
            "fraud_signals": fraud_signals,
            "fraud_capped": fraud_capped,
            "llm_score_100": pre.score,  # original LLM score before cap
        }
    )
    app.pre_screen_run_at = _utcnow()

    # Keep rank_score in sync so the directory list orders correctly even
    # before a full score is run.
    if score is not None:
        app.rank_score = score

    return {
        "status": "ok",
        "score": score,
        "recommendation": recommendation,
        "decision": decision,
        "reason": summary or pre.reason,
        "cache_hit": pre.cache_hit,
        "fraud_capped": fraud_capped,
        "prompt_version": pre.prompt_version,
        "trace_id": pre.trace_id,
        "fraud_signals": fraud_signals,
        "llm_score_100": pre.score,
    }


def application_needs_pre_screen(app: CandidateApplication) -> bool:
    """True if pre-screen should be (re-)run for this application.

    Logic:
    - No CV → not needed (and impossible).
    - ``pre_screen_run_at`` is NULL → needed (never attempted).
    - CV uploaded after the last pre-screen → needed (stale). Always
      beats the error backoff — a re-uploaded CV is the canonical signal
      that the candidate wants another shot.
    - An explicit transient 429/5xx/timeout/network error gets one retry after
      ``PRE_SCREEN_TRANSIENT_ERROR_BACKOFF``. A repeated transient error and
      every deterministic/unknown error use ``PRE_SCREEN_ERROR_BACKOFF``.
      This preserves fast self-healing without recreating the historical
      7,668-call retry storm.
    - Otherwise → not needed.
    """
    if app is None:
        return False
    if not (app.cv_text or "").strip():
        return False
    last_run = getattr(app, "pre_screen_run_at", None)
    if last_run is None:
        return True
    cv_uploaded = getattr(app, "cv_uploaded_at", None)
    if cv_uploaded is not None and cv_uploaded > last_run:
        return True
    # Error backoff. Successful pre-screen on a current CV → already
    # done, no retry. Errored pre-screen → retry only once the backoff
    # window has elapsed (transient errors self-heal; persistent errors
    # don't burn 48 ticks a day).
    error_reason = getattr(app, "pre_screen_error_reason", None)
    if error_reason:
        return pre_screen_error_retry_due(app)
    return False


# Backward-compatible lazy re-export avoids a decision-policy import cycle.
def evaluate_auto_reject_decision(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from ..decision_policy.auto_reject import (
        evaluate_auto_reject_decision as _impl,
    )
    return _impl(*args, **kwargs)
