"""Durable lifecycle for executing an immutable route decision.

The pure policy stops at a :class:`RouteDecision`.  This module owns the
provider-neutral execution boundary: it durably records the logical invocation
before spend, gives every physical provider call its own attempt ordinal, and
pins a successful deployment for the rest of the invocation.

Provider adapters call ``begin_attempt`` immediately before their transport and
one of the terminal methods immediately afterwards.  Pre-provider telemetry
writes fail closed.  Post-provider telemetry failures are logged and never
raised, because surfacing an error after a provider may have accepted work can
cause an unsafe duplicate retry.
"""

from __future__ import annotations

import logging
from time import monotonic
from typing import Any, Callable

from sqlalchemy.orm import Session

from ...platform.database import SessionLocal
from ...services.provider_usage_admission import (
    provider_error_is_definitely_nonbillable,
)
from .contracts import FallbackClass, RouteDecision, RouteRequest
from .execution_types import (
    AdmittedAttemptBudget,
    NextAttemptAuthorization,
    PhysicalAttempt,
    PlannedPhysicalAttempt,
    ProviderAttemptResult,
    RoutingAttribution,
)
from .attempt_evidence import (
    evidence_has_known_usage,
    evidence_usage_values,
    error_class,
    exception_request_id,
    latency_ms,
    provider_evidence,
    response_request_id,
    status_code,
    usage_values,
)
from .model_registry import DEFAULT_MODEL_REGISTRY, ModelDeployment, ModelRegistry
from .pricing import RoutedPricingReceiptError
from .lineage_validation import validate_runtime_lineage
from .task_registry import DEFAULT_TASK_REGISTRY, TaskRegistry
from .snapshots import decision_snapshot, request_snapshot
from .telemetry import (
    create_attempt,
    create_invocation,
    finish_attempt,
    finish_invocation,
    start_attempt,
    start_invocation,
)

logger = logging.getLogger("taali.ai_routing.execution")


class RouteExecutionError(RuntimeError):
    """The planned route could not be executed without violating its contract."""


class RoutingTelemetryUnavailable(RouteExecutionError):
    """The durable pre-provider trail could not be written, so spend is denied."""


SessionFactory = Callable[[], Session]


