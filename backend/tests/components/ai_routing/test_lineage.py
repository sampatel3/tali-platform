from __future__ import annotations

from uuid import uuid4

import pytest

from app.components.ai_routing import (
    RoutingAttribution,
    estimate_anthropic_messages,
    routing_scope,
)
from app.components.ai_routing.contracts import TaskKey
from app.components.ai_routing.execution import RouteExecution
from app.components.ai_routing.gateway import prepare_route


class _NoopRouteExecution(RouteExecution):
    """Route execution whose durable start is isolated from this unit test."""

    def start(self) -> "_NoopRouteExecution":
        self._started = True
        return self


def _prepare(task: TaskKey, **kwargs):
    return prepare_route(
        task,
        request_estimate=estimate_anthropic_messages(messages=[], max_tokens=1),
        **kwargs,
    )


def test_prepare_route_inherits_active_root_and_parent(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.components.ai_routing.gateway.RouteExecution",
        _NoopRouteExecution,
    )
    parent = _prepare(TaskKey.GENERAL_CHAT_ORCHESTRATION)

    with routing_scope(parent):
        child = _prepare(TaskKey.SEARCH_PARSE)

    assert child.decision.root_invocation_id == parent.decision.root_invocation_id
    assert child.decision.parent_invocation_id == parent.invocation_id
    assert child.invocation_id != parent.invocation_id


def test_explicit_lineage_is_not_replaced_by_active_scope(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.components.ai_routing.gateway.RouteExecution",
        _NoopRouteExecution,
    )
    parent = _prepare(TaskKey.GENERAL_CHAT_ORCHESTRATION)
    explicit_root = str(uuid4())
    explicit_parent = str(uuid4())

    with routing_scope(parent):
        child = _prepare(
            TaskKey.SEARCH_PARSE,
            root_invocation_id=explicit_root,
            parent_invocation_id=explicit_parent,
        )

    assert child.decision.root_invocation_id == explicit_root
    assert child.decision.parent_invocation_id == explicit_parent


def test_scope_resets_after_exit(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.components.ai_routing.gateway.RouteExecution",
        _NoopRouteExecution,
    )
    parent = _prepare(TaskKey.GENERAL_CHAT_ORCHESTRATION)

    with routing_scope(parent):
        _prepare(TaskKey.SEARCH_PARSE)
    sibling = _prepare(TaskKey.SEARCH_PARSE)

    assert sibling.decision.parent_invocation_id is None
    assert sibling.decision.root_invocation_id == sibling.invocation_id


def test_child_inherits_parent_role_authority_minimum(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.components.ai_routing.gateway.RouteExecution",
        _NoopRouteExecution,
    )
    parent = _prepare(TaskKey.AUTONOMOUS_RECRUITING_ORCHESTRATION)

    with routing_scope(parent):
        child = _prepare(
            TaskKey.SEARCH_RERANK,
            # An explicit false cannot weaken the active parent contract.
            require_role_authority=False,
        )

    assert parent.decision.require_role_authority is True
    assert child.request.require_role_authority is True
    assert child.decision.require_role_authority is True


def test_child_preserves_active_parent_user_attribution(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.components.ai_routing.gateway.RouteExecution",
        _NoopRouteExecution,
    )
    parent = _prepare(
        TaskKey.GENERAL_CHAT_ORCHESTRATION,
        attribution=RoutingAttribution(organization_id=7, user_id=41),
    )

    with routing_scope(parent):
        child = _prepare(
            TaskKey.SEARCH_PARSE,
            attribution=RoutingAttribution(organization_id=7),
        )

    assert child.attribution.user_id == 41

    with routing_scope(parent), pytest.raises(
        ValueError,
        match="child routing attribution user does not match its active parent",
    ):
        _prepare(
            TaskKey.SEARCH_PARSE,
            attribution=RoutingAttribution(organization_id=7, user_id=99),
        )
