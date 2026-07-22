"""Hard admission for one routed physical provider attempt.

Admission belongs at the provider-adapter boundary: feature workflows should
not each invent their own hold ordering.  A durable credit reservation is
created before route-attempt telemetry, then marked as provider-started only
after that telemetry exists and immediately before transport execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...services.provider_usage_admission import (
    mark_provider_attempt_started,
    release_provider_usage,
    release_provider_usage_if_definitely_nonbillable,
    reserve_provider_usage,
    with_credit_reservation,
)
from ...services.pricing_service import credits_charged
from .anthropic_estimation import (
    AnthropicRequestEstimate,
    conservative_raw_cost_micro_usd,
)
from .execution import RouteExecution
from .execution_types import AdmittedAttemptBudget, PlannedPhysicalAttempt


class ProviderAttemptAdmissionError(RuntimeError):
    """A provider attempt was denied before any provider work began."""

    provider_not_called = True


@dataclass(frozen=True, slots=True)
class AttemptAdmission:
    """One reservation and its pre-provider lifecycle operations."""

    metering: dict[str, Any]
    admitted_budget: AdmittedAttemptBudget

    @property
    def reservation(self) -> Any:
        return self.metering.get("credit_reservation")

    @property
    def external_ref(self) -> str:
        return self.admitted_budget.credit_reservation_ref

    def release_unstarted(self, *, reason: str) -> None:
        # Whether feature code supplied the hold or this adapter created it,
        # the reservation is dedicated to this one physical attempt. Route
        # telemetry failed before provider start, so retaining it would leak
        # capacity without protecting any possible spend.
        release_provider_usage(self.reservation, reason=reason)

    def release_before_transport(self, *, reason: str) -> None:
        """Release even a started marker when this adapter never called transport."""

        release_provider_usage(
            self.reservation,
            reason=reason,
            allow_started=True,
        )

    def mark_provider_started(self, *, provider: str, attempt_ref: str) -> None:
        if mark_provider_attempt_started(
            self.reservation,
            provider=provider,
            attempt_ref=attempt_ref,
        ):
            return
        # Do not release here: a false result may mean this hold is already
        # bound to an older, outcome-ambiguous attempt in another process.
        raise ProviderAttemptAdmissionError(
            "provider work denied because its durable attempt marker failed"
        )

    def release_if_definitely_nonbillable(self, error: BaseException) -> bool:
        """Release only an explicit provider rejection before billable work."""

        return release_provider_usage_if_definitely_nonbillable(
            self.reservation,
            error=error,
            reason="routed_provider_explicit_rejection",
        )


def admit_attempt(
    execution: RouteExecution,
    plan: PlannedPhysicalAttempt,
    metering: dict[str, Any] | None,
    *,
    request_estimate: AnthropicRequestEstimate,
) -> AttemptAdmission:
    """Return metering with exactly one durable reservation for this attempt."""

    meter = dict(metering or {})
    if meter.get("skip"):
        raise ProviderAttemptAdmissionError(
            "routed provider attempts cannot bypass usage admission"
        )
    attribution = execution.attribution
    organization_id = attribution.organization_id
    if organization_id is None:
        raise ProviderAttemptAdmissionError(
            "routed provider attempts require organization attribution"
        )
    supplied_org_id = meter.get("organization_id")
    if supplied_org_id is not None and int(supplied_org_id) != int(organization_id):
        raise ProviderAttemptAdmissionError(
            "metering organization does not match route attribution"
        )

    role_id = attribution.role_id
    supplied_role_id = meter.get("role_id")
    normalized_supplied_role = (
        int(supplied_role_id) if supplied_role_id is not None else None
    )
    if supplied_role_id is not None and normalized_supplied_role != role_id:
        raise ProviderAttemptAdmissionError(
            "metering role does not match route attribution"
        )

    user_id = attribution.user_id
    supplied_user_id = meter.get("user_id")
    normalized_supplied_user = (
        int(supplied_user_id) if supplied_user_id is not None else None
    )
    if supplied_user_id is not None and normalized_supplied_user != user_id:
        raise ProviderAttemptAdmissionError(
            "metering user does not match route attribution"
        )

    entity_id = attribution.entity_id
    supplied_entity_id = meter.get("entity_id")
    if supplied_entity_id is not None and str(supplied_entity_id) != entity_id:
        raise ProviderAttemptAdmissionError(
            "metering entity does not match route attribution"
        )

    feature = execution.decision.feature
    supplied_feature = meter.get("feature")
    supplied_feature_value = getattr(supplied_feature, "value", supplied_feature)
    if supplied_feature is not None and str(supplied_feature_value) != feature:
        raise ProviderAttemptAdmissionError(
            "metering feature does not match the routed task"
        )
    reservation_payload = meter.get("credit_reservation")
    if reservation_payload is not None:
        raise ProviderAttemptAdmissionError(
            "credit reservations are adapter-owned for routed provider attempts"
        )

    raw_cost_usd_micro = conservative_raw_cost_micro_usd(
        plan.deployment,
        input_tokens=request_estimate.input_tokens,
        output_tokens=request_estimate.output_tokens,
        input_cost_basis=request_estimate.input_cost_basis,
        region=execution.request.region or "global",
    )
    execution.authorize_estimated_attempt(
        deployment=plan.deployment,
        input_tokens=request_estimate.input_tokens,
        output_tokens=request_estimate.output_tokens,
        raw_cost_usd_micro=raw_cost_usd_micro,
    )
    reservation_amount = credits_charged(
        feature=feature,
        cost_usd_micro=raw_cost_usd_micro,
    )

    caller_trace_id = str(meter.get("trace_id") or "").strip()
    admission_trace_id = plan.trace_id
    reservation = reserve_provider_usage(
        organization_id=int(organization_id),
        role_id=int(role_id) if role_id is not None else None,
        feature=feature,
        trace_id=admission_trace_id,
        entity_id=str(entity_id) if entity_id is not None else None,
        sub_feature=execution.operation,
        metadata={
            **dict(meter.get("metadata") or {}),
            "route_id": execution.decision.route_id,
            "invocation_id": execution.invocation_id,
            "attempt_ordinal": plan.ordinal,
            "iteration_ordinal": plan.iteration_ordinal,
            "deployment_id": plan.deployment.deployment_id,
            "admission_source": "ai_routing_adapter",
            "estimated_input_tokens": int(request_estimate.input_tokens),
            "estimated_output_tokens": int(request_estimate.output_tokens),
            "estimated_input_cost_basis": request_estimate.input_cost_basis.value,
            "estimated_raw_cost_usd_micro": int(raw_cost_usd_micro),
            **(
                {"caller_trace_id": caller_trace_id}
                if caller_trace_id
                else {}
            ),
        },
        amount=reservation_amount,
        # This is a control-plane constraint, not caller metering metadata.
        # Profiles set a non-bypassable minimum and requests may strengthen it;
        # the immutable route decision is therefore the only authority source.
        require_role_authority=execution.decision.require_role_authority,
    )
    meter["feature"] = feature
    meter["organization_id"] = int(organization_id)
    if role_id is not None:
        meter["role_id"] = int(role_id)
    else:
        meter.pop("role_id", None)
    if user_id is not None:
        meter["user_id"] = int(user_id)
    else:
        meter.pop("user_id", None)
    if entity_id is not None:
        meter["entity_id"] = str(entity_id)
    else:
        meter.pop("entity_id", None)
    if caller_trace_id:
        meter["trace_id"] = caller_trace_id
    admitted_budget = AdmittedAttemptBudget(
        credit_reservation_ref=reservation.external_ref,
        estimated_input_tokens=int(request_estimate.input_tokens),
        estimated_output_tokens=int(request_estimate.output_tokens),
        estimated_input_cost_basis=request_estimate.input_cost_basis.value,
        estimated_cost_usd_micro=int(raw_cost_usd_micro),
    )
    return AttemptAdmission(
        metering=with_credit_reservation(meter, reservation),
        admitted_budget=admitted_budget,
    )


__all__ = [
    "AttemptAdmission",
    "ProviderAttemptAdmissionError",
    "admit_attempt",
]
