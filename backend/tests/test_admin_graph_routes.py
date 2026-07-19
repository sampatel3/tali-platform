"""Admin Graphiti diagnostics require explicit bounded tenant input."""

from __future__ import annotations

from contextlib import nullcontext
import sys
from urllib.parse import urlencode
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.candidate_graph import admin_routes
from app.candidate_graph import admin_operations
from app.candidate_graph.admin_operations import (
    attributed_admin_graph_call,
    require_admin_graph_organization,
)
from app.services.metered_async_anthropic_client import graph_metering_ctx


def _request(**query) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/graphiti/debug",
            "headers": [],
            "query_string": urlencode(query).encode(),
        }
    )


@pytest.mark.parametrize(
    "handler",
    [admin_routes.search_debug_response, admin_routes.cypher_debug_response],
)
def test_graph_debug_routes_require_explicit_query_before_provider(monkeypatch, handler):
    provider_checks: list[bool] = []
    monkeypatch.setattr(
        admin_routes.graph_client,
        "is_configured",
        lambda: provider_checks.append(True) or True,
    )

    with pytest.raises(HTTPException) as error:
        handler(_request(org_id=1))

    assert error.value.status_code == 400
    assert error.value.detail == "q is required"
    assert provider_checks == []


@pytest.mark.parametrize(
    "handler",
    [admin_routes.search_debug_response, admin_routes.cypher_debug_response],
)
def test_graph_debug_routes_reject_raw_padded_oversize_before_provider(
    monkeypatch,
    handler,
):
    provider_checks: list[bool] = []
    monkeypatch.setattr(
        admin_routes.graph_client,
        "is_configured",
        lambda: provider_checks.append(True) or True,
    )

    with pytest.raises(HTTPException) as error:
        handler(_request(org_id=1, q=" " * 100_000 + "x"))

    assert error.value.status_code == 400
    assert "at most" in str(error.value.detail)
    assert provider_checks == []


@pytest.mark.parametrize(
    "handler",
    [admin_routes.search_debug_response, admin_routes.cypher_debug_response],
)
def test_graph_debug_routes_reject_blank_query_before_provider(
    monkeypatch,
    handler,
):
    provider_checks: list[bool] = []
    monkeypatch.setattr(
        admin_routes.graph_client,
        "is_configured",
        lambda: provider_checks.append(True) or True,
    )

    with pytest.raises(HTTPException) as error:
        handler(_request(org_id=1, q="   "))

    assert error.value.status_code == 400
    assert error.value.detail == "candidate search query must be non-empty"
    assert provider_checks == []


