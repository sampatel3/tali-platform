from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from .document_service import sanitize_json_for_storage, sanitize_text_for_storage
from .taali_scoring import compute_role_fit_score
from .workable_actions_service import render_workable_note_template


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
        getattr(app, "requirements_fit_score_100", None)
        if getattr(app, "requirements_fit_score_100", None) is not None
        else details.get("requirements_match_score_100")
    )
    pre_screen_score = normalize_score_100(
        getattr(app, "pre_screen_score_100", None)
        if getattr(app, "pre_screen_score_100", None) is not None
        else compute_role_fit_score(cv_fit_score, requirements_fit_score)
    )
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


def resolved_auto_reject_config(org: Organization | None, role: Role | None) -> dict[str, Any]:
    org_config = org.workable_config if org and isinstance(org.workable_config, dict) else {}
    enabled = (
        role.auto_reject_enabled
        if role is not None and role.auto_reject_enabled is not None
        else bool(org_config.get("auto_reject_enabled"))
    )
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


def evaluate_auto_reject_decision(
    app: CandidateApplication,
    *,
    org: Organization | None,
    role: Role | None,
) -> dict[str, Any]:
    snapshot = pre_screen_snapshot(app)
    config = resolved_auto_reject_config(org, role)
    score = snapshot["pre_screen_score"]
    threshold = config["threshold_100"]

    if app.application_outcome != "open":
        return {
            "should_trigger": False,
            "state": "skipped",
            "reason": "Application is already closed locally",
            "config": config,
            "snapshot": snapshot,
        }
    if not config["enabled"]:
        return {
            "should_trigger": False,
            "state": "disabled",
            "reason": "Auto reject is disabled",
            "config": config,
            "snapshot": snapshot,
        }
    if threshold is None:
        return {
            "should_trigger": False,
            "state": "disabled",
            "reason": "Auto reject threshold is not configured",
            "config": config,
            "snapshot": snapshot,
        }
    if score is None:
        return {
            "should_trigger": False,
            "state": "pending_score",
            "reason": "Pre-screen score is not available yet",
            "config": config,
            "snapshot": snapshot,
        }
    if not getattr(app, "workable_candidate_id", None):
        return {
            "should_trigger": False,
            "state": "skipped",
            "reason": "Candidate is not linked to Workable",
            "config": config,
            "snapshot": snapshot,
        }

    if score < threshold:
        return {
            "should_trigger": True,
            "state": "eligible",
            "reason": f"Pre-screen score {score:.1f} is below configured threshold {threshold:.1f}",
            "config": config,
            "snapshot": snapshot,
        }
    return {
        "should_trigger": False,
        "state": "not_triggered",
        "reason": f"Pre-screen score {score:.1f} meets threshold {threshold:.1f}",
        "config": config,
        "snapshot": snapshot,
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
