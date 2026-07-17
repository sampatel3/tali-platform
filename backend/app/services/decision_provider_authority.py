"""Locked local authority for provider-gated decision operations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..actions.decision_execution_authority import (
    lock_decision_execution_scope,
    require_supported_override,
)
from ..models.agent_decision import AgentDecision
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import SisterRoleEvaluation
from .decision_provider_operation import (
    decision_provider_receipt,
    snapshot_from_receipt,
)
from .role_family_reject_authority import lock_current_role_families


_APPROVED_REJECT_TYPES = frozenset({"reject", "skip_assessment_reject"})


@dataclass(frozen=True)
class LockedDecisionAuthority:
    app: CandidateApplication
    candidate: Candidate
    organization: Organization
    owner_role: Role
    acting_role: Role
    decision: AgentDecision
    evaluation: SisterRoleEvaluation | None
    family_payload: dict[str, Any]
    workspace_version: int


def decision_operation_action(
    *, disposition: str, decision_type: str, override_action: str | None
) -> str | None:
    if disposition == "approved":
        if decision_type == "advance_to_interview":
            return "advance"
        if decision_type in _APPROVED_REJECT_TYPES:
            return "reject"
        return None
    action = str(override_action or "").strip()
    if action in {"advance", "skip_assessment_advance"}:
        return "advance"
    return "reject" if action == "reject" else None


def stable_json_fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _lock_candidate(db: Session, app: CandidateApplication) -> Candidate:
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == int(app.candidate_id),
            Candidate.organization_id == int(app.organization_id),
            Candidate.deleted_at.is_(None),
        )
        .populate_existing()
        .with_for_update(of=Candidate)
        .one_or_none()
    )
    if candidate is None:
        raise HTTPException(status_code=409, detail="Candidate is no longer available")
    return candidate


def _lock_related_evaluation(
    db: Session,
    *,
    app: CandidateApplication,
    acting_role: Role,
) -> SisterRoleEvaluation | None:
    if str(acting_role.role_kind or "") != ROLE_KIND_SISTER:
        return None
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.organization_id == int(app.organization_id),
            SisterRoleEvaluation.role_id == int(acting_role.id),
            SisterRoleEvaluation.source_application_id == int(app.id),
        )
        .populate_existing()
        .with_for_update(of=SisterRoleEvaluation)
        .one_or_none()
    )
    if evaluation is None:
        raise HTTPException(
            status_code=409,
            detail="Related role no longer owns this shared candidate roster",
        )
    return evaluation


def lock_decision_provider_authority(
    db: Session,
    *,
    organization_id: int,
    decision_id: int,
    expected_decision_type: str | None,
    expected_role_family: dict[str, Any] | None,
    disposition: str,
    override_action: str | None,
) -> LockedDecisionAuthority:
    reject_mode = (
        "approved_action"
        if disposition == "approved"
        else ("override" if override_action == "reject" else "none")
    )
    scope = lock_decision_execution_scope(
        db,
        organization_id=int(organization_id),
        decision_id=int(decision_id),
        expected_decision_type=expected_decision_type,
        expected_role_family=expected_role_family,
        reject_mode=reject_mode,
    )
    app, acting_role, decision = scope.application, scope.role, scope.decision
    if app is None or acting_role is None:
        raise HTTPException(
            status_code=409, detail="Decision application is unavailable"
        )
    candidate = _lock_candidate(db, app)
    organization = (
        db.query(Organization)
        .filter(Organization.id == int(organization_id))
        .populate_existing()
        .with_for_update(of=Organization)
        .one_or_none()
    )
    if organization is None:
        raise HTTPException(status_code=409, detail="Workspace is unavailable")
    families = lock_current_role_families(
        db,
        organization_id=int(organization_id),
        role_ids={int(app.role_id), int(decision.role_id)},
    )
    family = families.get(int(decision.role_id))
    if family is None or int(family.owner.id) != int(app.role_id):
        raise HTTPException(
            status_code=409,
            detail="Decision no longer belongs to this shared application",
        )
    owner_role = (
        db.query(Role)
        .filter(
            Role.id == int(family.owner.id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .populate_existing()
        .one_or_none()
    )
    if owner_role is None:
        raise HTTPException(status_code=409, detail="ATS owner role is unavailable")
    return LockedDecisionAuthority(
        app=app,
        candidate=candidate,
        organization=organization,
        owner_role=owner_role,
        acting_role=acting_role,
        decision=decision,
        evaluation=_lock_related_evaluation(db, app=app, acting_role=acting_role),
        family_payload=family.model_dump(),
        workspace_version=int(organization.agent_workspace_control_version or 1),
    )


def validate_decision_provider_preflight(
    db: Session,
    *,
    authority: LockedDecisionAuthority,
    disposition: str,
    override_action: str | None,
) -> None:
    app, role, decision = (
        authority.app,
        authority.acting_role,
        authority.decision,
    )
    if disposition == "overridden":
        require_supported_override(
            decision_id=int(decision.id),
            decision_type=str(decision.decision_type),
            override_action=override_action,
        )
    if decision.status not in {"pending", "reverted_for_feedback", "processing"}:
        raise HTTPException(
            status_code=409,
            detail=f"agent_decision {decision.id} is {decision.status}, not actionable",
        )
    from .decision_auto_execution_guard import application_action_block_reason

    block = application_action_block_reason(app)
    if block or bool(app.workable_disqualified):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "APPLICATION_NOT_ACTIONABLE",
                "message": block or "application is disqualified in the ATS",
            },
        )
    if disposition == "approved" and decision.decision_type == "advance_to_interview":
        if str(role.role_kind or "") == ROLE_KIND_SISTER:
            from .decision_role_context import related_decision_staleness

            report = related_decision_staleness(
                db,
                decision,
                authority.evaluation,
                application=app,
                role=role,
            )
            decision_is_current = not report.is_stale
            current_type = None
            stale_reasons = list(report.reasons)
        else:
            from .bulk_decision_service._shared import recompute_persisted_verdict

            current_type = recompute_persisted_verdict(db, role=role, app=app)
            decision_is_current = current_type == decision.decision_type
            stale_reasons = []
        if not decision_is_current:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ASSESSMENT_STAGE_DECISION_STALE",
                    "message": "Current role policy no longer authorizes this advance.",
                    "stored_decision_type": str(decision.decision_type),
                    "current_decision_type": current_type,
                    "stale_reasons": stale_reasons,
                },
            )


def matching_receipt_disposition(
    *,
    app: CandidateApplication,
    decision: AgentDecision,
    disposition: str,
    action: str,
    override_action: str | None,
    target_stage: str | None,
) -> tuple[object, str, dict[str, Any]] | None:
    receipt = decision_provider_receipt(app)
    if not isinstance(receipt, dict):
        return None
    if not (
        int(receipt.get("decision_id") or 0) == int(decision.id)
        and str(receipt.get("disposition") or "") == disposition
        and str(receipt.get("operation_action") or "") == action
        and str(receipt.get("expected_decision_type") or "")
        == str(decision.decision_type)
        and str(receipt.get("override_action") or "") == str(override_action or "")
        and str(receipt.get("target_stage") or "") == str(target_stage or "")
    ):
        return None
    receipt_disposition = {
        "confirmed": "confirmed_replay",
        "provider_succeeded": "finalize_provider_success",
        "provider_call_started": "reconciliation_required",
        "manual_reconciliation_required": "reconciliation_required",
    }.get(str(receipt.get("status") or "").strip().lower())
    if receipt_disposition is None:
        return None
    return snapshot_from_receipt(receipt), receipt_disposition, receipt


__all__ = [
    "LockedDecisionAuthority",
    "decision_operation_action",
    "lock_decision_provider_authority",
    "matching_receipt_disposition",
    "stable_json_fingerprint",
    "validate_decision_provider_preflight",
]
