from __future__ import annotations

import re
from typing import Any

from ..components.integrations.workable.service import WorkableService
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from .document_service import sanitize_text_for_storage

WORKABLE_ALLOWED_SCOPES = ("r_jobs", "r_candidates", "w_candidates")
WORKABLE_WRITE_SCOPE = "w_candidates"

_DOUBLE_BRACE_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _dedupe_scopes(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        token = str(value or "").strip()
        if not token or token not in WORKABLE_ALLOWED_SCOPES or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def workable_granted_scopes(org: Organization | None) -> list[str]:
    if org is None:
        return []
    config = org.workable_config if isinstance(org.workable_config, dict) else {}
    raw_scopes = config.get("granted_scopes")
    cleaned = _dedupe_scopes(raw_scopes if isinstance(raw_scopes, list) else [])
    if cleaned:
        if WORKABLE_WRITE_SCOPE in cleaned:
            for required in ("r_jobs", "r_candidates"):
                if required not in cleaned:
                    cleaned.insert(0 if required == "r_jobs" else 1, required)
        return cleaned

    if not getattr(org, "workable_connected", False):
        return []

    inferred = ["r_jobs", "r_candidates"]
    if (
        str(config.get("email_mode") or "manual_taali") == "workable_preferred_fallback_manual"
        or bool(config.get("auto_reject_enabled"))
    ):
        inferred.append(WORKABLE_WRITE_SCOPE)
    return inferred


def workable_has_scope(org: Organization | None, scope: str) -> bool:
    return scope in workable_granted_scopes(org)


def workable_can_write_candidates(org: Organization | None) -> bool:
    return workable_has_scope(org, WORKABLE_WRITE_SCOPE)


def resolved_workable_action_config(org: Organization | None, role: Role | None = None) -> dict[str, Any]:
    org_config = org.workable_config if org and isinstance(org.workable_config, dict) else {}
    actor_member_id = sanitize_text_for_storage(
        str(
            (role.workable_actor_member_id if role and role.workable_actor_member_id else None)
            or org_config.get("workable_actor_member_id")
            or ""
        ).strip()
    ) or None
    disqualify_reason_id = sanitize_text_for_storage(
        str(
            (role.workable_disqualify_reason_id if role and role.workable_disqualify_reason_id else None)
            or org_config.get("workable_disqualify_reason_id")
            or ""
        ).strip()
    ) or None
    note_template = sanitize_text_for_storage(
        str(
            (role.auto_reject_note_template if role and role.auto_reject_note_template is not None else None)
            or org_config.get("auto_reject_note_template")
            or ""
        ).strip()
    ) or None
    scopes = workable_granted_scopes(org)
    return {
        "granted_scopes": scopes,
        "has_write_scope": WORKABLE_WRITE_SCOPE in scopes,
        "actor_member_id": actor_member_id,
        "workable_disqualify_reason_id": disqualify_reason_id,
        "auto_reject_note_template": note_template,
    }


def render_workable_note_template(template: str | None, **mapping: Any) -> str | None:
    raw_template = sanitize_text_for_storage(str(template or "").strip())
    if not raw_template:
        return None
    normalized = _DOUBLE_BRACE_PLACEHOLDER_RE.sub(r"{\1}", raw_template)
    safe_mapping = _SafeFormatDict(
        {
            key: sanitize_text_for_storage(str(value).strip()) if value is not None else ""
            for key, value in mapping.items()
        }
    )
    try:
        rendered = normalized.format_map(safe_mapping)
    except Exception:
        rendered = normalized
    cleaned = sanitize_text_for_storage(rendered).strip()
    return cleaned[:256] if cleaned else None


def build_workable_reject_note(
    *,
    app: CandidateApplication | None,
    role: Role | None,
    template: str | None,
    reason: str | None = None,
    threshold_100: float | int | None = None,
) -> str | None:
    candidate = getattr(app, "candidate", None)
    candidate_name = sanitize_text_for_storage(
        str(
            getattr(candidate, "full_name", None)
            or getattr(candidate, "email", None)
            or "Candidate"
        ).strip()
    ) or "Candidate"
    role_name = sanitize_text_for_storage(str(getattr(role, "name", None) or "Role").strip()) or "Role"
    pre_screen_score = getattr(app, "pre_screen_score_100", None)
    recommendation = sanitize_text_for_storage(str(getattr(app, "pre_screen_recommendation", None) or "").strip()) or None
    rendered = render_workable_note_template(
        template,
        candidate_name=candidate_name,
        role_name=role_name,
        pre_screen_score=f"{float(pre_screen_score):.1f}" if pre_screen_score is not None else "",
        threshold_100=f"{float(threshold_100):.1f}" if threshold_100 is not None else "",
        recommendation=recommendation or "",
        action_reason=sanitize_text_for_storage(str(reason or "").strip()) or "",
    )
    if rendered:
        return rendered

    fallback_reason = sanitize_text_for_storage(str(reason or "").strip()) or None
    if fallback_reason:
        return fallback_reason[:256]

    if pre_screen_score is not None and threshold_100 is not None:
        fallback = (
            f"Auto-rejected from TAALI sync. {candidate_name} scored {float(pre_screen_score):.1f}/100 "
            f"for {role_name} against a threshold of {float(threshold_100):.1f}/100."
        )
        cleaned = sanitize_text_for_storage(fallback).strip()
        return cleaned[:256] if cleaned else None
    return None


def _candidate_id_from_app(app: CandidateApplication | None) -> str | None:
    return sanitize_text_for_storage(str(getattr(app, "workable_candidate_id", None) or "").strip()) or None


def _build_failure_result(
    *,
    action: str,
    code: str,
    message: str,
    config: dict[str, Any],
    response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "action": action,
        "code": code,
        "message": message,
        "config": config,
        "response": response or {},
    }


def _build_success_result(
    *,
    action: str,
    message: str,
    config: dict[str, Any],
    response: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    payload = {
        "success": True,
        "action": action,
        "code": "ok",
        "message": message,
        "config": config,
        "response": response or {},
    }
    if note is not None:
        payload["note"] = note
    return payload


def _validate_writeable_org(org: Organization | None, *, config: dict[str, Any], action: str) -> dict[str, Any] | None:
    if org is None or not org.workable_connected or not org.workable_access_token or not org.workable_subdomain:
        return _build_failure_result(
            action=action,
            code="missing_connection",
            message="Workable is not connected for candidate write-back",
            config=config,
        )
    if not config.get("has_write_scope"):
        return _build_failure_result(
            action=action,
            code="missing_write_scope",
            message="Workable token is missing w_candidates scope",
            config=config,
        )
    if not config.get("actor_member_id"):
        return _build_failure_result(
            action=action,
            code="missing_actor_member_id",
            message="Workable actor member is not configured",
            config=config,
        )
    return None


def disqualify_candidate_in_workable(
    *,
    org: Organization | None,
    app: CandidateApplication | None,
    role: Role | None = None,
    reason: str | None = None,
    note_template: str | None = None,
    threshold_100: float | int | None = None,
    withdrew: bool = False,
) -> dict[str, Any]:
    config = resolved_workable_action_config(org, role=role)
    validation_error = _validate_writeable_org(org, config=config, action="disqualify")
    if validation_error is not None:
        return validation_error

    candidate_id = _candidate_id_from_app(app)
    if not candidate_id:
        return _build_failure_result(
            action="disqualify",
            code="missing_candidate_id",
            message="Candidate is not linked to Workable",
            config=config,
        )

    note = build_workable_reject_note(
        app=app,
        role=role,
        template=note_template if note_template is not None else config.get("auto_reject_note_template"),
        reason=reason,
        threshold_100=threshold_100,
    )
    client = WorkableService(
        access_token=org.workable_access_token,
        subdomain=org.workable_subdomain,
    )
    result = client.disqualify_candidate(
        candidate_id=candidate_id,
        member_id=str(config["actor_member_id"]),
        disqualify_reason_id=config.get("workable_disqualify_reason_id"),
        disqualify_note=note,
        withdrew=withdrew,
    )
    if not result.get("success"):
        return _build_failure_result(
            action="disqualify",
            code="api_error",
            message=sanitize_text_for_storage(str(result.get("error") or result.get("response", {}).get("error") or "Failed to disqualify candidate in Workable")) or "Failed to disqualify candidate in Workable",
            config=config,
            response=result.get("response"),
        )
    return _build_success_result(
        action="disqualify",
        message="Candidate disqualified in Workable",
        config=config,
        response=result.get("response"),
        note=note,
    )


def revert_candidate_disqualification_in_workable(
    *,
    org: Organization | None,
    app: CandidateApplication | None,
    role: Role | None = None,
) -> dict[str, Any]:
    config = resolved_workable_action_config(org, role=role)
    validation_error = _validate_writeable_org(org, config=config, action="revert")
    if validation_error is not None:
        return validation_error

    candidate_id = _candidate_id_from_app(app)
    if not candidate_id:
        return _build_failure_result(
            action="revert",
            code="missing_candidate_id",
            message="Candidate is not linked to Workable",
            config=config,
        )

    client = WorkableService(
        access_token=org.workable_access_token,
        subdomain=org.workable_subdomain,
    )
    result = client.revert_candidate_disqualification(
        candidate_id=candidate_id,
        member_id=str(config["actor_member_id"]),
    )
    if not result.get("success"):
        return _build_failure_result(
            action="revert",
            code="api_error",
            message=sanitize_text_for_storage(str(result.get("error") or result.get("response", {}).get("error") or "Failed to revert candidate in Workable")) or "Failed to revert candidate in Workable",
            config=config,
            response=result.get("response"),
        )
    return _build_success_result(
        action="revert",
        message="Candidate disqualification reverted in Workable",
        config=config,
        response=result.get("response"),
    )


def move_candidate_in_workable(
    *,
    org: Organization | None,
    candidate_id: str,
    target_stage: str,
    role: Role | None = None,
) -> dict[str, Any]:
    config = resolved_workable_action_config(org, role=role)
    validation_error = _validate_writeable_org(org, config=config, action="move")
    if validation_error is not None:
        return validation_error

    clean_candidate_id = sanitize_text_for_storage(str(candidate_id or "").strip()) or None
    clean_target_stage = sanitize_text_for_storage(str(target_stage or "").strip()) or None
    if not clean_candidate_id:
        return _build_failure_result(
            action="move",
            code="missing_candidate_id",
            message="Candidate is not linked to Workable",
            config=config,
        )
    if not clean_target_stage:
        return _build_failure_result(
            action="move",
            code="missing_target_stage",
            message="Target stage is required for Workable move",
            config=config,
        )

    client = WorkableService(
        access_token=org.workable_access_token,
        subdomain=org.workable_subdomain,
    )
    result = client.move_candidate(
        candidate_id=clean_candidate_id,
        member_id=str(config["actor_member_id"]),
        target_stage=clean_target_stage,
    )
    if not result.get("success"):
        return _build_failure_result(
            action="move",
            code="api_error",
            message=sanitize_text_for_storage(str(result.get("error") or result.get("response", {}).get("error") or "Failed to move candidate in Workable")) or "Failed to move candidate in Workable",
            config=config,
            response=result.get("response"),
        )
    return _build_success_result(
        action="move",
        message="Candidate moved in Workable",
        config=config,
        response=result.get("response"),
    )
