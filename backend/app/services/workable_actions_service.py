from __future__ import annotations

import contextlib
import contextvars
from typing import Any

from ..components.integrations.workable.service import WorkableService
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from .ats_note_policy import (
    build_workable_reject_note,
    contains_assessment_lifecycle_content,
    render_workable_note_template,
)
from .document_service import sanitize_text_for_storage

WORKABLE_ALLOWED_SCOPES = ("r_jobs", "r_candidates", "w_candidates")
WORKABLE_WRITE_SCOPE = "w_candidates"


class WorkableWritebackError(Exception):
    """Raised by a Workable write helper when ``strict_workable_writes`` is
    active and the write fails.

    Callers that normally *swallow* a Workable failure (record an event, return
    a falsy result, fall back to email) must re-raise this so the failure can
    propagate to the decision-dispatch task, which gates the local commit on
    the Workable write and re-queues the decision on failure. ``retriable`` is
    True for transient API errors (429/5xx surface as ``code=="api_error"``);
    config/linkage failures are non-retriable.
    """

    def __init__(self, *, action: str, code: str, message: str, retriable: bool):
        super().__init__(f"{action} failed ({code}): {message}")
        self.action = action
        self.code = code
        self.message = message
        self.retriable = retriable


# When set, Workable write helpers raise ``WorkableWritebackError`` instead of
# returning a failure dict. The decision-dispatch task turns this on so a
# failed disqualify/move aborts the transaction (nothing half-applied) and
# re-queues the decision, rather than silently committing a Tali-only change.
_STRICT_WORKABLE_WRITES: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "strict_workable_writes", default=False
)


@contextlib.contextmanager
def strict_workable_writes():
    token = _STRICT_WORKABLE_WRITES.set(True)
    try:
        yield
    finally:
        _STRICT_WORKABLE_WRITES.reset(token)

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
        bool(config.get("workable_writeback"))
        or bool(config.get("auto_reject_enabled"))
    ):
        inferred.append(WORKABLE_WRITE_SCOPE)
    return inferred


def workable_has_scope(org: Organization | None, scope: str) -> bool:
    return scope in workable_granted_scopes(org)


def workable_can_write_candidates(org: Organization | None) -> bool:
    return workable_has_scope(org, WORKABLE_WRITE_SCOPE)


def workable_writeback_enabled(org: "Organization | None") -> bool:
    """True when Taali writes candidate activity back to Workable. Reads the
    ``workable_writeback`` bool; falls back to scope-derived capability when
    absent (pre-migration) so behavior is preserved."""
    if org is None or not getattr(org, "workable_connected", False):
        return False
    config = org.workable_config if isinstance(org.workable_config, dict) else {}
    if "workable_writeback" in config:
        return bool(config.get("workable_writeback"))
    return workable_can_write_candidates(org)  # legacy fallback (scope-derived)


def resolve_workable_interview_stage(
    org: "Organization | None", role: "Role | None"
) -> tuple[str | None, str | None]:
    """Resolve the safe Workable target for an autonomous interview hand-off.

    An explicit workspace setting remains authoritative.  When it is absent,
    use the role's cached pipeline only if it contains exactly one
    ``kind=interview`` stage.  Workable supplies that semantic ``kind`` itself,
    so selecting the sole match is deterministic; zero or multiple matches are
    intentionally returned as an actionable ambiguity instead of guessing.

    Returns ``(target_stage, error_detail)``.  ``target_stage`` prefers the
    stage slug accepted by Workable's move endpoint, then its id/name for older
    payload shapes.
    """
    config = (
        org.workable_config
        if org is not None and isinstance(org.workable_config, dict)
        else {}
    )
    configured = sanitize_text_for_storage(
        str(config.get("interview_stage_name") or "").strip()
    )
    if configured:
        return configured, None

    raw_stages = getattr(role, "workable_stages", None) if role is not None else None
    stages = raw_stages if isinstance(raw_stages, list) else []
    candidates = [
        stage
        for stage in stages
        if isinstance(stage, dict)
        and str(stage.get("kind") or "").strip().lower() == "interview"
    ]
    if len(candidates) != 1:
        if not candidates:
            return None, (
                "No cached Workable stage has kind=interview. Refresh this "
                "role's Workable stages or choose the interview hand-off stage "
                "in Agent settings."
            )
        labels = [
            str(
                stage.get("name")
                or stage.get("slug")
                or stage.get("id")
                or "unnamed stage"
            ).strip()
            for stage in candidates
        ]
        return None, (
            "Multiple cached Workable stages have kind=interview "
            f"({', '.join(labels)}). Choose the intended hand-off stage in "
            "Agent settings."
        )

    stage = candidates[0]
    target = sanitize_text_for_storage(
        str(
            stage.get("slug")
            or stage.get("id")
            or stage.get("name")
            or ""
        ).strip()
    )
    if not target:
        return None, (
            "The sole cached Workable interview stage has no slug, id, or "
            "name. Refresh this role's Workable stages or choose the hand-off "
            "stage in Agent settings."
        )
    return target, None


