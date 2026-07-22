"""JSON-safe, content-free snapshots of routing inputs and decisions.

Routing telemetry is an operational control-plane record, not a prompt log.
These explicit serializers intentionally copy only routing constraints,
versions, candidates, and reason codes.  Adding a new provider request field to
the dataclasses therefore cannot accidentally persist messages, CV text, or
other hiring content.
"""

from __future__ import annotations

from typing import Any

from .contracts import RouteDecision, RouteRequest


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _sorted_values(values: Any) -> list[str] | None:
    if values is None:
        return None
    return sorted(str(_value(value)) for value in values)


def request_snapshot(request: RouteRequest) -> dict[str, Any]:
    """Return the approved telemetry projection for a route request."""

    return {
        "task": request.task.value,
        "estimated_input_tokens": int(request.estimated_input_tokens),
        "estimated_output_tokens": int(request.estimated_output_tokens),
        "estimated_input_cost_basis": request.estimated_input_cost_basis.value,
        "root_invocation_id": request.root_invocation_id,
        "parent_invocation_id": request.parent_invocation_id,
        "override_alias": request.override_alias,
        "pinned_deployment_id": request.pinned_deployment_id,
        "additional_capabilities": _sorted_values(request.additional_capabilities),
        "data_classification": (
            request.data_classification.value
            if request.data_classification is not None
            else None
        ),
        "risk": request.risk.value if request.risk is not None else None,
        "region": request.region,
        "provider_allowlist": _sorted_values(request.provider_allowlist),
        "provider_denylist": _sorted_values(request.provider_denylist),
        "tenant_allowed_deployments": _sorted_values(
            request.tenant_allowed_deployments
        ),
        "tenant_blocked_deployments": _sorted_values(
            request.tenant_blocked_deployments
        ),
        "max_cost_micro_usd": request.max_cost_micro_usd,
        "require_role_authority": request.require_role_authority,
    }


def decision_snapshot(decision: RouteDecision) -> dict[str, Any]:
    """Return the approved telemetry projection for an immutable decision."""

    return {
        "route_id": decision.route_id,
        "behavior_fingerprint": decision.behavior_fingerprint,
        "workflow": decision.workflow.value,
        "task": decision.task.value,
        "execution_mode": decision.execution_mode.value,
        "required_capabilities": _sorted_values(decision.required_capabilities),
        "request_shape": {
            "require_tools": decision.request_shape.require_tools,
            "require_forced_tool_choice": (
                decision.request_shape.require_forced_tool_choice
            ),
            "require_citations_document": (
                decision.request_shape.require_citations_document
            ),
        },
        "risk": decision.risk.value,
        "data_classification": decision.data_classification.value,
        "registry_version": decision.registry_version,
        "task_registry_version": decision.task_registry_version,
        "policy_version": decision.policy_version,
        "profile_version": decision.profile_version,
        "semantic_revision": decision.semantic_revision,
        "schema_revision": decision.schema_revision,
        "prompt_revision": decision.prompt_revision,
        "tool_revision": decision.tool_revision,
        "feature": decision.feature,
        "require_role_authority": decision.require_role_authority,
        "selected_deployment_id": decision.selected_deployment_id,
        "selected_model_id": decision.selected_model_id,
        "eligible_deployments": [
            {
                "deployment_id": item.deployment_id,
                "model_id": item.model_id,
                "provider": item.provider,
                "expected_cost_micro_usd": item.expected_cost_micro_usd,
                "latency_rank": item.latency_rank,
            }
            for item in decision.eligible_deployments
        ],
        "exclusions": [
            {
                "deployment_id": item.deployment_id,
                "codes": [code.value for code in item.codes],
            }
            for item in decision.exclusions
        ],
        "attempts": [
            {
                "ordinal": item.ordinal,
                "deployment_id": item.deployment_id,
                "model_id": item.model_id,
                "expected_cost_micro_usd": item.expected_cost_micro_usd,
                "reason": item.reason.value,
            }
            for item in decision.attempts
        ],
        "limits": {
            "max_input_tokens": decision.limits.max_input_tokens,
            "max_output_tokens": decision.limits.max_output_tokens,
            "max_iterations": decision.limits.max_iterations,
            "max_attempts_per_iteration": (
                decision.limits.max_attempts_per_iteration
            ),
            "retry_backoff_base_ms": decision.limits.retry_backoff_base_ms,
            "retry_backoff_max_ms": decision.limits.retry_backoff_max_ms,
            "latency_slo_ms": decision.limits.latency_slo_ms,
            "max_cost_micro_usd": decision.limits.max_cost_micro_usd,
        },
        "fallback_classes": _sorted_values(decision.fallback_classes),
        "reason_codes": [code.value for code in decision.reason_codes],
        "stickiness": decision.stickiness.value,
        "pin_key": decision.pin_key,
    }


__all__ = ["decision_snapshot", "request_snapshot"]