@pytest.mark.parametrize(
    "handler,query,detail,error_code",
    [
        (
            admin_routes.search_debug_response,
            {"q": "probe"},
            "Graph search is temporarily unavailable",
            "graphiti_debug_search:RuntimeError",
        ),
        (
            admin_routes.test_episode_response,
            {},
            "Graphiti test episode failed; see server logs.",
            "graphiti_test_episode:RuntimeError",
        ),
        (
            admin_routes.cypher_debug_response,
            {"q": "probe"},
            "Graph database is temporarily unavailable",
            "graphiti_debug_connect:RuntimeError",
        ),
    ],
)
def test_graph_admin_provider_failures_drop_raw_exception_context(
    monkeypatch,
    caplog,
    handler,
    query,
    detail,
    error_code,
):
    secret = "neo4j://private-host?token=admin-route-secret"
    monkeypatch.setattr(admin_routes, "_required_org_id", lambda _request: 7)
    monkeypatch.setattr(
        admin_routes,
        "attributed_admin_graph_call",
        lambda _org_id, *, operation: nullcontext(),
    )
    monkeypatch.setattr(admin_routes.graph_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        admin_routes.graph_client,
        "get_graphiti",
        lambda: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    with pytest.raises(HTTPException) as caught:
        handler(_request(org_id=7, **query))

    assert caught.value.status_code == 503
    assert caught.value.detail == detail
    assert caught.value.__context__ is None
    assert secret not in str(caught.value)
    assert secret not in caplog.text
    assert error_code in caplog.text


def test_admin_graph_organization_rejects_bool_before_database_access():
    with pytest.raises(ValueError, match="positive integer"):
        require_admin_graph_organization(True)


def test_admin_graph_organization_rejects_unknown_workspace(monkeypatch):
    class MissingOrganizationQuery:
        def filter(self, *_args):
            return self

        def first(self):
            return None

    class MissingOrganizationSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def query(self, *_args):
            return MissingOrganizationQuery()

    monkeypatch.setattr(
        admin_operations,
        "SessionLocal",
        MissingOrganizationSession,
    )

    with pytest.raises(LookupError, match="organization does not exist"):
        require_admin_graph_organization(999_999)


def test_admin_graph_attribution_is_reset_when_operation_raises(monkeypatch):
    monkeypatch.setattr(
        admin_operations,
        "require_admin_graph_organization",
        lambda organization_id: organization_id,
    )
    assert graph_metering_ctx.get() is None

    with pytest.raises(RuntimeError, match="synthetic failure"):
        with attributed_admin_graph_call(7, operation="test-reset"):
            context = graph_metering_ctx.get()
            assert context is not None
            assert context.organization_id == 7
            assert context.episode_name == "admin:test-reset"
            assert context.require_hard_admission is True
            assert context.require_role_admission is False
            raise RuntimeError("synthetic failure")

    assert graph_metering_ctx.get() is None


def test_cypher_debug_uses_driver_parameters_for_org_and_query(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class Driver:
        def execute_query(self, statement, **kwargs):
            calls.append((statement, kwargs))
            return SimpleNamespace(records=[])

    group_id = "org-'\\-scope"
    query = "candidate ' OR 1=1 \\"
    monkeypatch.setattr(admin_routes, "_required_org_id", lambda _request: 7)
    monkeypatch.setattr(
        admin_routes,
        "attributed_admin_graph_call",
        lambda _org_id, *, operation: nullcontext(),
    )
    monkeypatch.setattr(admin_routes.graph_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        admin_routes.graph_client,
        "group_id_for_org",
        lambda _org_id: group_id,
    )
    monkeypatch.setattr(
        admin_routes.graph_client,
        "get_graphiti",
        lambda: SimpleNamespace(driver=Driver()),
    )
    monkeypatch.setattr(
        admin_routes.graph_client,
        "run_async",
        lambda value, *, timeout: value,
    )

    response = admin_routes.cypher_debug_response(_request(org_id=7, q=query))

    assert response["query"] == query
    assert len(calls) == 3
    assert calls[1][1] == {"parameters_": {"group_id": group_id}}
    assert calls[2][1] == {
        "parameters_": {"group_id": group_id, "query": query}
    }
    assert group_id not in calls[1][0]
    assert query not in calls[2][0]


def test_episode_probe_is_deterministic_and_isolated_from_candidate_group(
    monkeypatch,
):
    calls: list[dict] = []

    class Graphiti:
        def add_episode(self, **kwargs):
            calls.append(kwargs)
            return object()

    monkeypatch.setattr(admin_routes, "_required_org_id", lambda _request: 7)
    monkeypatch.setattr(
        admin_routes,
        "attributed_admin_graph_call",
        lambda _org_id, *, operation: nullcontext(),
    )
    monkeypatch.setattr(admin_routes.graph_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        admin_routes.graph_client,
        "group_id_for_org",
        lambda _org_id: "candidate-org-7",
    )
    monkeypatch.setattr(
        admin_routes.graph_client,
        "get_graphiti",
        Graphiti,
    )
    monkeypatch.setattr(
        admin_routes.graph_client,
        "run_async",
        lambda value, *, timeout: value,
    )
    monkeypatch.setitem(
        sys.modules,
        "graphiti_core.nodes",
        SimpleNamespace(EpisodeType=SimpleNamespace(text="text")),
    )

    first = admin_routes.test_episode_response(_request(org_id=7))
    second = admin_routes.test_episode_response(_request(org_id=7))

    assert first == second == {"status": "ok", "episodes_sent": 1}
    assert len(calls) == 2
    assert calls[0]["group_id"] == "candidate-org-7:admin-connectivity"
    assert calls[0]["group_id"] != "candidate-org-7"
    assert calls[0]["uuid"] == calls[1]["uuid"]
