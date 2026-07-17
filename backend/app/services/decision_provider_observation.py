"""Primitive-only exact observations for ambiguous decision provider writes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .ats_stage_move_provider import (
    StageMoveObservationFailure,
    StageMoveObservationPlan,
    perform_stage_move_provider_observation,
    stage_move_observation_plan,
)
from .decision_provider_call import DecisionProviderPlan


class DecisionProviderObservationFailure(RuntimeError):
    """The exact remote target could not be read or classified safely."""

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True)
class DecisionProviderObservationPlan:
    operation_action: str
    provider: str
    provider_target_id: str
    expected_remote_stage: str | None
    stage_plan: StageMoveObservationPlan = field(repr=False)


def decision_provider_observation_plan(
    plan: DecisionProviderPlan,
) -> DecisionProviderObservationPlan:
    """Detach only primitives needed for one exact, read-only provider check."""

    if plan.stage_plan is not None:
        stage_plan = stage_move_observation_plan(plan.stage_plan)
    elif plan.provider == "workable" and plan.operation_action == "reject":
        stage_plan = StageMoveObservationPlan(
            provider="workable",
            provider_target_id=plan.provider_target_id,
            organization_id=int(plan.organization_id),
            workable_subdomain=plan.workable_subdomain,
            workable_access_token=plan.workable_access_token,
        )
    else:
        raise DecisionProviderObservationFailure(
            code="not_configured",
            message="No exact read-only ATS observation is available for this decision",
        )
    return DecisionProviderObservationPlan(
        operation_action=str(plan.operation_action),
        provider=str(plan.provider),
        provider_target_id=str(plan.provider_target_id),
        expected_remote_stage=(
            str(plan.provider_remote_stage or "").strip() or None
        ),
        stage_plan=stage_plan,
    )


def _normalized_values(values: object) -> set[str]:
    source = values if isinstance(values, list) else [values]
    return {
        str(value or "").strip().casefold()
        for value in source
        if str(value or "").strip()
    }


def _workable_reject_observation(
    plan: DecisionProviderObservationPlan,
) -> dict[str, Any]:
    stage = plan.stage_plan
    if not all(
        str(value or "").strip()
        for value in (
            stage.workable_access_token,
            stage.workable_subdomain,
            plan.provider_target_id,
        )
    ):
        raise DecisionProviderObservationFailure(
            code="not_configured",
            message="Workable is not configured for an exact rejection check",
        )
    from ..components.integrations.workable.service import WorkableService

    try:
        payload = WorkableService(
            access_token=str(stage.workable_access_token),
            subdomain=str(stage.workable_subdomain),
        ).get_candidate(plan.provider_target_id)
    except Exception:
        raise DecisionProviderObservationFailure(
            code="provider_read_failed",
            message="The exact Workable candidate could not be read",
        ) from None
    if (
        not isinstance(payload, dict)
        or str(payload.get("id") or "") != plan.provider_target_id
    ):
        raise DecisionProviderObservationFailure(
            code="provider_target_mismatch",
            message="Workable returned a different candidate target",
        )
    raw_stage = payload.get("stage")
    stage_values: list[str] = []
    if isinstance(raw_stage, dict):
        stage_values.extend(
            str(raw_stage.get(key) or "").strip()
            for key in ("slug", "id", "name", "kind")
        )
    elif raw_stage is not None:
        stage_values.append(str(raw_stage).strip())
    stage_values.extend(
        str(payload.get(key) or "").strip()
        for key in ("stage_slug", "stage_id", "stage_name", "stage_kind", "status")
    )
    stage_values = list(dict.fromkeys(value for value in stage_values if value))
    disqualified = payload.get("disqualified")
    normalized = _normalized_values(stage_values)
    matches = bool(disqualified is True) or bool(
        normalized & {"rejected", "disqualified", "declined"}
    )
    remote_stage = stage_values[0] if stage_values else (
        "disqualified" if disqualified is True else "unknown"
    )
    return {
        "success": True,
        "provider": "workable",
        "provider_target_id": plan.provider_target_id,
        "provider_remote_stage": remote_stage,
        "provider_remote_stage_values": stage_values,
        "provider_effect_matches": matches,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "evidence": {
            "candidate_id": plan.provider_target_id,
            "disqualified": disqualified if isinstance(disqualified, bool) else None,
            "stage_values": stage_values,
            "updated_at": str(payload.get("updated_at") or "")[:200] or None,
        },
    }


def perform_decision_provider_observation(
    plan: DecisionProviderObservationPlan,
) -> dict[str, Any]:
    """Read and classify the exact provider target without a DB session."""

    if plan.provider == "workable" and plan.operation_action == "reject":
        return _workable_reject_observation(plan)
    try:
        result = perform_stage_move_provider_observation(plan.stage_plan)
    except StageMoveObservationFailure as exc:
        raise DecisionProviderObservationFailure(
            code=exc.code, message=exc.message
        ) from None
    expected = str(plan.expected_remote_stage or "").strip().casefold()
    values = _normalized_values(result.get("provider_remote_stage_values"))
    return {
        **result,
        "provider_effect_matches": bool(expected and expected in values),
    }


__all__ = [
    "DecisionProviderObservationFailure",
    "DecisionProviderObservationPlan",
    "decision_provider_observation_plan",
    "perform_decision_provider_observation",
]