class RouteExecution:
    """Stateful execution of one immutable, sticky route decision."""

    def __init__(
        self,
        *,
        request: RouteRequest,
        decision: RouteDecision,
        attribution: RoutingAttribution | None = None,
        operation: str | None = None,
        registry: ModelRegistry = DEFAULT_MODEL_REGISTRY,
        task_registry: TaskRegistry = DEFAULT_TASK_REGISTRY,
        session_factory: SessionFactory = SessionLocal,
    ) -> None:
        if request.invocation_id != decision.invocation_id:
            raise RouteExecutionError(
                "route request and decision invocation IDs differ"
            )
        if request.task is not decision.task:
            raise RouteExecutionError("route request and decision task keys differ")
        if not decision.attempts:
            raise RouteExecutionError("route decision has no authorized attempts")

        self.request = request
        self.decision = decision
        self.attribution = attribution or RoutingAttribution()
        self.operation = operation or decision.task.value
        self.registry = registry
        self.task_registry = task_registry
        self._session_factory = session_factory
        self._next_ordinal = 1
        self._next_iteration_ordinal = 1
        self._active_iteration_ordinal: int | None = None
        self._iteration_started_monotonic: float | None = None
        self._attempts_in_active_iteration = 0
        self._active_attempt: PhysicalAttempt | None = None
        self._pending_attempt_plan: PlannedPhysicalAttempt | None = None
        self._last_deployment_id: str | None = None
        self._last_attempt_status: str | None = None
        self._selected_deployment_id: str | None = None
        self._terminal_status: str | None = None
        self._started = False
        self._successful_attempts = 0
        self._cumulative_cost_usd_micro = 0
        self._replay_blocked = False
        self._next_attempt_authorization: NextAttemptAuthorization | None = None

        attempt_plan = tuple(attempt.deployment_id for attempt in decision.attempts)
        if decision.registry_version != registry.version:
            raise RouteExecutionError(
                "route decision model registry differs from its runtime registry"
            )
        if decision.task_registry_version != task_registry.version:
            raise RouteExecutionError(
                "route decision task registry differs from its runtime registry"
            )
        if attempt_plan[0] != decision.selected_deployment_id:
            raise RouteExecutionError(
                "selected deployment must be first in the attempt plan"
            )
        if tuple(attempt.ordinal for attempt in decision.attempts) != tuple(
            range(1, len(decision.attempts) + 1)
        ):
            raise RouteExecutionError("route attempt plan ordinals are not contiguous")
        if len(set(attempt_plan)) != len(attempt_plan):
            raise RouteExecutionError("route attempt plan repeats a deployment")
        for planned_attempt in decision.attempts:
            deployment = registry.get(planned_attempt.deployment_id)
            if deployment is None:
                raise RouteExecutionError(
                    "route references unregistered deployment "
                    f"{planned_attempt.deployment_id!r}"
                )
            if deployment.model_id != planned_attempt.model_id:
                raise RouteExecutionError(
                    "route model does not match deployment "
                    f"{planned_attempt.deployment_id!r}"
                )
        self._attempt_plan = attempt_plan
        self._attempt_plan_index = 0

    @property
    def invocation_id(self) -> str:
        return self.decision.invocation_id

    @property
    def selected_deployment_id(self) -> str:
        return self._selected_deployment_id or self.decision.selected_deployment_id

    @property
    def selected_deployment(self) -> ModelDeployment:
        deployment = self.registry.get(self.selected_deployment_id)
        if deployment is None:  # Registry closure was checked in __init__.
            raise RouteExecutionError("selected deployment disappeared from registry")
        return deployment

    @property
    def selected_model_id(self) -> str:
        return self.selected_deployment.model_id

    @property
    def last_attempt_model_id(self) -> str:
        deployment_id = self._last_deployment_id or self.selected_deployment_id
        deployment = self.registry.get(deployment_id)
        if deployment is None:
            raise RouteExecutionError("last attempt deployment disappeared")
        return deployment.model_id

    @property
    def terminal_status(self) -> str | None:
        return self._terminal_status

    @property
    def successful_attempts(self) -> int:
        return self._successful_attempts

    @property
    def cumulative_cost_usd_micro(self) -> int:
        return self._cumulative_cost_usd_micro

    def authorize_estimated_attempt(
        self,
        *,
        deployment: ModelDeployment | None = None,
        input_tokens: int,
        output_tokens: int,
        raw_cost_usd_micro: int,
    ) -> None:
        """Reject a request that would exceed immutable route/deployment limits."""

        if input_tokens < 0 or input_tokens > self.decision.limits.max_input_tokens:
            raise RouteExecutionError(
                "provider request exceeds the route input-token ceiling"
            )
        if output_tokens <= 0 or output_tokens > self.decision.limits.max_output_tokens:
            raise RouteExecutionError(
                "provider request exceeds the route output-token ceiling"
            )
        effective_deployment = deployment or self.selected_deployment
        if input_tokens + output_tokens > effective_deployment.context_tokens:
            raise RouteExecutionError(
                "provider request exceeds the deployment context window"
            )
        if raw_cost_usd_micro < 0 or (
            self._cumulative_cost_usd_micro + raw_cost_usd_micro
            > self.decision.limits.max_cost_micro_usd
        ):
            raise RouteExecutionError(
                "provider request exceeds the logical route cost ceiling"
            )

    def start(self) -> "RouteExecution":
        """Persist the decision before any provider attempt can begin."""

        if self._started:
            return self
        if self._terminal_status is not None:
            raise RouteExecutionError("cannot start a terminal route execution")

        def write(session: Session) -> None:
            validate_runtime_lineage(
                session,
                self.decision,
                attribution=self.attribution,
                task_registry=self.task_registry,
            )
            create_invocation(
                session,
                route_id=self.decision.route_id,
                invocation_id=self.decision.invocation_id,
                root_invocation_id=self.decision.root_invocation_id,
                parent_invocation_id=self.decision.parent_invocation_id,
                operation=self.operation,
                workflow=self.decision.workflow.value,
                task=self.decision.task.value,
                profile_version=self.decision.profile_version,
                policy_version=self.decision.policy_version,
                registry_version=self.decision.registry_version,
                request_snapshot=request_snapshot(self.request),
                decision_snapshot=decision_snapshot(self.decision),
                selected_deployment_id=self.decision.selected_deployment_id,
                organization_id=self.attribution.organization_id,
                user_id=self.attribution.user_id,
                role_id=self.attribution.role_id,
                agent_run_id=self.attribution.agent_run_id,
                entity_id=self.attribution.entity_id,
            )
            start_invocation(session, self.decision.invocation_id)

        self._write_before_provider(write, action="start invocation")
        self._started = True
        return self

    def plan_next_attempt(
        self,
        *,
        start_new_iteration: bool,
    ) -> PlannedPhysicalAttempt:
        """Return the exact one-use plan that hard admission must authorize.

        One feature-level model call is a logical iteration. A transport retry
        remains inside that iteration; a later tool-loop round starts a new
        iteration. This prevents retries from silently consuming (or extending)
        the task's semantic iteration budget.
        """

        if not self._started:
            self.start()
        if self._terminal_status is not None:
            raise RouteExecutionError(
                "cannot call a provider for a terminal invocation"
            )
        if self._active_attempt is not None:
            raise RouteExecutionError("a provider attempt is already active")
        if self._pending_attempt_plan is not None:
            raise RouteExecutionError("a provider attempt plan is already pending")
        if self._replay_blocked:
            raise RouteExecutionError(
                "an outcome-ambiguous attempt cannot be replayed or failed over"
            )

        if start_new_iteration:
            if self._active_iteration_ordinal is not None:
                raise RouteExecutionError(
                    "cannot start a new logical iteration while a retry is pending"
                )
            if self._last_attempt_status == "failed":
                raise RouteExecutionError(
                    "the prior terminal result did not authorize a new iteration"
                )
            if self._next_iteration_ordinal > self.decision.limits.max_iterations:
                raise RouteExecutionError("route iteration ceiling was reached")
            self._active_iteration_ordinal = self._next_iteration_ordinal
            self._next_iteration_ordinal += 1
            self._iteration_started_monotonic = monotonic()
            self._attempts_in_active_iteration = 0
        elif self._active_iteration_ordinal is None:
            raise RouteExecutionError("no logical iteration is open for a retry")

        if self.remaining_iteration_timeout_s() <= 0:
            raise RouteExecutionError("logical iteration latency ceiling was reached")

        max_attempts = int(
            getattr(self.decision.limits, "max_attempts_per_iteration", 1)
        )
        if self._attempts_in_active_iteration >= max_attempts:
            raise RouteExecutionError(
                "physical-attempt ceiling for this logical iteration was reached"
            )

        authorization = self._next_attempt_authorization
        next_plan_index = self._attempt_plan_index
        if self._attempts_in_active_iteration == 0:
            deployment_id = self.selected_deployment_id
            is_switch = False
            fallback_reason = None
        elif self._last_attempt_status == "failed":
            if authorization is None:
                raise RouteExecutionError(
                    "the prior terminal result did not authorize another attempt"
                )
            deployment_id = authorization.deployment_id
            is_switch = authorization.switches_deployment
            fallback_reason = (
                authorization.fallback_class.value if is_switch else None
            )
            if is_switch:
                next_plan_index += 1
                if (
                    next_plan_index >= len(self._attempt_plan)
                    or self._attempt_plan[next_plan_index] != deployment_id
                ):
                    raise RouteExecutionError(
                        "authorized fallback is outside the ordered attempt plan"
                    )
        else:
            raise RouteExecutionError(
                "another physical attempt requires a typed terminal result"
            )

        deployment = self.registry.get(deployment_id)
        if deployment is None:
            raise RouteExecutionError(f"unknown deployment {deployment_id!r}")
        assert self._active_iteration_ordinal is not None
        ordinal = self._next_ordinal
        plan = PlannedPhysicalAttempt(
            ordinal=ordinal,
            iteration_ordinal=self._active_iteration_ordinal,
            attempt_in_iteration=self._attempts_in_active_iteration + 1,
            deployment=deployment,
            trace_id=f"ai-route:{self.invocation_id}:{ordinal}",
            fallback_from_deployment_id=(
                self._last_deployment_id if is_switch else None
            ),
            fallback_reason=fallback_reason,
        )
        self._pending_attempt_plan = plan
        return plan

    def cancel_planned_attempt(self, plan: PlannedPhysicalAttempt) -> None:
        """Drop a plan whose hard admission failed before provider work."""

        if self._pending_attempt_plan is not plan:
            raise RouteExecutionError("attempt plan is not the pending one")
        self._pending_attempt_plan = None
        if self._attempts_in_active_iteration == 0:
            self._active_iteration_ordinal = None
            self._iteration_started_monotonic = None
            self._next_iteration_ordinal -= 1

    def remaining_iteration_timeout_s(self) -> float:
        """Remaining wall-clock budget shared by all attempts in this call."""

        if self._iteration_started_monotonic is None:
            raise RouteExecutionError("no logical iteration is currently active")
        elapsed_ms = (monotonic() - self._iteration_started_monotonic) * 1000.0
        return max(
            (float(self.decision.limits.latency_slo_ms) - elapsed_ms) / 1000.0,
            0.0,
        )

    def begin_attempt(
        self,
        plan: PlannedPhysicalAttempt,
        *,
        admitted_budget: AdmittedAttemptBudget,
    ) -> PhysicalAttempt:
        """Durably consume an admitted plan immediately before transport.

        Approved adapters plan first, admit that exact deployment, then pass
        the same immutable object and hard-admission budget here. There is no
        unadmitted lifecycle convenience: every physical attempt must have one
        traceable reservation before provider work can begin.
        """

        if self._pending_attempt_plan is not plan:
            raise RouteExecutionError("attempt plan is stale or not execution-owned")

        deployment = plan.deployment
        attempt = PhysicalAttempt(
            ordinal=plan.ordinal,
            iteration_ordinal=plan.iteration_ordinal,
            attempt_in_iteration=plan.attempt_in_iteration,
            deployment=deployment,
            trace_id=plan.trace_id,
            started_monotonic=monotonic(),
            admitted_budget=admitted_budget,
            fallback_from_deployment_id=plan.fallback_from_deployment_id,
            fallback_reason=plan.fallback_reason,
        )

        def write(session: Session) -> None:
            create_attempt(
                session,
                invocation_id=self.invocation_id,
                ordinal=plan.ordinal,
                iteration_ordinal=plan.iteration_ordinal,
                attempt_in_iteration=plan.attempt_in_iteration,
                provider=deployment.provider,
                runtime=deployment.runtime,
                deployment_id=deployment.deployment_id,
                model=deployment.model_id,
                region=(self.request.region or "global").strip().lower(),
                pricing_id=(
                    deployment.pricing.pricing_id
                    if deployment.pricing is not None
                    else None
                ),
                credit_reservation_ref=admitted_budget.credit_reservation_ref,
                estimated_input_tokens=admitted_budget.estimated_input_tokens,
                estimated_output_tokens=admitted_budget.estimated_output_tokens,
                estimated_input_cost_basis=(
                    admitted_budget.estimated_input_cost_basis
                ),
                admitted_cost_usd_micro=(
                    admitted_budget.estimated_cost_usd_micro
                ),
                fallback_from_deployment_id=attempt.fallback_from_deployment_id,
                fallback_reason=attempt.fallback_reason,
            )
            start_attempt(session, self.invocation_id, plan.ordinal)

        self._write_before_provider(write, action=f"start attempt {plan.ordinal}")
        self._pending_attempt_plan = None
        self._active_attempt = attempt
        if attempt.fallback_from_deployment_id is not None:
            self._attempt_plan_index += 1
        self._next_attempt_authorization = None
        self._next_ordinal += 1
        self._attempts_in_active_iteration += 1
        return attempt

    def finish_success(self, attempt: PhysicalAttempt, response: Any) -> None:
        """Record a successful physical response and pin its deployment."""

        self._require_active(attempt)
        evidence = provider_evidence(self._session_factory, attempt.trace_id)
        usage = getattr(response, "usage", None)
        if evidence is not None and evidence_has_known_usage(evidence):
            known = evidence_usage_values(evidence)
            usage_unknown = False
            provider_request_id = evidence.anthropic_request_id
            usage_event_id = evidence.usage_event_id
            claude_call_log_id = evidence.id
        elif usage is not None:
            try:
                known = usage_values(
                    usage=usage,
                    deployment=attempt.deployment,
                    region=self.request.region or "global",
                )
            except RoutedPricingReceiptError:
                # A conforming metered transport records malformed receipts
                # before raising, but this lifecycle boundary must remain safe
                # even if an alternate approved transport returns one. Provider
                # work has completed, so retain the conservative admitted cost
                # and terminalize the attempt instead of stranding it as active.
                logger.exception(
                    "could not price provider receipt; recording usage unknown "
                    "invocation=%s ordinal=%s",
                    self.invocation_id,
                    attempt.ordinal,
                )
                known = {}
                usage_unknown = True
            else:
                usage_unknown = False
            provider_request_id = (
                evidence.anthropic_request_id
                if evidence is not None
                else response_request_id(response)
            )
            usage_event_id = evidence.usage_event_id if evidence is not None else None
            claude_call_log_id = evidence.id if evidence is not None else None
        else:
            known = {}
            usage_unknown = True
            provider_request_id = (
                evidence.anthropic_request_id
                if evidence is not None
                else response_request_id(response)
            )
            usage_event_id = evidence.usage_event_id if evidence is not None else None
            claude_call_log_id = evidence.id if evidence is not None else None

        elapsed_ms = latency_ms(attempt.started_monotonic)

        def write(session: Session) -> None:
            finish_attempt(
                session,
                self.invocation_id,
                attempt.ordinal,
                status="succeeded",
                latency_ms=elapsed_ms,
                usage_unknown=usage_unknown,
                provider_request_id=provider_request_id,
                usage_event_id=usage_event_id,
                claude_call_log_id=claude_call_log_id,
                **known,
            )

        self._write_after_provider(write, action=f"finish attempt {attempt.ordinal}")
        self._selected_deployment_id = attempt.deployment.deployment_id
        self._last_deployment_id = attempt.deployment.deployment_id
        self._last_attempt_status = "succeeded"
        self._successful_attempts += 1
        self._cumulative_cost_usd_micro += (
            attempt.admitted_budget.estimated_cost_usd_micro
            if usage_unknown
            else int(known["cost_usd_micro"])
        )
        self._active_attempt = None
        self._next_attempt_authorization = None
        self._active_iteration_ordinal = None
        self._iteration_started_monotonic = None
        self._attempts_in_active_iteration = 0

    def finish_error(
        self,
        attempt: PhysicalAttempt,
        error: BaseException,
        *,
        stream_accepted: bool = False,
    ) -> ProviderAttemptResult:
        """Record an explicit rejection or an outcome-ambiguous failure."""

        self._require_active(attempt)
        definitely_nonbillable = not stream_accepted and (
            bool(getattr(error, "provider_not_called", False))
            or provider_error_is_definitely_nonbillable(error)
        )
        provider_status = status_code(error)
        attempt_error_class = error_class(
            provider_status=provider_status,
            error=error,
            ambiguous=not definitely_nonbillable,
        )
        status = "failed" if definitely_nonbillable else "ambiguous"
        evidence = provider_evidence(self._session_factory, attempt.trace_id)
        evidence_known = evidence is not None and evidence_has_known_usage(evidence)
        known = (
            evidence_usage_values(evidence)
            if evidence_known and evidence is not None
            else (
                {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_creation_tokens": 0,
                    "cost_usd_micro": 0,
                }
                if definitely_nonbillable
                else {}
            )
        )
        usage_unknown = not (evidence_known or definitely_nonbillable)
        provider_request_id = (
            evidence.anthropic_request_id
            if evidence is not None
            else exception_request_id(error)
        )
        usage_event_id = evidence.usage_event_id if evidence is not None else None
        claude_call_log_id = evidence.id if evidence is not None else None

        def write(session: Session) -> None:
            finish_attempt(
                session,
                self.invocation_id,
                attempt.ordinal,
                status=status,
                latency_ms=latency_ms(attempt.started_monotonic),
                usage_unknown=usage_unknown,
                error_class=attempt_error_class,
                error_reason=attempt_error_class,
                provider_request_id=provider_request_id,
                usage_event_id=usage_event_id,
                claude_call_log_id=claude_call_log_id,
                **known,
            )

        self._write_after_provider(write, action=f"finish attempt {attempt.ordinal}")
        self._last_deployment_id = attempt.deployment.deployment_id
        self._last_attempt_status = status
        self._cumulative_cost_usd_micro += (
            attempt.admitted_budget.estimated_cost_usd_micro
            if usage_unknown
            else int(known["cost_usd_micro"])
        )
        self._active_attempt = None
        if not definitely_nonbillable:
            self._replay_blocked = True
        next_attempt = self._authorize_next_attempt(
            attempt=attempt,
            provider_status=provider_status,
            error=error,
            definitely_nonbillable=definitely_nonbillable,
        )
        self._next_attempt_authorization = next_attempt
        if next_attempt is None:
            self._active_iteration_ordinal = None
            self._iteration_started_monotonic = None
            self._attempts_in_active_iteration = 0
        return ProviderAttemptResult(
            status=status,
            error_class=attempt_error_class,
            definitely_nonbillable=definitely_nonbillable,
            next_attempt=next_attempt,
        )

    def finish(self, status: str) -> None:
        """Finish the logical invocation after its workflow step completes."""

        if status not in {"succeeded", "failed", "cancelled"}:
            raise RouteExecutionError(f"invalid invocation status {status!r}")
        if self._terminal_status is not None:
            if self._terminal_status != status:
                raise RouteExecutionError("invocation already finished differently")
            return
        if self._active_attempt is not None:
            raise RouteExecutionError("cannot finish with an active provider attempt")
        if self._pending_attempt_plan is not None:
            raise RouteExecutionError("cannot finish with a pending provider attempt")
        if status == "succeeded" and self._successful_attempts == 0:
            raise RouteExecutionError(
                "a provider invocation cannot succeed without a response"
            )

        def write(session: Session) -> None:
            finish_invocation(
                session,
                self.invocation_id,
                status=status,
                selected_deployment_id=(
                    self._selected_deployment_id or self.decision.selected_deployment_id
                ),
            )

        # No provider may follow a terminal call. A write failure still should
        # not make already-completed provider work look safe to replay.
        self._write_after_provider(write, action="finish invocation")
        self._terminal_status = status

    def finish_workflow(self, *, succeeded: bool) -> None:
        """Map a feature outcome onto the logical invocation lifecycle.

        Injected test doubles and deterministic substitutes may return a valid
        result without touching the routed provider adapter. Such an unused
        provider plan is cancelled rather than falsely counted as model
        success in the future optimization dataset.
        """

        if succeeded and self._successful_attempts:
            self.finish("succeeded")
        elif succeeded:
            self.finish("cancelled")
        else:
            self.finish("failed")

    def routing_metadata(self, attempt: PhysicalAttempt) -> dict[str, Any]:
        """Small content-free projection embedded in existing usage metadata."""

        return {
            "route_id": self.decision.route_id,
            "invocation_id": self.invocation_id,
            "root_invocation_id": self.decision.root_invocation_id,
            "parent_invocation_id": self.decision.parent_invocation_id,
            "workflow": self.decision.workflow.value,
            "task": self.decision.task.value,
            "profile_version": self.decision.profile_version,
            "policy_version": self.decision.policy_version,
            "registry_version": self.decision.registry_version,
            "deployment_id": attempt.deployment.deployment_id,
            "model_id": attempt.deployment.model_id,
            "attempt_ordinal": attempt.ordinal,
            "iteration_ordinal": attempt.iteration_ordinal,
            "attempt_in_iteration": attempt.attempt_in_iteration,
            "region": (self.request.region or "global").strip().lower(),
            "pricing_id": (
                attempt.deployment.pricing.pricing_id
                if attempt.deployment.pricing is not None
                else None
            ),
            "behavior_fingerprint": self.decision.behavior_fingerprint,
            "reason_codes": [code.value for code in self.decision.reason_codes],
        }

    def _write_before_provider(
        self, operation: Callable[[Session], None], *, action: str
    ) -> None:
        try:
            with self._session_factory() as session:
                operation(session)
                session.commit()
        except Exception as exc:
            raise RoutingTelemetryUnavailable(
                f"could not durably {action}; provider work was not started"
            ) from exc

    def _write_after_provider(
        self, operation: Callable[[Session], None], *, action: str
    ) -> None:
        try:
            with self._session_factory() as session:
                operation(session)
                session.commit()
        except Exception:
            logger.exception(
                "ai routing telemetry failed after provider work action=%s "
                "invocation=%s",
                action,
                self.invocation_id,
            )

    def _require_active(self, attempt: PhysicalAttempt) -> None:
        if self._active_attempt is None or self._active_attempt != attempt:
            raise RouteExecutionError("attempt is not the active provider attempt")

    def _authorize_next_attempt(
        self,
        *,
        attempt: PhysicalAttempt,
        provider_status: int | None,
        error: BaseException,
        definitely_nonbillable: bool,
    ) -> NextAttemptAuthorization | None:
        if not definitely_nonbillable:
            return None
        max_attempts = int(
            getattr(self.decision.limits, "max_attempts_per_iteration", 1)
        )
        if self._attempts_in_active_iteration >= max_attempts:
            return None
        if (
            provider_status == 429
            and FallbackClass.RETRYABLE_TRANSPORT in self.decision.fallback_classes
        ):
            return NextAttemptAuthorization(
                deployment_id=attempt.deployment.deployment_id,
                fallback_class=FallbackClass.RETRYABLE_TRANSPORT,
                switches_deployment=False,
            )
        if (
            bool(getattr(error, "provider_not_called", False))
            and FallbackClass.PRE_ACCEPTANCE_TRANSPORT
            in self.decision.fallback_classes
        ):
            return NextAttemptAuthorization(
                deployment_id=attempt.deployment.deployment_id,
                fallback_class=FallbackClass.PRE_ACCEPTANCE_TRANSPORT,
                switches_deployment=False,
            )
        next_index = self._attempt_plan_index + 1
        replacement_id = attempt.deployment.replacement_deployment_id
        if (
            provider_status == 404
            and FallbackClass.REGISTERED_REPLACEMENT
            in self.decision.fallback_classes
            and replacement_id is not None
            and next_index < len(self._attempt_plan)
            and self._attempt_plan[next_index] == replacement_id
        ):
            return NextAttemptAuthorization(
                deployment_id=replacement_id,
                fallback_class=FallbackClass.REGISTERED_REPLACEMENT,
                switches_deployment=True,
            )
        return None


__all__ = [
    "PhysicalAttempt",
    "ProviderAttemptResult",
    "RouteExecution",
    "RouteExecutionError",
    "RoutingAttribution",
    "RoutingTelemetryUnavailable",
]