def resolve_workable_invite_stage(
    org: "Organization | None", role: "Role | None"
) -> tuple[str | None, str | None]:
    """Resolve the deterministic Workable assessment/invited target.

    Workspace configuration is authoritative. Without it, the only safe
    fallback is exactly one cached stage whose Workable semantic kind is
    ``assessment``; zero or multiple matches require recruiter mapping.
    """
    config = (
        org.workable_config
        if org is not None and isinstance(org.workable_config, dict)
        else {}
    )
    configured = sanitize_text_for_storage(
        str(config.get("invite_stage_name") or "").strip()
    )
    if configured:
        return configured, None

    raw_stages = getattr(role, "workable_stages", None) if role is not None else None
    stages = raw_stages if isinstance(raw_stages, list) else []
    candidates = [
        stage
        for stage in stages
        if isinstance(stage, dict)
        and str(stage.get("kind") or "").strip().lower() == "assessment"
    ]
    if len(candidates) != 1:
        if not candidates:
            return None, (
                "No cached Workable stage has kind=assessment. Refresh this "
                "role's Workable stages or choose the assessment/invited stage "
                "in Agent settings."
            )
        labels = [
            str(
                stage.get("name")
                or stage.get("slug")
                or stage.get("id")
                or "unnamed stage"
            ).strip()
            for stage in candidates
        ]
        return None, (
            "Multiple cached Workable stages have kind=assessment "
            f"({', '.join(labels)}). Choose the intended assessment/invited "
            "stage in Agent settings."
        )

    stage = candidates[0]
    target = sanitize_text_for_storage(
        str(
            stage.get("slug")
            or stage.get("id")
            or stage.get("name")
            or ""
        ).strip()
    )
    if not target:
        return None, (
            "The sole cached Workable assessment stage has no slug, id, or "
            "name. Refresh this role's stages or choose the target in Agent "
            "settings."
        )
    return target, None


def resolved_workable_action_config(org: Organization | None, role: Role | None = None) -> dict[str, Any]:
    # Per-role overrides for ``workable_disqualify_reason_id`` and
    # ``auto_reject_note_template`` were dropped in alembic 076 — both
    # now live solely on ``org.workable_config``. ``actor_member_id``
    # keeps its per-role override (set on the role page) and falls back
    # to the org value.
    org_config = org.workable_config if org and isinstance(org.workable_config, dict) else {}
    actor_member_id = sanitize_text_for_storage(
        str(
            (role.workable_actor_member_id if role and role.workable_actor_member_id else None)
            or org_config.get("workable_actor_member_id")
            or ""
        ).strip()
    ) or None
    disqualify_reason_id = sanitize_text_for_storage(
        str(org_config.get("workable_disqualify_reason_id") or "").strip()
    ) or None
    note_template = sanitize_text_for_storage(
        str(org_config.get("auto_reject_note_template") or "").strip()
    ) or None
    scopes = workable_granted_scopes(org)
    return {
        "granted_scopes": scopes,
        "has_write_scope": WORKABLE_WRITE_SCOPE in scopes,
        "actor_member_id": actor_member_id,
        "workable_disqualify_reason_id": disqualify_reason_id,
        "auto_reject_note_template": note_template,
    }


