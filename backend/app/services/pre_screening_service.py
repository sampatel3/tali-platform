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
    build_fraud_signals_payload,
    detect_cv_copy_paste,
)
from .pricing_service import Feature
from .taali_scoring import compute_role_fit_score
from .usage_metering_service import record_event as _meter_record_event
from .workable_actions_service import render_workable_note_template

logger = logging.getLogger("taali.pre_screening_service")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_score_100(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    if numeric <= 10.0:
        numeric *= 10.0
    return round(max(0.0, min(100.0, numeric)), 1)


def pre_screen_recommendation_label(score_100: float | None) -> str | None:
    if score_100 is None:
        return None
    if score_100 >= 80.0:
        return "Strong match"
    if score_100 >= 65.0:
        return "Proceed to screening"
    if score_100 >= 50.0:
        return "Manual review recommended"
    return "Below threshold"


def build_pre_screen_evidence(details: dict[str, Any] | None) -> dict[str, Any]:
    payload = details if isinstance(details, dict) else {}
    return sanitize_json_for_storage(
        {
            "summary": sanitize_text_for_storage(str(payload.get("summary") or "").strip()) or None,
            "matching_skills": [
                sanitize_text_for_storage(str(item).strip())
                for item in payload.get("matching_skills", [])
                if str(item or "").strip()
            ][:8],
            "missing_skills": [
                sanitize_text_for_storage(str(item).strip())
                for item in payload.get("missing_skills", [])
                if str(item or "").strip()
            ][:8],
            "concerns": [
                sanitize_text_for_storage(str(item).strip())
                for item in payload.get("concerns", [])
                if str(item or "").strip()
            ][:6],
            "score_rationale_bullets": [
                sanitize_text_for_storage(str(item).strip())
                for item in payload.get("score_rationale_bullets", [])
                if str(item or "").strip()
            ][:6],
            "requirements_coverage": payload.get("requirements_coverage")
            if isinstance(payload.get("requirements_coverage"), dict)
            else {},
            "requirements_assessment": payload.get("requirements_assessment")
            if isinstance(payload.get("requirements_assessment"), list)
            else [],
        }
    )


def pre_screen_snapshot(app: CandidateApplication) -> dict[str, Any]:
    details = app.cv_match_details if isinstance(app.cv_match_details, dict) else {}
    cv_fit_score = normalize_score_100(app.cv_match_score)
    requirements_fit_score = normalize_score_100(
        details.get("requirements_match_score_100")
        or details.get("requirements_match_score")
    )
    # ``cv_match_score`` is already the aggregated role_fit score
    # written by ``cv_score_orchestrator`` (= 0.40·cv_fit + 0.60·req in
    # v9). Re-running ``compute_role_fit_score`` here would double-count
    # requirements. Treat pre_screen as a pass-through of the aggregated
    # score so the directory list matches the candidate detail page.
    # For pre-screen-filtered candidates (cv_match_score=None), fall back to
    # the pre-screen score stored in cv_match_details so they appear in the
    # directory with their numeric pre-screen score instead of blank.
    if cv_fit_score is None:
        cv_fit_score = normalize_score_100(details.get("pre_screen_score_100"))
    pre_screen_score = cv_fit_score
    recommendation = sanitize_text_for_storage(
        str(
            getattr(app, "pre_screen_recommendation", None)
            or details.get("recommendation")
            or pre_screen_recommendation_label(pre_screen_score)
            or ""
        ).strip()
    ) or None
    evidence = (
        sanitize_json_for_storage(app.pre_screen_evidence)
        if isinstance(getattr(app, "pre_screen_evidence", None), dict)
        else build_pre_screen_evidence(details)
    )
    return {
        "cv_fit_score": cv_fit_score,
        "requirements_fit_score": requirements_fit_score,
        "pre_screen_score": pre_screen_score,
        "pre_screen_recommendation": recommendation,
        "pre_screen_evidence": evidence,
    }


def refresh_pre_screening_fields(app: CandidateApplication) -> dict[str, Any]:
    snapshot = pre_screen_snapshot(app)
    app.requirements_fit_score_100 = snapshot["requirements_fit_score"]
    app.pre_screen_score_100 = snapshot["pre_screen_score"]
    app.pre_screen_recommendation = snapshot["pre_screen_recommendation"]
    app.pre_screen_evidence = snapshot["pre_screen_evidence"]
    if snapshot["pre_screen_score"] is not None:
        app.rank_score = snapshot["pre_screen_score"]
    elif app.workable_score is not None:
        app.rank_score = app.workable_score
    else:
        app.rank_score = app.cv_match_score
    return snapshot


def resolved_auto_reject_config(
    org: Organization | None,
    role: Role | None,
    *,
    db: Session | None = None,
) -> dict[str, Any]:
    org_config = org.workable_config if org and isinstance(org.workable_config, dict) else {}
    enabled = (
        role.auto_reject_enabled
        if role is not None and role.auto_reject_enabled is not None
        else bool(org_config.get("auto_reject_enabled"))
    )
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
        threshold = (
            role.auto_reject_threshold_100
            if role is not None and role.auto_reject_threshold_100 is not None
            else org_config.get("auto_reject_threshold_100")
        )
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
            str(
                (role.workable_disqualify_reason_id if role and role.workable_disqualify_reason_id else None)
                or org_config.get("workable_disqualify_reason_id")
                or ""
            ).strip()
        ) or None,
        "auto_reject_note_template": sanitize_text_for_storage(
            str(
                (role.auto_reject_note_template if role and role.auto_reject_note_template is not None else None)
                or org_config.get("auto_reject_note_template")
                or ""
            ).strip()
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


# ---------------------------------------------------------------------------
# Standalone pre-screen — runs the cheap pre-screen LLM and persists results
# without triggering the v3 full-score path. Used by the "Pre-screen new" and
# "Refresh pre-screen" batch actions, where we explicitly want to decouple
# pre-screen from scoring.
# ---------------------------------------------------------------------------

def execute_pre_screen_only(
    app: CandidateApplication,
    *,
    db: Session | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """Canonical Stage 1 engine: run pre-screen LLM + fraud detection.

    This is the ONE place pre-screen scoring and fraud detection live.
    Stage 2 (cv_score_orchestrator) calls this when a candidate hasn't
    been pre-screened yet so fraudulent CVs are filtered before the
    expensive v3 scoring call ever runs.

    Args:
      app: The application to pre-screen.
      db: Optional session — when provided, usage metering is recorded.
      client: Optional org-scoped Anthropic client for billing routing.

    Returns ``{status, score, recommendation, decision, reason, ...}`` where
    ``status`` is ``ok | skipped | error``. Does NOT touch
    ``cv_match_score`` / ``cv_match_details`` / ``cv_match_scored_at`` so
    a subsequent score job can still run cleanly.

    Idempotency: caller is expected to filter by ``pre_screen_run_at``
    before invoking. The underlying ``run_pre_screen`` has its own cache,
    so duplicates are cheap.
    """
    if app is None or app.id is None:
        return {"status": "skipped", "reason": "no_application"}

    cv_text = (app.cv_text or "").strip()
    role = app.role
    job_spec_text = ((role.job_spec_text if role else None) or "").strip()
    if not cv_text:
        return {"status": "skipped", "reason": "no_cv"}
    if not job_spec_text:
        return {"status": "skipped", "reason": "no_job_spec"}

    from ..cv_matching import MODEL_VERSION as PRE_SCREEN_MODEL_VERSION
    from ..cv_matching.runner_pre_screen import run_pre_screen
    from ..cv_matching.schemas import Priority, RequirementInput

    requirements: list[RequirementInput] = []
    for c in sorted((role.criteria or []), key=lambda c: getattr(c, "ordering", 0)):
        if getattr(c, "deleted_at", None) is not None:
            continue
        text = str(c.text or "").strip()
        if not text:
            continue
        bucket = str(getattr(c, "bucket", None) or ("must" if bool(c.must_have) else "preferred"))
        # Constraints (timezone, start date, etc.) are pass/fail filters about
        # logistics, not candidate quality. We surface them with MUST_HAVE
        # priority so the pre-screen call flags mismatches prominently — the
        # agent can decide whether the constraint is fatal or worth a chat.
        if bucket in ("must", "constraint"):
            priority = Priority.MUST_HAVE
        else:
            priority = Priority.STRONG_PREFERENCE
        requirements.append(
            RequirementInput(
                id=f"crit_{int(c.id)}",
                requirement=text,
                priority=priority,
            )
        )

    try:
        pre = run_pre_screen(cv_text, job_spec_text, requirements, client=client)
    except Exception as exc:  # noqa: BLE001 — guard the LLM call
        return {"status": "error", "reason": f"pre_screen_failed: {exc}"[:200]}

    # Record usage metering when a session is available. Telemetry must
    # never break pre-screen, so swallow any failure.
    if db is not None and getattr(app, "organization_id", None):
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
                cache_hit=pre.cache_hit,
                entity_id=f"application:{app.id}",
            )
        except Exception:  # pragma: no cover — defensive
            logger.exception(
                "usage_metering record_event failed for app=%s feature=prescreen",
                app.id,
            )

    # Deterministic fraud check — currently CV ↔ JD copy-paste only.
    # Always compute (so we can calibrate the threshold from real data
    # later) but only cap the score when the threshold is crossed.
    fraud = detect_cv_copy_paste(
        cv_text,
        job_spec_text,
        threshold=settings.FRAUD_COPY_PASTE_THRESHOLD,
    )
    score, fraud_capped = apply_fraud_penalty(
        pre.score,
        fraud,
        cap_score=settings.FRAUD_PENALTY_CAP_SCORE,
    )
    fraud_signals = build_fraud_signals_payload(fraud)
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
        recommendation = pre_screen_recommendation_label(score)
        summary = sanitize_text_for_storage(str(pre.reason or "").strip()) or None
        decision = pre.decision

    # Persist on the application. We deliberately do NOT touch cv_match_*
    # so a subsequent score job can still run.
    app.pre_screen_score_100 = score
    app.requirements_fit_score_100 = score  # parity with snapshot fallback
    app.pre_screen_recommendation = recommendation
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
    - Never pre-screened → needed.
    - CV uploaded after the last pre-screen → needed (stale).
    - Otherwise → not needed.
    """
    if app is None:
        return False
    if not (app.cv_text or "").strip():
        return False
    last_run = getattr(app, "pre_screen_run_at", None)
    if last_run is None:
        return app.pre_screen_recommendation is None
    cv_uploaded = getattr(app, "cv_uploaded_at", None)
    if cv_uploaded is not None and cv_uploaded > last_run:
        return True
    return False


# Backward-compat re-export. The decider lives in the decision_policy
# package now (engine + auto-reject sit together) but several callers
# still import it from here. Lazy import keeps a load-time cycle from
# forming, since auto_reject imports helpers (snapshot, config) from
# this module.
def evaluate_auto_reject_decision(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from ..decision_policy.auto_reject import (
        evaluate_auto_reject_decision as _impl,
    )
    return _impl(*args, **kwargs)
