"""Small immutable values shared by route execution and provider adapters."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import FallbackClass
from .model_registry import ModelDeployment


@dataclass(frozen=True, slots=True)
class RoutingAttribution:
    organization_id: int | None = None
    user_id: int | None = None
    role_id: int | None = None
    agent_run_id: int | None = None
    entity_id: str | None = None


@dataclass(frozen=True, slots=True)
class PhysicalAttempt:
    ordinal: int
    iteration_ordinal: int
    attempt_in_iteration: int
    deployment: ModelDeployment
    trace_id: str
    started_monotonic: float
    admitted_budget: "AdmittedAttemptBudget"
    fallback_from_deployment_id: str | None = None
    fallback_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderAttemptResult:
    status: str
    error_class: str | None
    definitely_nonbillable: bool
    next_attempt: "NextAttemptAuthorization | None" = None


@dataclass(frozen=True, slots=True)
class NextAttemptAuthorization:
    """One-use execution-owned permission for the next physical attempt."""

    deployment_id: str
    fallback_class: FallbackClass
    switches_deployment: bool


@dataclass(frozen=True, slots=True)
class PlannedPhysicalAttempt:
    """Execution-owned, one-use plan consumed after hard admission.

    Admission must price and validate this exact deployment.  Keeping the
    immutable plan object between ``plan_next_attempt`` and ``begin_attempt``
    prevents a fallback from being admitted against the primary model and
    then rendered with a different context window or price.
    """

    ordinal: int
    iteration_ordinal: int
    attempt_in_iteration: int
    deployment: ModelDeployment
    trace_id: str
    fallback_from_deployment_id: str | None = None
    fallback_reason: str | None = None


@dataclass(frozen=True, slots=True)
class AdmittedAttemptBudget:
    """Durable hard-admission identity and conservative spend envelope."""

    credit_reservation_ref: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_input_cost_basis: str
    estimated_cost_usd_micro: int

    def __post_init__(self) -> None:
        if not self.credit_reservation_ref.strip():
            raise ValueError("credit_reservation_ref must be non-empty")
        values = (
            self.estimated_input_tokens,
            self.estimated_output_tokens,
            self.estimated_cost_usd_micro,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in values
        ):
            raise ValueError("admitted attempt estimates must be non-negative")
        if self.estimated_output_tokens == 0:
            raise ValueError("estimated_output_tokens must be positive")
        if not self.estimated_input_cost_basis.strip():
            raise ValueError("estimated_input_cost_basis must be non-empty")


__all__ = [
    "AdmittedAttemptBudget",
    "NextAttemptAuthorization",
    "PhysicalAttempt",
    "PlannedPhysicalAttempt",
    "ProviderAttemptResult",
    "RoutingAttribution",
]