def resolve_workable_actor_member_id(org: Organization | None, role: Role | None = None) -> str | None:
    """Member id Workable attributes write-backs to (per-role override → org).

    Structured movement summaries and stage changes require this; it is None
    when the org never configured ``workable_actor_member_id``.
    """
    return resolved_workable_action_config(org, role=role).get("actor_member_id")


# Workable refuses candidate write-backs (disqualify, stage move) on reqs that
# aren't live: archived/closed jobs return 403, drafts aren't actionable. When a
# role's linked job is in one of these states we skip the Workable round-trip and
# act locally (e.g. reject in Taali only) instead of 403-looping forever.
WORKABLE_NON_LIVE_JOB_STATES = frozenset({"archived", "closed", "draft"})


def workable_job_state(role: Role | None) -> str | None:
    """The cached Workable job ``state`` for a role's linked job, lowercased.

    ``None`` for manual/Taali-created roles or when the job hasn't been synced.
    """
    data = getattr(role, "workable_job_data", None) if role is not None else None
    if isinstance(data, dict):
        return str(data.get("state") or "").strip().lower() or None
    return None


def workable_job_syncable(role: Role | None) -> bool:
    """False when the role's linked Workable job is archived/closed/draft.

    Those reqs reject candidate write-backs (disqualify/move) with a 403, so
    callers should skip the Workable round-trip and act locally instead. A role
    with no linked job, or a published job, is syncable.
    """
    return workable_job_state(role) not in WORKABLE_NON_LIVE_JOB_STATES


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
    if _STRICT_WORKABLE_WRITES.get():
        # api_error covers transient 429/5xx (Workable returns these as a
        # failure dict, not an exception) — retriable. Config/linkage codes
        # (not_writeable, missing_candidate_id, missing actor) won't fix
        # themselves — non-retriable.
        raise WorkableWritebackError(
            action=action,
            code=code,
            message=message,
            retriable=(code == "api_error"),
        )
    return {
        "success": False,
        "action": action,
        "code": code,
        "message": message,
        "config": config,
        "response": response or {},
    }


def _build_skipped_result(*, action: str, config: dict, message: str) -> dict:
    # Deliberate no-op (read-only mode). Never raises, even under strict
    # writes — the caller resolves the decision locally.
    return {"success": False, "skipped": True, "action": action,
            "code": "writeback_disabled", "message": message, "config": config, "response": {}}


def _readonly_skip(org: "Organization | None", action: str, *, role: "Role | None" = None) -> dict | None:
    """Benign skipped-result when write-back is off; None when writes are on."""
    if workable_writeback_enabled(org):
        return None
    return _build_skipped_result(
        action=action,
        config=resolved_workable_action_config(org, role=role),
        message="Workable write-back is off (read-only mode)",
    )


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
    if (skip := _readonly_skip(org, "disqualify", role=role)) is not None:
        return skip
    config = resolved_workable_action_config(org, role=role)
    if bool(getattr(app, "workable_disqualified", False)):
        return {
            "success": True,
            "skipped": True,
            "action": "disqualify",
            "code": "already_disqualified",
            "message": "Candidate is already disqualified in Workable",
            "config": {**config, "movement_performed": False},
            "response": {},
        }
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
        # The organization template is an automatic pre-screen policy. Only
        # that caller passes it explicitly; manual and non-score rejections keep
        # their own reason instead of being silently overwritten here.
        template=note_template,
        reason=reason,
        threshold_100=threshold_100,
    )
    trusted_role_name = str(getattr(role, "name", None) or "").strip()
    if contains_assessment_lifecycle_content(
        note,
        trusted_role_values=(trusted_role_name,) if trusted_role_name else None,
    ):
        # The rejection movement may still proceed, but assessment lifecycle
        # details belong in Taali and must not become a Workable disqualify note.
        note = None
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
    if (skip := _readonly_skip(org, "revert", role=role)) is not None:
        return skip
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
    if (skip := _readonly_skip(org, "move", role=role)) is not None:
        return skip
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
