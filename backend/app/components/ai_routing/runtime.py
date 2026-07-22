"""Runtime bridge from legacy configuration to the pure routing policy.

Feature code enters routing through this module with a typed :class:`TaskKey`.
Configuration lookup and invocation-id creation live here so the policy remains
deterministic and side-effect free.  Provider execution and persistence belong
to transport adapters, not this facade.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol
from uuid import UUID, uuid4, uuid5

from app.platform.config import settings

from .contracts import (
    Capability,
    DataClassification,
    InputCostBasis,
    RiskClass,
    RouteDecision,
    RouteRequest,
    TaskKey,
)
from .policy import DEFAULT_ROUTING_POLICY, RoutingPolicy
from .model_registry import (
    ANTHROPIC_HAIKU_4_5,
    ANTHROPIC_SONNET_4_5,
    ANTHROPIC_SONNET_4_6,
)


class RoutingRuntimeConfigurationError(ValueError):
    """Raised when centralized routing configuration is malformed."""


class RoutingSettings(Protocol):
    """The small Settings surface needed by the compatibility bridge."""

    AI_ROUTER_MODEL_OVERRIDES_JSON: str

    @property
    def resolved_claude_model(self) -> str: ...

    @property
    def resolved_agent_autonomous_model(self) -> str: ...


_LEGACY_ENV_BY_TASK: Mapping[TaskKey, str] = {
    TaskKey.SEARCH_PARSE: "CLAUDE_SEARCH_PARSER_MODEL",
    TaskKey.SEARCH_GROUNDING: "CLAUDE_GROUNDING_MODEL",
}
_GENERAL_MODEL_TASKS = frozenset(
    {
        TaskKey.GENERAL_CHAT_ORCHESTRATION,
        TaskKey.ROLE_CHAT_ORCHESTRATION,
    }
)
# Per-role model values are durable data, so they need an explicit policy just
# like environment and task overrides. New registered deployments do not
# silently become selectable by an old role row until this policy is reviewed.
ROLE_MODEL_DEPLOYMENT_POLICY: Mapping[TaskKey, tuple[str, ...]] = MappingProxyType(
    {
        TaskKey.AUTONOMOUS_RECRUITING_ORCHESTRATION: (
            ANTHROPIC_HAIKU_4_5,
            ANTHROPIC_SONNET_4_5,
            ANTHROPIC_SONNET_4_6,
        )
    }
)
_BEHAVIOR_INVOCATION_NAMESPACE = UUID("d026a94e-04c5-59c6-a598-ae96d72b93e6")


def _require_task(task: TaskKey) -> TaskKey:
    if not isinstance(task, TaskKey):
        raise TypeError("task must be a TaskKey")
    return task


def _optional_nonempty_string(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RoutingRuntimeConfigurationError(f"{label} must be a string")
    cleaned = value.strip()
    return cleaned or None


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RoutingRuntimeConfigurationError(
                f"AI_ROUTER_MODEL_OVERRIDES_JSON contains duplicate task key {key!r}"
            )
        result[key] = value
    return result


def _configured_overrides(settings_obj: RoutingSettings) -> dict[TaskKey, str]:
    raw = getattr(settings_obj, "AI_ROUTER_MODEL_OVERRIDES_JSON", "")
    if not isinstance(raw, str):
        raise RoutingRuntimeConfigurationError(
            "AI_ROUTER_MODEL_OVERRIDES_JSON must be a JSON string"
        )
    if not raw.strip():
        return {}

    try:
        decoded = json.loads(raw, object_pairs_hook=_unique_json_object)
    except RoutingRuntimeConfigurationError:
        raise
    except (json.JSONDecodeError, TypeError) as exc:
        raise RoutingRuntimeConfigurationError(
            "AI_ROUTER_MODEL_OVERRIDES_JSON must be a valid JSON object"
        ) from exc

    if not isinstance(decoded, dict):
        raise RoutingRuntimeConfigurationError(
            "AI_ROUTER_MODEL_OVERRIDES_JSON must be a JSON object"
        )

    overrides: dict[TaskKey, str] = {}
    for raw_task, raw_identifier in decoded.items():
        try:
            task = TaskKey(raw_task)
        except (TypeError, ValueError) as exc:
            raise RoutingRuntimeConfigurationError(
                f"unknown AI routing task key in override configuration: {raw_task!r}"
            ) from exc
        identifier = _optional_nonempty_string(
            raw_identifier,
            label=f"model override for {task.value}",
        )
        if identifier is None:
            raise RoutingRuntimeConfigurationError(
                f"model override for {task.value} must be non-empty"
            )
        overrides[task] = identifier
    return overrides


def legacy_override_for_task(
    task: TaskKey,
    *,
    settings_obj: RoutingSettings = settings,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return the pre-router model selector for a phase-one parity task.

    A missing legacy search variable intentionally returns ``None`` so the
    registered task profile owns its pinned default.  Reserved future task keys
    also return ``None`` and remain non-executable until a profile is added.
    """

    task = _require_task(task)
    if task in _GENERAL_MODEL_TASKS:
        return _optional_nonempty_string(
            settings_obj.resolved_claude_model,
            label="resolved CLAUDE_MODEL",
        )
    if task is TaskKey.AUTONOMOUS_RECRUITING_ORCHESTRATION:
        return _optional_nonempty_string(
            settings_obj.resolved_agent_autonomous_model,
            label="resolved CLAUDE_AGENT_AUTONOMOUS_MODEL",
        )

    env_key = _LEGACY_ENV_BY_TASK.get(task)
    if env_key is not None:
        source = os.environ if environ is None else environ
        return _optional_nonempty_string(source.get(env_key), label=env_key)
    return None


