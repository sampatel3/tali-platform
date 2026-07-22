"""Pure, deterministic route planning for registered AI tasks."""

from __future__ import annotations

import uuid
from decimal import ROUND_CEILING, Decimal

from .contracts import (
    DataClassification,
    EligibleDeployment,
    ExcludedDeployment,
    ExclusionCode,
    ExecutionMode,
    InputCostBasis,
    LifecycleState,
    ModelDeployment,
    NoEligibleDeploymentError,
    PlanningErrorCode,
    ReasonCode,
    RiskClass,
    RouteAttempt,
    RouteDecision,
    RouteLimits,
    RoutePlanningError,
    RouteRequest,
    RouteStickiness,
)
from .fingerprints import decision_behavior_fingerprint
from .model_registry import (
    DEFAULT_MODEL_REGISTRY,
    ModelRegistry,
    UnknownDeploymentError,
)
from .task_registry import DEFAULT_TASK_REGISTRY, TaskRegistry
from .validation import validate_control_plane

PARITY_POLICY_VERSION = "tali-routing-parity-2026-07-22.v1"
_ROUTE_NAMESPACE = uuid.UUID("648f09ea-9358-5f49-bb0c-b05dcb887647")
_RISK_RANK = {value: rank for rank, value in enumerate(RiskClass)}
_DATA_RANK = {value: rank for rank, value in enumerate(DataClassification)}


def _stricter(left, right, ranks):
    if right is None:
        return left
    return right if ranks[right] > ranks[left] else left


def _expected_cost_micro_usd(
    deployment: ModelDeployment,
    *,
    execution_mode: ExecutionMode,
    input_tokens: int,
    output_tokens: int,
    input_cost_basis: InputCostBasis,
    region: str,
) -> int:
    pricing = deployment.pricing
    if pricing is None:
        raise ValueError("cannot estimate an unpriced deployment")
    if execution_mode is ExecutionMode.BATCH:
        input_rate = pricing.batch_input_per_million
        output_rate = pricing.batch_output_per_million
    else:
        input_rate = {
            InputCostBasis.STANDARD: pricing.input_per_million,
            InputCostBasis.CACHE_WRITE_5M: max(
                pricing.input_per_million, pricing.cache_write_5m_per_million
            ),
            InputCostBasis.CACHE_WRITE_1H: max(
                pricing.input_per_million, pricing.cache_write_1h_per_million
            ),
        }[input_cost_basis]
        output_rate = pricing.output_per_million
    raw = Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate
    if region == "us" and pricing.us_inference_multiplier is not None:
        raw *= pricing.us_inference_multiplier
    return int(raw.to_integral_value(rounding=ROUND_CEILING))


