"""Role-local candidate mutations for independent related roles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..candidate_search.population import lock_live_candidate_for_execution
from ..domains.assessments_runtime.pipeline_event_service import (
    existing_idempotent_event,
)
from ..domains.assessments_runtime.pipeline_service import append_application_event
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.sister_role_evaluation import SisterRoleEvaluation
from .pre_screen_decision_emitter import discard_pending_decisions_for_app
from .sister_role_service import (
    related_role_action_restrictions,
    transition_related_role_outcome,
    transition_related_role_stage,
)


@dataclass(frozen=True)
class RelatedRoleActionState:
    role: Role
    evaluation: SisterRoleEvaluation
    changed: bool


@dataclass(frozen=True)
class RelatedRoleAtsActionContext:
    """The role-owned candidate row and its optional shared ATS transport.

    ``source_application`` is the application represented in the related
    role. ``ats_application`` is only the external-write transport. Keeping
    both explicit prevents a confirmed ATS write from mutating the original
    role's local funnel state.
    """

    role: Role
    evaluation: SisterRoleEvaluation
    source_application: CandidateApplication
    ats_application: CandidateApplication


class RelatedRoleActionContractError(RuntimeError):
    """A queued action no longer matches an explicit role membership."""


def _decision_id(metadata: dict[str, Any] | None) -> int | None:
    for key in ("agent_decision_id", "decision_id"):
        value = (metadata or {}).get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def lock_related_role_membership(
    db: Session,
    *,
    application: CandidateApplication,
    acting_role_id: int | None,
    for_update: bool = True,
) -> tuple[Role, SisterRoleEvaluation] | None:
    """Resolve an acting related role without inferring membership from ATS."""

    if acting_role_id is None:
        return None
    role = (
        db.query(Role)
        .filter(
            Role.id == int(acting_role_id),
            Role.organization_id == int(application.organization_id),
            Role.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if role is None:
        return None
    from .logical_role_batch_operations import is_related_role

    if not is_related_role(role):
        return None
    membership_query = db.query(SisterRoleEvaluation).filter(
        SisterRoleEvaluation.organization_id == int(application.organization_id),
        SisterRoleEvaluation.role_id == int(role.id),
        SisterRoleEvaluation.source_application_id == int(application.id),
        SisterRoleEvaluation.deleted_at.is_(None),
    )
    if (
        for_update
        and db.bind is not None
        and db.bind.dialect.name == "postgresql"
    ):
        membership_query = membership_query.with_for_update()
    evaluation = membership_query.one_or_none()
    if evaluation is None:
        raise HTTPException(
            status_code=404,
            detail="Candidate is not a member of this related role",
        )
    return role, evaluation


def resolve_related_role_ats_action_context(
    db: Session,
    *,
    organization_id: int,
    ats_application: CandidateApplication,
    acting_role_id: int | None,
    source_application_id: int | None,
    for_update: bool = True,
) -> RelatedRoleAtsActionContext | None:
    """Resolve a queued related-role ATS action without inferring membership.

    Queue payloads carry both identities: the application whose ATS linkage is
    written and the application that is a member of the logical role. Legacy
    payloads may omit ``source_application_id`` only when both are the same
    row. Any mismatch fails closed instead of falling through to an owner-role
    transition.
    """

    if acting_role_id is None:
        return None
    if (
        lock_live_candidate_for_execution(
            db,
            organization_id=int(organization_id),
            candidate_id=int(ats_application.candidate_id),
        )
        is None
    ):
        raise RelatedRoleActionContractError(
            "Related-role candidate is unavailable"
        )
    source_id = int(source_application_id or ats_application.id)
    source_application = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == source_id,
            CandidateApplication.organization_id == int(organization_id),
        )
        .one_or_none()
    )
    if source_application is None:
        raise RelatedRoleActionContractError(
            "Related-role source application is unavailable"
        )
    try:
        locked = lock_related_role_membership(
            db,
            application=source_application,
            acting_role_id=int(acting_role_id),
            for_update=for_update,
        )
    except HTTPException as exc:
        raise RelatedRoleActionContractError(
            "Candidate is no longer a member of the acting related role"
        ) from exc
    if locked is None:
        raise RelatedRoleActionContractError(
            "Related-role action requires an explicit membership"
        )
    role, evaluation = locked
    owner_role_id = int(role.ats_owner_role_id or 0)
    owner_role = (
        db.query(Role)
        .filter(
            Role.id == owner_role_id,
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .one_or_none()
        if owner_role_id
        else None
    )
    if owner_role is None:
        raise RelatedRoleActionContractError(
            "Related-role ATS owner is unavailable"
        )
    if getattr(ats_application, "deleted_at", None) is not None:
        raise RelatedRoleActionContractError(
            "Related-role ATS application is deleted"
        )
    if int(ats_application.role_id or 0) != owner_role_id:
        raise RelatedRoleActionContractError(
            "Related-role ATS application belongs to the wrong owner role"
        )
    linked_ats_id = evaluation.ats_application_id
    if linked_ats_id is None:
        # Mixed-version fallback for pre-migration memberships. It is safe only
        # when the role-owned row is itself the ATS owner's application.
        if not (
            int(source_application.id) == int(ats_application.id)
            and int(source_application.role_id or 0)
            == int(role.ats_owner_role_id or 0)
        ):
            raise RelatedRoleActionContractError(
                "Related-role membership has no writable ATS link"
            )
    elif int(linked_ats_id) != int(ats_application.id):
        raise RelatedRoleActionContractError(
            "Queued ATS application does not match the role membership"
        )
    if int(source_application.candidate_id) != int(ats_application.candidate_id):
        raise RelatedRoleActionContractError(
            "Related-role and ATS applications belong to different candidates"
        )
    return RelatedRoleAtsActionContext(
        role=role,
        evaluation=evaluation,
        source_application=source_application,
        ats_application=ats_application,
    )


def related_role_ats_action_state(
    context: RelatedRoleAtsActionContext,
) -> dict[str, Any]:
    """Return role-local blockers and shared-ATS restrictions separately."""

    outcome = str(
        context.evaluation.application_outcome or "open"
    ).strip().lower()
    stage = str(context.evaluation.pipeline_stage or "applied").strip().lower()
    local_codes: list[str] = []
    if outcome != "open":
        local_codes.append(f"role_application_outcome_{outcome}")
    if stage == "advanced":
        local_codes.append("role_pipeline_stage_advanced")
    restrictions = related_role_action_restrictions(
        role=context.role,
        evaluation=context.evaluation,
        source_application=context.source_application,
    )
    restriction_codes = [str(code) for code in restrictions.get("codes") or []]
    return {
        "local_codes": local_codes,
        "restriction_codes": restriction_codes,
        "hard_restriction_codes": [
            code for code in restriction_codes if code != "shared_ats_post_handover"
        ],
        "post_handover": "shared_ats_post_handover" in restriction_codes,
        "can_advance_in_ats": bool(restrictions.get("can_advance_in_ats")),
    }


def _existing_idempotent_action(
    db: Session,
    *,
    application_id: int,
    role_id: int,
    idempotency_key: str | None,
) -> bool:
    return (
        existing_idempotent_event(
            db,
            application_id=int(application_id),
            role_id=int(role_id),
            idempotency_key=idempotency_key,
        )
        is not None
    )


def transition_related_role_stage_action(
    db: Session,
    *,
    application: CandidateApplication,
    acting_role_id: int | None,
    to_stage: str,
    source: str,
    actor_type: str,
    actor_id: int | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    expected_version: int | None = None,
) -> RelatedRoleActionState | None:
    locked = lock_related_role_membership(
        db,
        application=application,
        acting_role_id=acting_role_id,
    )
    if locked is None:
        return None
    role, evaluation = locked
    if _existing_idempotent_action(
        db,
        application_id=int(application.id),
        role_id=int(role.id),
        idempotency_key=idempotency_key,
    ):
        return RelatedRoleActionState(role, evaluation, False)
    if (
        expected_version is not None
        and int(expected_version) != int(evaluation.version or 1)
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Version mismatch: expected={int(expected_version)}, "
                f"current={int(evaluation.version or 1)}"
            ),
        )
    if str(evaluation.application_outcome or "open").strip().lower() != "open":
        raise HTTPException(
            status_code=409,
            detail="This candidate has already left this role's active flow",
        )
    previous = str(evaluation.pipeline_stage or "applied").strip().lower()
    target = str(to_stage or "").strip().lower()
    if previous == target:
        return RelatedRoleActionState(role, evaluation, False)
    try:
        transition_related_role_stage(evaluation, to_stage=target, source=source)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    changed = str(evaluation.pipeline_stage or "").strip().lower() != previous
    if not changed:
        return RelatedRoleActionState(role, evaluation, False)
    evaluation.version = int(evaluation.version or 1) + 1
    event_metadata = {**(metadata or {}), "acting_role_id": int(role.id)}
    append_application_event(
        db,
        app=application,
        role_id=int(role.id),
        agent_decision_id=_decision_id(metadata),
        event_type="role_pipeline_stage_changed",
        actor_type=actor_type,
        actor_id=actor_id,
        from_stage=previous,
        to_stage=target,
        from_outcome=str(evaluation.application_outcome or "open"),
        to_outcome=str(evaluation.application_outcome or "open"),
        target_stage=target,
        effect_status="confirmed",
        reason=reason or "Related-role stage updated",
        metadata=event_metadata,
        idempotency_key=idempotency_key,
    )
    if target == "advanced":
        discard_pending_decisions_for_app(
            db,
            application_id=int(application.id),
            role_id=int(role.id),
            reason="superseded: candidate advanced in this role",
            include_processing=True,
        )
    return RelatedRoleActionState(role, evaluation, True)


def transition_related_role_outcome_action(
    db: Session,
    *,
    application: CandidateApplication,
    acting_role_id: int | None,
    to_outcome: str,
    source: str,
    actor_type: str,
    actor_id: int | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    expected_version: int | None = None,
) -> RelatedRoleActionState | None:
    locked = lock_related_role_membership(
        db,
        application=application,
        acting_role_id=acting_role_id,
    )
    if locked is None:
        return None
    role, evaluation = locked
    if _existing_idempotent_action(
        db,
        application_id=int(application.id),
        role_id=int(role.id),
        idempotency_key=idempotency_key,
    ):
        return RelatedRoleActionState(role, evaluation, False)
    if (
        expected_version is not None
        and int(expected_version) != int(evaluation.version or 1)
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Version mismatch: expected={int(expected_version)}, "
                f"current={int(evaluation.version or 1)}"
            ),
        )
    previous = str(evaluation.application_outcome or "open").strip().lower()
    target = str(to_outcome or "").strip().lower()
    if previous == target:
        return RelatedRoleActionState(role, evaluation, False)
    try:
        transition_related_role_outcome(
            evaluation,
            to_outcome=target,
            source=source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    evaluation.version = int(evaluation.version or 1) + 1
    event_metadata = {**(metadata or {}), "acting_role_id": int(role.id)}
    append_application_event(
        db,
        app=application,
        role_id=int(role.id),
        agent_decision_id=_decision_id(metadata),
        event_type="role_application_outcome_changed",
        actor_type=actor_type,
        actor_id=actor_id,
        from_stage=str(evaluation.pipeline_stage or "applied"),
        to_stage=str(evaluation.pipeline_stage or "applied"),
        from_outcome=previous,
        to_outcome=target,
        target_stage=target,
        effect_status="confirmed",
        reason=reason or "Related-role outcome updated",
        metadata=event_metadata,
        idempotency_key=idempotency_key,
    )
    if target != "open":
        discard_pending_decisions_for_app(
            db,
            application_id=int(application.id),
            role_id=int(role.id),
            reason=f"superseded: candidate closed in this role ({target})",
            include_processing=True,
        )
    return RelatedRoleActionState(role, evaluation, True)


__all__ = [
    "RelatedRoleAtsActionContext",
    "RelatedRoleActionContractError",
    "RelatedRoleActionState",
    "lock_related_role_membership",
    "resolve_related_role_ats_action_context",
    "related_role_ats_action_state",
    "transition_related_role_outcome_action",
    "transition_related_role_stage_action",
]
