"""Fail-closed validation for routing registry closure and workflow topology."""

from __future__ import annotations

from enum import Enum

from .contracts import (
    FallbackClass,
    LifecycleState,
    ModelDeployment,
    RiskClass,
    RouteStickiness,
    TaskProfile,
    WorkflowKey,
)
from .model_registry import ModelRegistry
from .task_registry import TaskRegistry


class ValidationCode(str, Enum):
    UNKNOWN_WORKFLOW = "routing.registry.unknown_workflow.v1"
    WORKFLOW_CYCLE = "routing.registry.workflow_cycle.v1"
    WORKFLOW_DEPTH = "routing.registry.workflow_depth.v1"
    UNKNOWN_DEPLOYMENT = "routing.registry.unknown_deployment.v1"
    INCOMPATIBLE_DEPLOYMENT = "routing.registry.incompatible_deployment.v1"
    INCOMPATIBLE_FALLBACK = "routing.registry.incompatible_fallback.v1"
    INCOMPATIBLE_REPLACEMENT = "routing.registry.incompatible_replacement.v1"
    UNSUPPORTED_STICKINESS = "routing.registry.unsupported_stickiness.v1"


class RegistryValidationError(ValueError):
    def __init__(self, code: ValidationCode, message: str) -> None:
        super().__init__(message)
        self.code = code


_RISK_RANK = {value: rank for rank, value in enumerate(RiskClass)}


def _deployment_contract_errors(
    deployment: ModelDeployment,
    profile: TaskProfile,
) -> tuple[str, ...]:
    errors: list[str] = []
    if deployment.lifecycle is not LifecycleState.ACTIVE:
        errors.append("lifecycle")
    if deployment.pricing is None:
        errors.append("pricing")
    if profile.execution_mode not in deployment.supported_modes:
        errors.append("execution_mode")
    if not profile.required_capabilities.issubset(deployment.capabilities):
        errors.append("capabilities")
    if any(
        conflict.issubset(profile.required_capabilities)
        for conflict in deployment.capability_conflicts
    ):
        errors.append("capability_conflict")
    if profile.max_input_tokens + profile.max_output_tokens > deployment.context_tokens:
        errors.append("context")
    if profile.max_output_tokens > deployment.max_output_tokens:
        errors.append("output")
    if profile.data_classification not in deployment.allowed_data_classes:
        errors.append("data")
    if _RISK_RANK[profile.risk] > _RISK_RANK[deployment.max_risk]:
        errors.append("risk")
    if profile.key not in deployment.evaluated_tasks:
        errors.append("task_evaluation")
    if deployment.quality_tier < profile.min_quality_tier:
        errors.append("quality")
    return tuple(errors)


def validate_workflow_graph(task_registry: TaskRegistry, *, max_depth: int = 8) -> None:
    if max_depth <= 0:
        raise ValueError("max_depth must be positive")
    graph = {
        workflow.key: workflow.child_workflows for workflow in task_registry.workflows
    }
    for parent, children in graph.items():
        unknown = [child for child in children if child not in graph]
        if unknown:
            raise RegistryValidationError(
                ValidationCode.UNKNOWN_WORKFLOW,
                f"workflow {parent.value} references unknown children: "
                + ", ".join(child.value for child in unknown),
            )

    visiting: list[WorkflowKey] = []
    depths: dict[WorkflowKey, int] = {}

    def visit(node: WorkflowKey) -> int:
        if node in visiting:
            start = visiting.index(node)
            cycle = (*visiting[start:], node)
            raise RegistryValidationError(
                ValidationCode.WORKFLOW_CYCLE,
                "workflow cycle: " + " -> ".join(item.value for item in cycle),
            )
        if node in depths:
            return depths[node]
        visiting.append(node)
        child_depth = max((visit(child) for child in graph[node]), default=0)
        visiting.pop()
        depth = child_depth + 1
        if depth > max_depth:
            raise RegistryValidationError(
                ValidationCode.WORKFLOW_DEPTH,
                f"workflow graph exceeds maximum routing depth {max_depth} at {node.value}",
            )
        depths[node] = depth
        return depth

    for workflow in sorted(graph, key=lambda item: item.value):
        visit(workflow)


