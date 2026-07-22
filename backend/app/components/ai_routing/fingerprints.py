"""Stable, content-free identities for routing behavior contracts."""

from __future__ import annotations

import hashlib
import json

from .contracts import Capability, DataClassification, RiskClass, TaskProfile


def decision_behavior_fingerprint(
    *,
    policy_version: str,
    registry_version: str,
    task_registry_version: str,
    profile: TaskProfile,
    required_capabilities: frozenset[Capability],
    risk: RiskClass,
    data_classification: DataClassification,
    region: str,
    attempt_ids: tuple[str, ...],
    require_role_authority: bool,
) -> str:
    """Hash every behavior-affecting route contract field, never prompt content."""

    material = {
        "policy_version": policy_version,
        "registry_version": registry_version,
        "task_registry_version": task_registry_version,
        "profile_version": profile.profile_version,
        "semantic_revision": profile.semantic_revision,
        "schema_revision": profile.schema_revision,
        "prompt_revision": profile.prompt_revision,
        "tool_revision": profile.tool_revision,
        "execution_mode": profile.execution_mode.value,
        "required_capabilities": sorted(
            capability.value for capability in required_capabilities
        ),
        "request_shape": {
            "require_tools": profile.request_shape.require_tools,
            "require_forced_tool_choice": (
                profile.request_shape.require_forced_tool_choice
            ),
            "require_citations_document": (
                profile.request_shape.require_citations_document
            ),
        },
        "risk": risk.value,
        "data_classification": data_classification.value,
        "require_role_authority": require_role_authority,
        "region": region,
        "attempt_deployment_ids": list(attempt_ids),
        "fallback_classes": sorted(value.value for value in profile.fallback_classes),
        "max_input_tokens": profile.max_input_tokens,
        "max_output_tokens": profile.max_output_tokens,
        "max_iterations": profile.max_iterations,
        "max_attempts_per_iteration": profile.max_attempts_per_iteration,
        "retry_backoff_base_ms": profile.retry_backoff_base_ms,
        "retry_backoff_max_ms": profile.retry_backoff_max_ms,
        "latency_slo_ms": profile.latency_slo_ms,
        "max_cost_micro_usd": profile.max_cost_micro_usd,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()[:24]


__all__ = ["decision_behavior_fingerprint"]
