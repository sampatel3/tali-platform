"""Application entrypoint from a typed task to a durable route execution."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .anthropic_estimation import AnthropicRequestEstimate
from .contracts import TaskKey
from .execution import RouteExecution, RoutingAttribution
from .lifecycle_scope import track_route
from .lineage import current_route, inherited_lineage
from .policy import DEFAULT_ROUTING_POLICY, RoutingPolicy
from .runtime import build_route_request


def prepare_route(
    task: TaskKey,
    *,
    request_estimate: AnthropicRequestEstimate,
    attribution: RoutingAttribution | None = None,
    operation: str | None = None,
    policy: RoutingPolicy = DEFAULT_ROUTING_POLICY,
    **request_kwargs: Any,
) -> RouteExecution:
    """Plan, validate, and durably start one logical AI invocation.

    Deterministic short-circuits should happen before calling this function.
    Once it returns, provider adapters may start physical attempts, but feature
    code still owns the surrounding workflow and its terminal outcome.
    """

    # Explicit lineage always wins. Otherwise, tool work dispatched inside a
    # routing scope becomes a child of that workflow invocation automatically.
    active_parent = current_route()
    resolved_attribution = attribution or RoutingAttribution()
    if active_parent is not None and active_parent.attribution.user_id is not None:
        parent_user_id = int(active_parent.attribution.user_id)
        if resolved_attribution.user_id is None:
            resolved_attribution = replace(
                resolved_attribution,
                user_id=parent_user_id,
            )
        elif int(resolved_attribution.user_id) != parent_user_id:
            raise ValueError(
                "child routing attribution user does not match its active parent"
            )
    if (
        "root_invocation_id" not in request_kwargs
        and "parent_invocation_id" not in request_kwargs
    ):
        lineage = inherited_lineage()
        if lineage is not None:
            root_invocation_id, parent_invocation_id = lineage
            request_kwargs = {
                **request_kwargs,
                "root_invocation_id": root_invocation_id,
                "parent_invocation_id": parent_invocation_id,
            }

    # An active workflow may strengthen admission for every provider call it
    # dispatches. Child feature code cannot accidentally weaken that minimum by
    # omitting (or explicitly passing false for) its own request constraint.
    if active_parent is not None and active_parent.decision.require_role_authority:
        request_kwargs = {**request_kwargs, "require_role_authority": True}

    forbidden = {
        "estimated_input_tokens",
        "estimated_output_tokens",
        "estimated_input_cost_basis",
    }.intersection(request_kwargs)
    if forbidden:
        raise TypeError(
            "prepare_route estimates are adapter-derived; do not pass "
            + ", ".join(sorted(forbidden))
        )
    request = build_route_request(
        task,
        estimated_input_tokens=request_estimate.input_tokens,
        estimated_output_tokens=request_estimate.output_tokens,
        estimated_input_cost_basis=request_estimate.input_cost_basis,
        **request_kwargs,
    )
    decision = policy.plan(request)
    execution = RouteExecution(
        request=request,
        decision=decision,
        attribution=resolved_attribution,
        operation=operation,
        registry=policy.model_registry,
        task_registry=policy.task_registry,
    ).start()
    track_route(execution)
    return execution


__all__ = ["prepare_route"]