def _selected_override(
    task: TaskKey,
    *,
    explicit_model_override: str | None,
    settings_obj: RoutingSettings,
    environ: Mapping[str, str] | None,
) -> str | None:
    # Parse and validate the complete centralized map even when an explicit
    # value wins. A malformed deployment-wide policy must never be silently
    # masked by one call site's higher-precedence override.
    configured = _configured_overrides(settings_obj)
    explicit = _optional_nonempty_string(
        explicit_model_override,
        label=f"explicit model override for {task.value}",
    )
    if explicit is not None:
        return explicit
    if task in configured:
        return configured[task]
    return legacy_override_for_task(task, settings_obj=settings_obj, environ=environ)


def _optional_invocation_id(value: str | None, *, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string when provided")
    return value.strip()


def build_route_request(
    task: TaskKey,
    *,
    explicit_model_override: str | None = None,
    settings_obj: RoutingSettings = settings,
    environ: Mapping[str, str] | None = None,
    invocation_id: str | None = None,
    root_invocation_id: str | None = None,
    parent_invocation_id: str | None = None,
    pinned_deployment_id: str | None = None,
    estimated_input_tokens: int = 0,
    estimated_output_tokens: int = 0,
    estimated_input_cost_basis: InputCostBasis = InputCostBasis.STANDARD,
    additional_capabilities: frozenset[Capability] = frozenset(),
    data_classification: DataClassification | None = None,
    risk: RiskClass | None = None,
    region: str | None = None,
    provider_allowlist: frozenset[str] | None = None,
    provider_denylist: frozenset[str] = frozenset(),
    tenant_allowed_deployments: frozenset[str] | None = None,
    tenant_blocked_deployments: frozenset[str] = frozenset(),
    max_cost_micro_usd: int | None = None,
    require_role_authority: bool = False,
) -> RouteRequest:
    """Build one immutable request, generating lineage only when omitted."""

    task = _require_task(task)
    resolved_invocation_id = _optional_invocation_id(
        invocation_id,
        label="invocation_id",
    ) or str(uuid4())
    resolved_root_id = (
        _optional_invocation_id(
            root_invocation_id,
            label="root_invocation_id",
        )
        or resolved_invocation_id
    )

    return RouteRequest(
        task=task,
        invocation_id=resolved_invocation_id,
        root_invocation_id=resolved_root_id,
        parent_invocation_id=_optional_invocation_id(
            parent_invocation_id,
            label="parent_invocation_id",
        ),
        override_alias=_selected_override(
            task,
            explicit_model_override=explicit_model_override,
            settings_obj=settings_obj,
            environ=environ,
        ),
        pinned_deployment_id=_optional_invocation_id(
            pinned_deployment_id,
            label="pinned_deployment_id",
        ),
        estimated_input_tokens=estimated_input_tokens,
        estimated_output_tokens=estimated_output_tokens,
        estimated_input_cost_basis=estimated_input_cost_basis,
        additional_capabilities=additional_capabilities,
        data_classification=data_classification,
        risk=risk,
        region=region,
        provider_allowlist=provider_allowlist,
        provider_denylist=provider_denylist,
        tenant_allowed_deployments=tenant_allowed_deployments,
        tenant_blocked_deployments=tenant_blocked_deployments,
        max_cost_micro_usd=max_cost_micro_usd,
        require_role_authority=require_role_authority,
    )


def plan_route(
    task: TaskKey,
    *,
    policy: RoutingPolicy = DEFAULT_ROUTING_POLICY,
    explicit_model_override: str | None = None,
    settings_obj: RoutingSettings = settings,
    environ: Mapping[str, str] | None = None,
    invocation_id: str | None = None,
    root_invocation_id: str | None = None,
    parent_invocation_id: str | None = None,
    pinned_deployment_id: str | None = None,
    estimated_input_tokens: int = 0,
    estimated_output_tokens: int = 0,
    estimated_input_cost_basis: InputCostBasis = InputCostBasis.STANDARD,
    additional_capabilities: frozenset[Capability] = frozenset(),
    data_classification: DataClassification | None = None,
    risk: RiskClass | None = None,
    region: str | None = None,
    provider_allowlist: frozenset[str] | None = None,
    provider_denylist: frozenset[str] = frozenset(),
    tenant_allowed_deployments: frozenset[str] | None = None,
    tenant_blocked_deployments: frozenset[str] = frozenset(),
    max_cost_micro_usd: int | None = None,
    require_role_authority: bool = False,
) -> RouteDecision:
    """Resolve configuration and return the pure policy's immutable decision."""

    request = build_route_request(
        task,
        explicit_model_override=explicit_model_override,
        settings_obj=settings_obj,
        environ=environ,
        invocation_id=invocation_id,
        root_invocation_id=root_invocation_id,
        parent_invocation_id=parent_invocation_id,
        pinned_deployment_id=pinned_deployment_id,
        estimated_input_tokens=estimated_input_tokens,
        estimated_output_tokens=estimated_output_tokens,
        estimated_input_cost_basis=estimated_input_cost_basis,
        additional_capabilities=additional_capabilities,
        data_classification=data_classification,
        risk=risk,
        region=region,
        provider_allowlist=provider_allowlist,
        provider_denylist=provider_denylist,
        tenant_allowed_deployments=tenant_allowed_deployments,
        tenant_blocked_deployments=tenant_blocked_deployments,
        max_cost_micro_usd=max_cost_micro_usd,
        require_role_authority=require_role_authority,
    )
    return policy.plan(request)


def route_behavior_fingerprint(
    task: TaskKey,
    settings_obj: RoutingSettings = settings,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return the route behavior identity suitable for semantic cache keys."""

    task = _require_task(task)
    decision = plan_route(
        task,
        settings_obj=settings_obj,
        environ=environ,
        invocation_id=str(uuid5(_BEHAVIOR_INVOCATION_NAMESPACE, task.value)),
    )
    return decision.behavior_fingerprint


def _validate_task_selector(
    task: TaskKey,
    identifier: str,
    *,
    source: str,
    policy: RoutingPolicy,
) -> None:
    """Validate one selector against the complete task route contract."""

    try:
        request = RouteRequest(
            task=task,
            invocation_id=str(
                uuid5(
                    _BEHAVIOR_INVOCATION_NAMESPACE,
                    f"startup:{source}:{task.value}:{identifier}",
                )
            ),
            override_alias=identifier,
        )
        policy.plan(request)
    except Exception as exc:
        raise RoutingRuntimeConfigurationError(
            f"invalid {source} for {task.value}: {exc}"
        ) from exc


def validated_role_model_override(
    task: TaskKey,
    identifier: str | None,
    *,
    policy: RoutingPolicy = DEFAULT_ROUTING_POLICY,
) -> str | None:
    """Return a role selector only when its finite task policy authorizes it."""

    task = _require_task(task)
    cleaned = _optional_nonempty_string(
        identifier,
        label=f"role model override for {task.value}",
    )
    if cleaned is None:
        return None
    allowed = ROLE_MODEL_DEPLOYMENT_POLICY.get(task)
    if allowed is None:
        raise RoutingRuntimeConfigurationError(
            f"task {task.value} does not permit role model overrides"
        )
    try:
        deployment = policy.model_registry.resolve(cleaned)
    except Exception as exc:
        raise RoutingRuntimeConfigurationError(
            f"invalid role model override for {task.value}: {exc}"
        ) from exc
    if deployment.deployment_id not in allowed:
        raise RoutingRuntimeConfigurationError(
            f"deployment {deployment.deployment_id!r} is not authorized by the "
            f"role model policy for {task.value}"
        )
    _validate_task_selector(
        task,
        cleaned,
        source="role model override",
        policy=policy,
    )
    return cleaned


def validate_configured_overrides(
    settings_obj: RoutingSettings,
    *,
    policy: RoutingPolicy = DEFAULT_ROUTING_POLICY,
) -> None:
    """Validate the complete centralized override map before serving traffic."""

    for task, identifier in _configured_overrides(settings_obj).items():
        _validate_task_selector(
            task,
            identifier,
            source="AI router override",
            policy=policy,
        )


def validate_routing_configuration(
    settings_obj: RoutingSettings,
    *,
    environ: Mapping[str, str] | None = None,
    policy: RoutingPolicy = DEFAULT_ROUTING_POLICY,
) -> None:
    """Validate every model selector that can affect a migrated task.

    This deliberately checks the underlying legacy selectors even when a
    centralized override currently masks one. Removing an override must not
    reveal a latent, unauthorized provider model on the next process start.
    Durable per-role values are validated when read; their finite deployment
    policy is also checked here so registry/profile drift fails at boot.
    """

    from .transport_registry import DEFAULT_TRANSPORT_ADAPTER_REGISTRY

    DEFAULT_TRANSPORT_ADAPTER_REGISTRY.validate_control_plane(
        policy.model_registry,
        policy.task_registry,
    )
    validate_configured_overrides(settings_obj, policy=policy)
    for profile in policy.task_registry.profiles:
        identifier = legacy_override_for_task(
            profile.key,
            settings_obj=settings_obj,
            environ=environ,
        )
        if identifier is not None:
            _validate_task_selector(
                profile.key,
                identifier,
                source="legacy model selector",
                policy=policy,
            )

    for task, deployment_ids in ROLE_MODEL_DEPLOYMENT_POLICY.items():
        for deployment_id in deployment_ids:
            _validate_task_selector(
                task,
                deployment_id,
                source="role model policy deployment",
                policy=policy,
            )


__all__ = [
    "RoutingRuntimeConfigurationError",
    "build_route_request",
    "legacy_override_for_task",
    "plan_route",
    "route_behavior_fingerprint",
    "validate_configured_overrides",
    "validate_routing_configuration",
    "validated_role_model_override",
    "ROLE_MODEL_DEPLOYMENT_POLICY",
]
