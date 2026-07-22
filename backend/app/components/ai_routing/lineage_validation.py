"""Durable runtime enforcement of the registered workflow graph."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ...models.ai_routing import AIRoutingInvocation
from .contracts import RouteDecision, WorkflowKey
from .execution_types import RoutingAttribution
from .task_registry import DEFAULT_TASK_REGISTRY, TaskRegistry


class RoutingLineageError(ValueError):
    """A requested parent/child edge is absent or outside the graph contract."""


def validate_runtime_lineage(
    session: Session,
    decision: RouteDecision,
    *,
    attribution: RoutingAttribution,
    task_registry: TaskRegistry = DEFAULT_TASK_REGISTRY,
    max_depth: int = 8,
) -> None:
    """Validate an invocation's durable parent edge and ancestor depth."""

    parent_id = decision.parent_invocation_id
    if parent_id is None:
        return
    parent = session.get(AIRoutingInvocation, parent_id)
    if parent is None:
        raise RoutingLineageError(f"unknown parent invocation: {parent_id}")
    if parent.status != "running":
        raise RoutingLineageError("parent routing invocation is not running")
    if decision.root_invocation_id != str(parent.root_invocation_id):
        raise RoutingLineageError("child root does not match its durable parent")
    if attribution.organization_id != parent.organization_id:
        raise RoutingLineageError("child organization does not match its parent")
    if parent.role_id is not None and attribution.role_id != parent.role_id:
        raise RoutingLineageError("child role does not match its parent")
    if parent.user_id is not None and attribution.user_id != parent.user_id:
        raise RoutingLineageError("child user does not match its parent")
    try:
        parent_workflow = WorkflowKey(parent.workflow)
    except ValueError as exc:
        raise RoutingLineageError(
            f"parent has unregistered workflow: {parent.workflow!r}"
        ) from exc
    definition = task_registry.workflow(parent_workflow)
    if definition is None or decision.workflow not in definition.child_workflows:
        raise RoutingLineageError(
            f"workflow {decision.workflow.value!r} is not an allowed child of "
            f"{parent_workflow.value!r}"
        )

    seen = {decision.invocation_id}
    cursor = parent
    node_count = 1  # the new child
    while cursor is not None:
        cursor_id = str(cursor.invocation_id)
        if cursor_id in seen:
            raise RoutingLineageError("routing invocation lineage contains a cycle")
        seen.add(cursor_id)
        if cursor.organization_id != parent.organization_id:
            raise RoutingLineageError("routing lineage crosses an organization boundary")
        node_count += 1
        if node_count > max_depth:
            raise RoutingLineageError(
                f"routing invocation exceeds maximum workflow depth {max_depth}"
            )
        cursor = (
            session.get(AIRoutingInvocation, cursor.parent_invocation_id)
            if cursor.parent_invocation_id is not None
            else None
        )


__all__ = ["RoutingLineageError", "validate_runtime_lineage"]