class RoutingPolicy:
    """Side-effect-free planner over immutable registries."""

    __slots__ = ("model_registry", "policy_version", "task_registry")

    def __init__(
        self,
        *,
        model_registry: ModelRegistry = DEFAULT_MODEL_REGISTRY,
        task_registry: TaskRegistry = DEFAULT_TASK_REGISTRY,
        policy_version: str = PARITY_POLICY_VERSION,
        max_workflow_depth: int = 8,
    ) -> None:
        if not policy_version.strip():
            raise ValueError("policy_version must be non-empty")
        validate_control_plane(
            model_registry,
            task_registry,
            max_workflow_depth=max_workflow_depth,
        )
        self.model_registry = model_registry
        self.task_registry = task_registry
        self.policy_version = policy_version

    def plan(self, request: RouteRequest) -> RouteDecision:
        profile = self.task_registry.get(request.task)
        if profile is None:
            task_name = getattr(request.task, "value", str(request.task))
            raise RoutePlanningError(
                PlanningErrorCode.UNKNOWN_TASK,
                f"task {task_name!r} has no registered execution profile",
            )
        if (
            request.estimated_input_tokens > profile.max_input_tokens
            or request.estimated_output_tokens > profile.max_output_tokens
        ):
            raise RoutePlanningError(
                PlanningErrorCode.PROFILE_LIMIT,
                f"request exceeds token limits for {profile.key.value}",
            )

        override = self._resolve_override(request.override_alias)
        pinned = self._resolve_pin(request.pinned_deployment_id)
        if override is not None and pinned is not None and override != pinned:
            raise RoutePlanningError(
                PlanningErrorCode.CONFLICTING_SELECTION,
                "override alias and pinned deployment resolve to different deployments",
            )

        selection_ids: tuple[str, ...]
        if pinned is not None:
            selection_ids = (pinned,)
        elif override is not None:
            selection_ids = (override,)
        else:
            selection_ids = profile.candidate_deployment_ids
        route_ids = frozenset((*selection_ids, *profile.fallback_deployment_ids))
        primary_transport_contracts = {
            deployment.transport_contract
            for deployment_id in profile.candidate_deployment_ids
            if (deployment := self.model_registry.get(deployment_id)) is not None
        }

        required_capabilities = (
            profile.required_capabilities | request.additional_capabilities
        )
        risk = _stricter(profile.risk, request.risk, _RISK_RANK)
        data_classification = _stricter(
            profile.data_classification,
            request.data_classification,
            _DATA_RANK,
        )
        require_role_authority = (
            profile.require_role_authority or request.require_role_authority
        )
        region = (request.region or "global").strip().lower()
        cost_ceiling = profile.max_cost_micro_usd
        if request.max_cost_micro_usd is not None:
            cost_ceiling = min(cost_ceiling, request.max_cost_micro_usd)

        allow_providers = (
            {value.strip().lower() for value in request.provider_allowlist}
            if request.provider_allowlist is not None
            else None
        )
        deny_providers = {value.strip().lower() for value in request.provider_denylist}
        eligible: list[EligibleDeployment] = []
        exclusions: list[ExcludedDeployment] = []

        for deployment in self.model_registry.deployments:
            codes: list[ExclusionCode] = []
            if (
                deployment.lifecycle is not LifecycleState.ACTIVE
                or deployment.pricing is None
            ):
                codes.append(ExclusionCode.LIFECYCLE)
            if profile.execution_mode not in deployment.supported_modes:
                codes.append(ExclusionCode.EXECUTION_MODE)
            if (
                deployment.deployment_id in selection_ids
                and (override is not None or pinned is not None)
                and deployment.transport_contract not in primary_transport_contracts
            ):
                codes.append(ExclusionCode.TRANSPORT_CONTRACT)
            if not required_capabilities.issubset(deployment.capabilities):
                codes.append(ExclusionCode.CAPABILITY)
            if any(
                conflict.issubset(required_capabilities)
                for conflict in deployment.capability_conflicts
            ):
                codes.append(ExclusionCode.CAPABILITY_CONFLICT)
            if (
                request.estimated_input_tokens + request.estimated_output_tokens
                > deployment.context_tokens
            ):
                codes.append(ExclusionCode.CONTEXT_LIMIT)
            if request.estimated_output_tokens > deployment.max_output_tokens:
                codes.append(ExclusionCode.OUTPUT_LIMIT)
            if data_classification not in deployment.allowed_data_classes:
                codes.append(ExclusionCode.DATA_CLASSIFICATION)
            if region not in deployment.regions:
                codes.append(ExclusionCode.REGION)
            provider = deployment.provider.lower()
            if allow_providers is not None and provider not in allow_providers:
                codes.append(ExclusionCode.PROVIDER_NOT_ALLOWED)
            if provider in deny_providers:
                codes.append(ExclusionCode.PROVIDER_DENIED)
            if _RISK_RANK[risk] > _RISK_RANK[deployment.max_risk]:
                codes.append(ExclusionCode.RISK)
            if profile.key not in deployment.evaluated_tasks:
                codes.append(ExclusionCode.NOT_TASK_EVALUATED)
            if deployment.quality_tier < profile.min_quality_tier:
                codes.append(ExclusionCode.QUALITY_FLOOR)
            if (
                request.tenant_allowed_deployments is not None
                and deployment.deployment_id not in request.tenant_allowed_deployments
            ):
                codes.append(ExclusionCode.TENANT_NOT_ALLOWED)
            if deployment.deployment_id in request.tenant_blocked_deployments:
                codes.append(ExclusionCode.TENANT_BLOCKED)

            if deployment.deployment_id not in route_ids:
                if pinned is not None:
                    codes.append(ExclusionCode.PIN_MISMATCH)
                elif override is not None:
                    codes.append(ExclusionCode.OVERRIDE_MISMATCH)
                else:
                    codes.append(ExclusionCode.TASK_POLICY)

            expected_cost: int | None = None
            if deployment.pricing is not None:
                expected_cost = _expected_cost_micro_usd(
                    deployment,
                    execution_mode=profile.execution_mode,
                    input_tokens=request.estimated_input_tokens,
                    output_tokens=request.estimated_output_tokens,
                    input_cost_basis=request.estimated_input_cost_basis,
                    region=region,
                )
                if expected_cost > cost_ceiling:
                    codes.append(ExclusionCode.COST_CEILING)

            if codes:
                exclusions.append(
                    ExcludedDeployment(
                        deployment.deployment_id, tuple(dict.fromkeys(codes))
                    )
                )
            else:
                assert expected_cost is not None
                eligible.append(
                    EligibleDeployment(
                        deployment_id=deployment.deployment_id,
                        model_id=deployment.model_id,
                        provider=deployment.provider,
                        expected_cost_micro_usd=expected_cost,
                        latency_rank=deployment.latency_rank,
                    )
                )

        selection_eligible = [
            item for item in eligible if item.deployment_id in selection_ids
        ]
        if not selection_eligible:
            raise NoEligibleDeploymentError(tuple(exclusions))
        selection_eligible.sort(
            key=lambda item: (
                item.expected_cost_micro_usd,
                item.latency_rank,
                item.deployment_id,
            )
        )
        selected = selection_eligible[0]
        reasons = self._selection_reasons(
            selection_eligible,
            pinned=pinned is not None,
            override=override is not None,
        )

        eligible_by_id = {item.deployment_id: item for item in eligible}
        attempts = [
            RouteAttempt(
                ordinal=1,
                deployment_id=selected.deployment_id,
                model_id=selected.model_id,
                expected_cost_micro_usd=selected.expected_cost_micro_usd,
                reason=reasons[0],
            )
        ]
        # A profile authorizes a finite fallback set, but the selected primary
        # may have come from an override or pin rather than the profile's normal
        # candidate list. Publish only the exact registered replacement chain
        # reachable from the deployment we actually selected. This keeps every
        # immutable RouteDecision attempt executable by RouteExecution.
        authorized_fallback_ids = frozenset(profile.fallback_deployment_ids)
        current_deployment = self.model_registry.get(selected.deployment_id)
        while current_deployment is not None:
            fallback_id = current_deployment.replacement_deployment_id
            if fallback_id is None or fallback_id not in authorized_fallback_ids:
                break
            fallback = eligible_by_id.get(fallback_id)
            if fallback is None:
                break
            attempts.append(
                RouteAttempt(
                    ordinal=len(attempts) + 1,
                    deployment_id=fallback.deployment_id,
                    model_id=fallback.model_id,
                    expected_cost_micro_usd=fallback.expected_cost_micro_usd,
                    reason=ReasonCode.PROFILE_FALLBACK,
                )
            )
            current_deployment = self.model_registry.get(fallback_id)

        root_invocation_id = request.root_invocation_id or request.invocation_id
        pin_key = self._pin_key(
            profile.stickiness,
            invocation_id=request.invocation_id,
            root_invocation_id=root_invocation_id,
        )
        route_id = self._route_id(
            request.invocation_id, profile.key.value, selected.deployment_id
        )
        behavior_fingerprint = decision_behavior_fingerprint(
            policy_version=self.policy_version,
            registry_version=self.model_registry.version,
            task_registry_version=self.task_registry.version,
            profile=profile,
            required_capabilities=required_capabilities,
            risk=risk,
            data_classification=data_classification,
            region=region,
            attempt_ids=tuple(item.deployment_id for item in attempts),
            require_role_authority=require_role_authority,
        )
        return RouteDecision(
            route_id=route_id,
            behavior_fingerprint=behavior_fingerprint,
            invocation_id=request.invocation_id,
            root_invocation_id=root_invocation_id,
            parent_invocation_id=request.parent_invocation_id,
            workflow=profile.workflow,
            task=profile.key,
            execution_mode=profile.execution_mode,
            required_capabilities=required_capabilities,
            request_shape=profile.request_shape,
            risk=risk,
            data_classification=data_classification,
            registry_version=self.model_registry.version,
            task_registry_version=self.task_registry.version,
            policy_version=self.policy_version,
            profile_version=profile.profile_version,
            semantic_revision=profile.semantic_revision,
            schema_revision=profile.schema_revision,
            prompt_revision=profile.prompt_revision,
            tool_revision=profile.tool_revision,
            feature=profile.feature,
            require_role_authority=require_role_authority,
            selected_deployment_id=selected.deployment_id,
            selected_model_id=selected.model_id,
            eligible_deployments=tuple(
                sorted(
                    eligible,
                    key=lambda item: (
                        item.expected_cost_micro_usd,
                        item.latency_rank,
                        item.deployment_id,
                    ),
                )
            ),
            exclusions=tuple(exclusions),
            attempts=tuple(attempts),
            limits=RouteLimits(
                max_input_tokens=profile.max_input_tokens,
                max_output_tokens=profile.max_output_tokens,
                max_iterations=profile.max_iterations,
                latency_slo_ms=profile.latency_slo_ms,
                max_cost_micro_usd=cost_ceiling,
                max_attempts_per_iteration=profile.max_attempts_per_iteration,
                retry_backoff_base_ms=profile.retry_backoff_base_ms,
                retry_backoff_max_ms=profile.retry_backoff_max_ms,
            ),
            fallback_classes=profile.fallback_classes,
            reason_codes=reasons,
            stickiness=profile.stickiness,
            pin_key=pin_key,
        )

    def _resolve_override(self, alias: str | None) -> str | None:
        if alias is None:
            return None
        try:
            return self.model_registry.resolve(alias).deployment_id
        except UnknownDeploymentError as exc:
            raise RoutePlanningError(
                PlanningErrorCode.INVALID_OVERRIDE,
                str(exc),
            ) from exc

    def _resolve_pin(self, deployment_id: str | None) -> str | None:
        if deployment_id is None:
            return None
        deployment = self.model_registry.get(deployment_id)
        if deployment is None:
            raise RoutePlanningError(
                PlanningErrorCode.INVALID_PIN,
                f"unknown pinned deployment: {deployment_id!r}",
            )
        return deployment.deployment_id

    @staticmethod
    def _selection_reasons(
        candidates: list[EligibleDeployment],
        *,
        pinned: bool,
        override: bool,
    ) -> tuple[ReasonCode, ...]:
        if pinned:
            return (ReasonCode.PINNED_DEPLOYMENT,)
        if override:
            return (ReasonCode.VALIDATED_OVERRIDE,)
        reasons = [ReasonCode.PRIMARY_POLICY, ReasonCode.LOWEST_EXPECTED_COST]
        cheapest = candidates[0].expected_cost_micro_usd
        same_cost = [
            item for item in candidates if item.expected_cost_micro_usd == cheapest
        ]
        if len(same_cost) > 1:
            reasons.append(ReasonCode.LOWEST_LATENCY)
            fastest = same_cost[0].latency_rank
            if sum(item.latency_rank == fastest for item in same_cost) > 1:
                reasons.append(ReasonCode.STABLE_TIEBREAK)
        return tuple(reasons)

    def _route_id(self, invocation_id: str, task: str, deployment_id: str) -> str:
        identity = "|".join(
            (
                invocation_id,
                task,
                deployment_id,
                self.policy_version,
                self.model_registry.version,
                self.task_registry.version,
            )
        )
        return str(uuid.uuid5(_ROUTE_NAMESPACE, identity))

    @staticmethod
    def _pin_key(
        stickiness: RouteStickiness,
        *,
        invocation_id: str,
        root_invocation_id: str,
    ) -> str | None:
        if stickiness is RouteStickiness.NONE:
            return None
        if stickiness is RouteStickiness.ROOT_INVOCATION:
            return root_invocation_id
        return invocation_id


DEFAULT_ROUTING_POLICY = RoutingPolicy()
