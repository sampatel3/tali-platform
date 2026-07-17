"""Non-secret authority snapshots for durable ATS stage-move dispatch."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..models.candidate_application import CandidateApplication
    from ..models.role import Role
    from ..models.sister_role_evaluation import SisterRoleEvaluation
    from .ats_stage_move_receipt import StageMoveSnapshot


def queued_stage_move_authority_failure(
    payload: dict, snapshot: "StageMoveSnapshot"
) -> tuple[str, str] | None:
    """Require and compare the complete authority frozen by the producer."""

    if int(payload.get("authority_snapshot_version") or 0) != 1:
        return (
            "authority_snapshot_required",
            "The queued ATS move lacks the exact application and role authority snapshot; queue it again safely",
        )
    exact_values = {
        "expected_application_version": snapshot.expected_application_version,
        "expected_application_outcome": snapshot.expected_application_outcome,
        "expected_pipeline_stage": snapshot.expected_pipeline_stage,
        "expected_workable_disqualified": snapshot.expected_workable_disqualified,
        "expected_candidate_id": snapshot.expected_candidate_id,
        "expected_owner_role_id": snapshot.expected_owner_role_id,
        "expected_owner_role_version": snapshot.expected_owner_role_version,
        "expected_owner_external_job_id": snapshot.owner_external_job_id,
        "expected_acting_role_id": snapshot.acting_role_id,
        "expected_acting_role_version": snapshot.expected_acting_role_version,
        "expected_related_evaluation_id": snapshot.related_evaluation_id,
        "expected_related_evaluation_status": snapshot.related_evaluation_status,
        "expected_related_pipeline_stage": snapshot.related_pipeline_stage,
        "expected_related_spec_fingerprint": snapshot.related_spec_fingerprint,
        "expected_provider": snapshot.provider,
        "expected_provider_target_id": snapshot.provider_target_id,
        "expected_target_intent": snapshot.target_intent,
    }
    if any(key not in payload for key in exact_values):
        return (
            "authority_snapshot_incomplete",
            "The queued ATS move is missing part of its authority snapshot",
        )
    integer_keys = {
        "expected_application_version",
        "expected_candidate_id",
        "expected_owner_role_id",
        "expected_owner_role_version",
        "expected_acting_role_id",
        "expected_acting_role_version",
        "expected_related_evaluation_id",
    }
    for key, current in exact_values.items():
        queued = payload.get(key)
        if key in integer_keys:
            equal = queued is None and current is None
            if queued is not None and current is not None:
                equal = int(queued) == int(current)
        elif key == "expected_workable_disqualified":
            equal = bool(queued) == bool(current)
        else:
            equal = str(queued or "") == str(current or "")
        if not equal:
            return (
                "queued_authority_changed",
                "The application, provider, owner, or related-role roster changed before the ATS move began",
            )
    return None


def build_stage_move_dispatch_payload(
    *,
    app: "CandidateApplication",
    provider: str,
    target_stage: str,
    owner_role: "Role | None" = None,
    acting_role: "Role | None" = None,
    related_evaluation: "SisterRoleEvaluation | None" = None,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """Freeze every local/provider authority field confirmed by the caller."""

    owner = owner_role or app.role
    if owner is None:
        raise ValueError("ATS stage move has no owning role")
    provider_name = str(provider or "").strip().lower()
    if provider_name not in {"workable", "bullhorn"}:
        raise ValueError("ATS stage move provider must be explicit")
    if (acting_role is None) != (related_evaluation is None):
        raise ValueError("Related-role authority snapshot is incomplete")
    target = str(target_stage or "").strip()
    if not target:
        raise ValueError("ATS stage move target cannot be blank")
    provider_target = str(
        (
            app.bullhorn_job_submission_id
            if provider_name == "bullhorn"
            else app.workable_candidate_id
        )
        or ""
    ).strip()
    if not provider_target:
        raise ValueError("ATS stage move has no exact provider target")
    authority = {
        "authority_snapshot_version": 1,
        "expected_application_version": int(app.version or 1),
        "expected_application_outcome": str(app.application_outcome or "open").lower(),
        "expected_pipeline_stage": str(app.pipeline_stage or "applied").lower(),
        "expected_workable_disqualified": bool(app.workable_disqualified),
        "expected_candidate_id": int(app.candidate_id),
        "expected_owner_role_id": int(app.role_id),
        "expected_owner_role_version": int(owner.version or 1),
        "expected_owner_external_job_id": str(
            (
                owner.bullhorn_job_order_id
                if provider_name == "bullhorn"
                else owner.workable_job_id
            )
            or ""
        ).strip()
        or None,
        "expected_acting_role_id": (
            int(acting_role.id) if acting_role is not None else None
        ),
        "expected_acting_role_version": (
            int(acting_role.version or 1) if acting_role is not None else None
        ),
        "expected_related_evaluation_id": (
            int(related_evaluation.id) if related_evaluation is not None else None
        ),
        "expected_related_evaluation_status": (
            str(related_evaluation.status or "")
            if related_evaluation is not None
            else None
        ),
        "expected_related_pipeline_stage": (
            str(related_evaluation.pipeline_stage or "applied")
            if related_evaluation is not None
            else None
        ),
        "expected_related_spec_fingerprint": (
            str(related_evaluation.spec_fingerprint or "")
            if related_evaluation is not None
            else None
        ),
        "expected_provider": provider_name,
        "expected_provider_target_id": provider_target,
        "expected_target_intent": target.lower(),
    }
    base = {
        "application_id": int(app.id),
        "provider": provider_name,
        "provider_target_id": provider_target,
        "target_stage": target,
        "target_intent": target.lower() if provider_name == "bullhorn" else None,
        "acting_role_id": int(acting_role.id) if acting_role is not None else None,
        **authority,
    }
    if not authority["expected_owner_external_job_id"]:
        raise ValueError("ATS stage move has no exact owner job target")
    if operation_id:
        base["operation_id"] = str(operation_id)[:200]
        return base
    digest = hashlib.sha256(
        json.dumps(base, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    base["operation_id"] = f"stage-move:{int(app.id)}:{digest[:40]}"
    return base


__all__ = [
    "build_stage_move_dispatch_payload",
    "queued_stage_move_authority_failure",
]
