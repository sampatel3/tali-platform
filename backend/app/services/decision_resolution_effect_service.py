"""Canonical observed effects for resolved agent decisions.

An ``AgentDecision`` records a recommendation and the recruiter's resolution;
it is not proof that the requested workflow action happened.  Completion is
grounded only in the immutable application-event ledger, matched to the same
logical role, decision, application membership, and candidate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable, Literal

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.candidate_application_event import CandidateApplicationEvent
from ..models.sister_role_evaluation import SisterRoleEvaluation
from ..domains.assessments_runtime.pipeline_event_service import (
    membership_active_at_event,
)

EffectStatus = Literal["confirmed", "failed", "pending", "unknown"]


@dataclass(frozen=True)
class DecisionResolutionEffect:
    status: EffectStatus
    action: str
    target: str | None = None
    occurred_at: datetime | None = None
    event_id: int | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def requested_action(decision: AgentDecision) -> str:
    override = str(decision.override_action or "").strip().lower()
    decision_type = str(decision.decision_type or "").strip().lower()
    basis = override if str(decision.status or "") == "overridden" and override else decision_type
    if "reject" in basis or basis == "skip_assessment_reject":
        return "reject"
    if "advance" in basis:
        return "advance"
    if "assessment" in basis or "invite" in basis or "send" in basis:
        return "assessment_send"
    return "unknown"


def _metadata_int(event: CandidateApplicationEvent, *keys: str) -> int | None:
    metadata = event.event_metadata if isinstance(event.event_metadata, dict) else {}
    for key in keys:
        try:
            value = int(metadata.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _event_action(event: CandidateApplicationEvent) -> str:
    event_type = str(event.event_type or "").strip().lower()
    to_stage = str(event.to_stage or "").strip().lower()
    to_outcome = str(event.to_outcome or "").strip().lower()
    metadata = event.event_metadata if isinstance(event.event_metadata, dict) else {}
    metadata_action = str(metadata.get("action") or "").strip().lower()
    operation_type = str(metadata.get("op_type") or "").strip().lower()
    source = str(metadata.get("source") or "").strip().lower()
    if event_type in {"assessment_invite_sent", "assessment_invite_resent"}:
        return "assessment_send"
    if event_type in {
        "pipeline_stage_changed",
        "role_pipeline_stage_changed",
        "workable_moved",
        "bullhorn_moved",
    } or to_stage == "advanced":
        return "advance"
    if event_type in {
        "application_outcome_changed",
        "role_application_outcome_changed",
        "workable_disqualified",
        "bullhorn_rejected",
    } or to_outcome == "rejected":
        return "reject"
    if metadata_action in {"move", "advance", "advanced"}:
        return "advance"
    if metadata_action in {"disqualify", "reject", "rejected"}:
        return "reject"
    if "assessment" in metadata_action or "invite" in metadata_action:
        return "assessment_send"
    if operation_type == "move_stage":
        return "advance"
    if operation_type == "manual_outcome":
        return "reject"
    if source == "reject_application" and "writeback" in event_type:
        return "reject"
    if "assessment_invite" in event_type:
        return "assessment_send"
    return "unknown"


def _effect_status(event: CandidateApplicationEvent) -> EffectStatus:
    value = str(event.effect_status or "").strip().lower()
    if value in {"confirmed", "local_confirmed", "success", "succeeded"}:
        return "confirmed"
    if value in {"failed", "error"}:
        return "failed"
    if value in {"pending", "queued", "processing", "running", "retry_wait"}:
        return "pending"
    event_type = str(event.event_type or "").strip().lower()
    if "failed" in event_type or "error" in event_type:
        return "failed"
    if event_type in {
        "pipeline_stage_changed",
        "role_pipeline_stage_changed",
        "application_outcome_changed",
        "role_application_outcome_changed",
        "workable_moved",
        "bullhorn_moved",
        "workable_disqualified",
        "bullhorn_rejected",
        "assessment_invite_sent",
        "assessment_invite_resent",
    }:
        return "confirmed"
    return "unknown"


def _authority(event: CandidateApplicationEvent, *, action: str) -> int:
    event_type = str(event.event_type or "").strip().lower()
    if action == "assessment_send" and event_type in {
        "assessment_invite_sent",
        "assessment_invite_resent",
    }:
        return 4
    if action == "advance" and event_type in {
        "pipeline_stage_changed",
        "role_pipeline_stage_changed",
    }:
        return 4
    if action == "reject" and event_type in {
        "application_outcome_changed",
        "role_application_outcome_changed",
    } and str(event.to_outcome or "").strip().lower() == "rejected":
        return 4
    if event_type in {
        "workable_moved",
        "bullhorn_moved",
        "workable_disqualified",
        "bullhorn_rejected",
    }:
        return 3
    return 2 if _effect_status(event) in {"failed", "pending"} else 1


def _event_target(event: CandidateApplicationEvent) -> str | None:
    return str(
        event.target_stage
        or event.to_stage
        or event.to_outcome
        or ""
    ).strip() or None


def _event_timestamp(event: CandidateApplicationEvent) -> float:
    value = event.created_at
    if not isinstance(value, datetime):
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def resolution_effects_for_decisions(
    db: Session,
    *,
    decisions: Iterable[AgentDecision],
    applications_by_id: dict[int, CandidateApplication],
) -> dict[int, DecisionResolutionEffect]:
    """Resolve observed effects for decisions without inferring from approval."""

    decision_list = list(decisions)
    if not decision_list:
        return {}
    decisions_by_id = {int(item.id): item for item in decision_list}
    application_ids = {int(item.application_id) for item in decision_list}
    role_ids = {int(item.role_id) for item in decision_list}

    memberships = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id.in_(sorted(role_ids)),
            or_(
                SisterRoleEvaluation.source_application_id.in_(
                    sorted(application_ids)
                ),
                SisterRoleEvaluation.ats_application_id.in_(
                    sorted(application_ids)
                ),
            ),
        )
        .all()
    )
    allowed_application_ids: dict[int, set[int]] = {
        decision_id: {int(decision.application_id)}
        for decision_id, decision in decisions_by_id.items()
    }
    for membership in memberships:
        for decision_id, decision in decisions_by_id.items():
            membership_application_ids = {
                int(membership.source_application_id),
                *(
                    [int(membership.ats_application_id)]
                    if membership.ats_application_id is not None
                    else []
                ),
            }
            if int(decision.role_id) == int(membership.role_id) and int(
                decision.application_id
            ) in membership_application_ids:
                allowed_application_ids[decision_id].add(
                    int(membership.source_application_id)
                )
                if membership.ats_application_id is not None:
                    allowed_application_ids[decision_id].add(
                        int(membership.ats_application_id)
                    )

    all_event_application_ids = sorted(
        {
            application_id
            for values in allowed_application_ids.values()
            for application_id in values
        }
    )
    event_applications = {
        int(application.id): application
        for application in db.query(CandidateApplication)
        .filter(CandidateApplication.id.in_(all_event_application_ids))
        .all()
    }
    rows = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.organization_id.in_(
                sorted({int(item.organization_id) for item in decision_list})
            ),
            CandidateApplicationEvent.application_id.in_(
                all_event_application_ids or [-1]
            ),
        )
        .order_by(
            CandidateApplicationEvent.created_at.desc(),
            CandidateApplicationEvent.id.desc(),
        )
        .all()
    )

    candidates: dict[int, list[tuple[int, CandidateApplicationEvent, str]]] = {
        decision_id: [] for decision_id in decisions_by_id
    }
    for event in rows:
        linked_id = (
            int(event.agent_decision_id)
            if event.agent_decision_id is not None
            else _metadata_int(event, "agent_decision_id", "decision_id")
        )
        decision = decisions_by_id.get(linked_id or -1)
        if decision is None:
            continue
        if int(event.application_id) not in allowed_application_ids[int(decision.id)]:
            continue
        decision_app = applications_by_id.get(int(decision.application_id))
        event_app = event_applications.get(int(event.application_id))
        if (
            decision_app is None
            or event_app is None
            or int(decision_app.candidate_id) != int(event_app.candidate_id)
        ):
            continue
        event_role_id = (
            int(event.role_id)
            if event.role_id is not None
            else _metadata_int(event, "acting_role_id", "role_id")
        )
        if event_role_id is None:
            if int(decision_app.role_id) == int(decision.role_id):
                event_role_id = int(decision.role_id)
            elif any(
                int(membership.role_id) == int(decision.role_id)
                and int(event.application_id)
                in {
                    int(membership.source_application_id),
                    *(
                        [int(membership.ats_application_id)]
                        if membership.ats_application_id is not None
                        else []
                    ),
                }
                and membership_active_at_event(membership, event.created_at)
                for membership in memberships
            ):
                event_role_id = int(decision.role_id)
        if event_role_id != int(decision.role_id):
            continue
        expected = requested_action(decision)
        action = _event_action(event)
        if action == "unknown" or (expected != "unknown" and action != expected):
            continue
        if _effect_status(event) == "unknown" and _authority(event, action=action) <= 1:
            continue
        candidates[int(decision.id)].append(
            (_authority(event, action=action), event, action)
        )

    output: dict[int, DecisionResolutionEffect] = {}
    for decision_id, decision in decisions_by_id.items():
        matches = candidates[decision_id]
        if matches:
            matches.sort(
                key=lambda item: (
                    item[0],
                    _event_timestamp(item[1]),
                    int(item[1].id),
                ),
                reverse=True,
            )
            _, event, action = matches[0]
            output[decision_id] = DecisionResolutionEffect(
                status=_effect_status(event),
                action=action,
                target=_event_target(event),
                occurred_at=event.created_at,
                event_id=int(event.id),
            )
            continue
        output[decision_id] = DecisionResolutionEffect(
            status=(
                "pending"
                if str(decision.status or "").strip().lower() == "processing"
                else "unknown"
            ),
            action=requested_action(decision),
        )
    return output


__all__ = [
    "DecisionResolutionEffect",
    "EffectStatus",
    "requested_action",
    "resolution_effects_for_decisions",
]