def _validate_replacement_closure(model_registry: ModelRegistry) -> None:
    for source in model_registry.deployments:
        replacement_id = source.replacement_deployment_id
        if replacement_id is None:
            continue
        replacement = model_registry.get(replacement_id)
        if replacement is None:  # ModelRegistry normally catches this first.
            raise RegistryValidationError(
                ValidationCode.UNKNOWN_DEPLOYMENT,
                f"unknown replacement {replacement_id} for {source.deployment_id}",
            )
        failures: list[str] = []
        if (
            replacement.lifecycle is not LifecycleState.ACTIVE
            or replacement.pricing is None
        ):
            failures.append("active_priced")
        if not source.supported_modes.issubset(replacement.supported_modes):
            failures.append("execution_modes")
        if source.transport_contract != replacement.transport_contract:
            failures.append("transport_contract")
        if not source.capabilities.issubset(replacement.capabilities):
            failures.append("capabilities")
        if not set(replacement.capability_conflicts).issubset(
            source.capability_conflicts
        ):
            failures.append("capability_conflicts")
        if source.context_tokens > replacement.context_tokens:
            failures.append("context")
        if source.max_output_tokens > replacement.max_output_tokens:
            failures.append("output")
        if not source.allowed_data_classes.issubset(replacement.allowed_data_classes):
            failures.append("data")
        if not source.regions.issubset(replacement.regions):
            failures.append("regions")
        if source.retention_policy != replacement.retention_policy:
            failures.append("retention_policy")
        if source.credential_strategy != replacement.credential_strategy:
            failures.append("credential_strategy")
        if _RISK_RANK[source.max_risk] > _RISK_RANK[replacement.max_risk]:
            failures.append("risk")
        if not source.evaluated_tasks.issubset(replacement.evaluated_tasks):
            failures.append("task_evaluation")
        if source.quality_tier > replacement.quality_tier:
            failures.append("quality")
        if failures:
            raise RegistryValidationError(
                ValidationCode.INCOMPATIBLE_REPLACEMENT,
                f"replacement {replacement.deployment_id} is not contract-compatible with "
                f"{source.deployment_id}: {', '.join(failures)}",
            )


def validate_control_plane(
    model_registry: ModelRegistry,
    task_registry: TaskRegistry,
    *,
    max_workflow_depth: int = 8,
) -> None:
    """Validate complete primary/fallback/replacement closure before serving."""

    validate_workflow_graph(task_registry, max_depth=max_workflow_depth)
    _validate_replacement_closure(model_registry)

    for profile in task_registry.profiles:
        if profile.stickiness is not RouteStickiness.INVOCATION:
            raise RegistryValidationError(
                ValidationCode.UNSUPPORTED_STICKINESS,
                f"task {profile.key.value} uses unsupported stickiness "
                f"{profile.stickiness.value!r}",
            )
        if not profile.require_same_transport_fallback:
            raise RegistryValidationError(
                ValidationCode.INCOMPATIBLE_FALLBACK,
                f"task {profile.key.value} disables same-transport fallback, "
                "but cross-transport retries are not supported by this gateway",
            )
        route_ids = (
            *profile.candidate_deployment_ids,
            *profile.fallback_deployment_ids,
        )
        resolved: list[ModelDeployment] = []
        for deployment_id in route_ids:
            deployment = model_registry.get(deployment_id)
            if deployment is None:
                raise RegistryValidationError(
                    ValidationCode.UNKNOWN_DEPLOYMENT,
                    f"task {profile.key.value} references unknown deployment {deployment_id}",
                )
            errors = _deployment_contract_errors(deployment, profile)
            if errors:
                raise RegistryValidationError(
                    ValidationCode.INCOMPATIBLE_DEPLOYMENT,
                    f"deployment {deployment_id} violates {profile.key.value}: "
                    + ", ".join(errors),
                )
            resolved.append(deployment)

        if profile.fallback_deployment_ids:
            contracts = {deployment.transport_contract for deployment in resolved}
            if len(contracts) != 1:
                raise RegistryValidationError(
                    ValidationCode.INCOMPATIBLE_FALLBACK,
                    f"task {profile.key.value} fallback chain crosses transport contracts",
                )

        if profile.fallback_deployment_ids:
            if FallbackClass.REGISTERED_REPLACEMENT not in profile.fallback_classes:
                raise RegistryValidationError(
                    ValidationCode.INCOMPATIBLE_FALLBACK,
                    f"task {profile.key.value} has deployment fallbacks without "
                    "REGISTERED_REPLACEMENT authorization",
                )
            for primary_id in profile.candidate_deployment_ids:
                current = model_registry.get(primary_id)
                assert current is not None
                for fallback_id in profile.fallback_deployment_ids:
                    if current.replacement_deployment_id != fallback_id:
                        raise RegistryValidationError(
                            ValidationCode.INCOMPATIBLE_FALLBACK,
                            f"task {profile.key.value} fallback {fallback_id} is not "
                            f"the registered replacement for {current.deployment_id}",
                        )
                    next_deployment = model_registry.get(fallback_id)
                    assert next_deployment is not None
                    current = next_deployment
        elif FallbackClass.REGISTERED_REPLACEMENT in profile.fallback_classes:
            raise RegistryValidationError(
                ValidationCode.INCOMPATIBLE_FALLBACK,
                f"task {profile.key.value} authorizes a replacement with no "
                "fallback deployment chain",
            )
