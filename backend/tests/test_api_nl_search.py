"""Endpoint integration tests for ?nl_query= on /applications.

Mocks ``run_search`` so we don't depend on Postgres-only JSONB or a
running Anthropic key. Asserts:
- nl_query routes through the runner with the correct args.
- response shape includes parsed_filter, nl_warnings, nl_rerank_applied.
- view=graph attaches subgraph; view=list omits it.
- legacy ``search`` param is suppressed when nl_query is present.
- per-org rate limit returns 429 after 60 NL queries / minute.
- /healthz/neo4j returns "unconfigured" when NEO4J_URI is empty.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.candidate_search import rate_limit as nl_rate_limit
from app.candidate_search.parser import ProviderCallsForbiddenError
from app.candidate_search.schemas import (
    CandidateDeepVerification,
    GraphPayload,
    GraphNode,
    GraphEdge,
    ParsedFilter,
    SearchOutput,
    SearchRetrievalSummary,
    SearchRetrievalTrace,
    SearchWarning,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers


def _stub_search_output(
    *,
    with_subgraph: bool = False,
    with_verification: bool = False,
    with_retrieval: bool = False,
    with_plan: bool = False,
    application_ids: list[int] | None = None,
    retrieval_hits: list[SearchRetrievalTrace] | None = None,
    database_matches: int | None = None,
    retrieval_matches: int | None = None,
    is_exact_empty: bool = False,
) -> SearchOutput:
    parsed = ParsedFilter(
        skills_all=["AWS Glue"],
        free_text="candidates with AWS Glue experience",
    )
    subgraph = None
    if with_subgraph:
        subgraph = GraphPayload(
            nodes=[
                GraphNode(id="person:1", label="Person", name="Alice"),
                GraphNode(id="company:acme", label="Company", name="Acme"),
            ],
            edges=[GraphEdge(source="person:1", target="company:acme", label="WORKED_AT")],
        )
    selected_application_ids = list(application_ids or [])
    selected_retrieval_hits = retrieval_hits
    if with_retrieval and selected_retrieval_hits is None:
        selected_retrieval_hits = [
            SearchRetrievalTrace(
                application_id=77,
                candidate_id=7,
                score=0.05,
                sources=["graph", "postgres"],
                graph_rank=1,
                postgres_rank=2,
            )
        ]
    return SearchOutput(
        application_ids=selected_application_ids,
        parsed_filter=parsed,
        warnings=[SearchWarning(code="neo4j_unavailable", message="not configured")],
        rerank_applied=False,
        subgraph=subgraph,
        database_matches=database_matches,
        retrieval_matches=(
            retrieval_matches
            if retrieval_matches is not None
            else (
                len(selected_retrieval_hits or [])
                if with_retrieval
                else None
            )
        ),
        search_plan=(
            {
                "version": "1.0",
                "query": "candidates with AWS Glue experience",
                "criteria": [],
                "root": {"operator": "criterion", "criterion_id": "claim-1"},
                "limit": 50,
            }
            if with_plan
            else None
        ),
        retrieval=(
            SearchRetrievalSummary(
                mode="hybrid",
                graph_status="ok",
                graph_coverage=0.5,
                capped=False,
                exhaustive=False,
                is_exact_empty=is_exact_empty,
                hits=selected_retrieval_hits or [],
            )
            if with_retrieval
            else None
        ),
        is_exact_empty=is_exact_empty,
        **(
            {
                "deep_checked": 2,
                "evidence_succeeded": 1,
                "evidence_failed": 1,
                "qualified": 1,
                "capped": True,
                "exhaustive": False,
                "verification_results": [
                    CandidateDeepVerification(
                        application_id=77,
                        status="error",
                        error_code="model_call_failed",
                    )
                ],
            }
            if with_verification
            else {}
        ),
    )


@pytest.fixture(autouse=True)
def _reset_nl_rate_limit():
    nl_rate_limit.reset()
    yield
    nl_rate_limit.reset()


def _seed_application(
    db,
    *,
    organization_id: int,
    role: Role,
    label: str,
    candidate: Candidate | None = None,
    source: str = "workable",
    stage: str = "review",
    outcome: str = "open",
    taali_score: float = 80.0,
    pre_screen_score: float = 75.0,
) -> CandidateApplication:
    selected_candidate = candidate
    if selected_candidate is None:
        selected_candidate = Candidate(
            organization_id=organization_id,
            email=f"{label}@example.test",
            full_name=label.replace("-", " ").title(),
        )
        db.add(selected_candidate)
        db.flush()
    application = CandidateApplication(
        organization_id=organization_id,
        candidate_id=selected_candidate.id,
        role_id=role.id,
        source=source,
        workable_sourced=(source == "workable"),
        pipeline_stage=stage,
        application_outcome=outcome,
        taali_score_cache_100=taali_score,
        pre_screen_score_100=pre_screen_score,
    )
    db.add(application)
    db.flush()
    return application


def test_nl_query_routes_through_runner_and_echoes_parsed_filter(client):
    headers, _ = auth_headers(client)
    # No applications exist for this user → run_search returns empty ids,
    # the endpoint applies WHERE id IN ([-1]) and SQLite handles it cleanly.
    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(),
    ) as mocked:
        resp = client.get(
            "/api/v1/applications?nl_query=candidates with AWS Glue experience",
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["items"] == []
    assert data["parsed_filter"]["skills_all"] == ["AWS Glue"]
    assert data["nl_rerank_applied"] is False
    assert data["nl_warnings"][0]["code"] == "neo4j_unavailable"
    # subgraph absent in list view.
    assert "subgraph" not in data
    assert data["nl_coverage"]["database_matches"] == 0
    # Deep verification is opt-in; the default search is Postgres-only.
    mocked.assert_called_once()
    kwargs = mocked.call_args.kwargs
    assert kwargs["nl_query"] == "candidates with AWS Glue experience"
    assert kwargs["role_id"] is None
    assert kwargs["rerank_enabled"] is False
    assert kwargs["include_subgraph"] is False


def test_view_graph_attaches_subgraph(client):
    headers, _ = auth_headers(client)
    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(with_subgraph=True),
    ) as mocked:
        resp = client.get(
            "/api/v1/applications?nl_query=ignored&view=graph",
            headers=headers,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "subgraph" in data
    assert data["subgraph"]["nodes"][0]["id"] == "person:1"
    kwargs = mocked.call_args.kwargs
    assert kwargs["include_subgraph"] is True


def test_unscoped_hybrid_retrieval_trace_is_not_exposed(client):
    headers, _ = auth_headers(client)
    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(with_retrieval=True),
    ):
        resp = client.get(
            "/api/v1/applications?nl_query=semantic requirement",
            headers=headers,
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["nl_retrieval"] == {
        "mode": "hybrid",
        "graph_status": "ok",
        "graph_coverage": 0.5,
        "capped": False,
        "exhaustive": False,
        "is_exact_empty": False,
        "total_hits": 1,
        "filtered_hits": 0,
        "returned_hits": 0,
        "hits": [],
    }


def test_runner_receives_full_lifecycle_source_stage_and_score_scope(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    role_one = Role(
        organization_id=user.organization_id,
        name="Platform Engineer",
        source="manual",
    )
    role_two = Role(
        organization_id=user.organization_id,
        name="AI Engineer",
        source="manual",
    )
    db.add_all([role_one, role_two])
    db.flush()

    shared_candidate = Candidate(
        organization_id=user.organization_id,
        email="shared-candidate@example.test",
        full_name="Shared Candidate",
    )
    db.add(shared_candidate)
    db.flush()
    active_application = _seed_application(
        db,
        organization_id=user.organization_id,
        role=role_one,
        label="active",
        candidate=shared_candidate,
        outcome="open",
    )
    closed_application = _seed_application(
        db,
        organization_id=user.organization_id,
        role=role_two,
        label="closed",
        candidate=shared_candidate,
        outcome="rejected",
    )
    _seed_application(
        db,
        organization_id=user.organization_id,
        role=role_one,
        label="manual-source",
        source="manual",
    )
    _seed_application(
        db,
        organization_id=user.organization_id,
        role=role_one,
        label="wrong-stage",
        stage="applied",
    )
    _seed_application(
        db,
        organization_id=user.organization_id,
        role=role_one,
        label="low-score",
        taali_score=49,
    )
    _seed_application(
        db,
        organization_id=user.organization_id,
        role=role_one,
        label="low-prescreen",
        pre_screen_score=59,
    )
    db.commit()

    scoped_rows: list[tuple] = []

    def _scoped_search(**kwargs):
        rows = kwargs["base_query"].with_entities(
            CandidateApplication.id,
            CandidateApplication.candidate_id,
            CandidateApplication.application_outcome,
            CandidateApplication.source,
            CandidateApplication.pipeline_stage,
            CandidateApplication.taali_score_cache_100,
            CandidateApplication.pre_screen_score_100,
        ).all()
        scoped_rows.extend(tuple(row) for row in rows)
        return _stub_search_output(
            application_ids=[active_application.id],
            database_matches=1,
            retrieval_matches=1,
        )

    with patch(
        "app.candidate_search.runner.run_search",
        side_effect=_scoped_search,
    ) as mocked:
        response = client.get(
            "/api/v1/applications",
            params={
                "nl_query": "agent platform experience",
                "role_ids": f"{role_one.id},{role_two.id}",
                "source": "workable",
                "application_outcome": "all",
                "pipeline_stage": "review",
                "min_taali_score": 50,
                "min_pre_screen_score": 60,
                "include_stage_counts": "false",
            },
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert [row[0] for row in scoped_rows] == [
        active_application.id,
        closed_application.id,
    ]
    assert all(row[3] == "workable" for row in scoped_rows)
    assert all(row[4] == "review" for row in scoped_rows)
    assert all(row[5] >= 50 for row in scoped_rows)
    assert all(row[6] >= 60 for row in scoped_rows)
    assert [item["id"] for item in response.json()["items"]] == [
        active_application.id
    ]
    assert mocked.call_args.kwargs["retrieval_limit"] == 1000


def test_retrieval_trace_contains_only_final_page_with_counts(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    role = Role(
        organization_id=user.organization_id,
        name="Search Trace Role",
        source="manual",
    )
    db.add(role)
    db.flush()
    first = _seed_application(
        db,
        organization_id=user.organization_id,
        role=role,
        label="trace-first",
    )
    excluded = _seed_application(
        db,
        organization_id=user.organization_id,
        role=role,
        label="trace-excluded",
        outcome="rejected",
    )
    second = _seed_application(
        db,
        organization_id=user.organization_id,
        role=role,
        label="trace-second",
    )
    third = _seed_application(
        db,
        organization_id=user.organization_id,
        role=role,
        label="trace-third",
    )
    db.commit()

    ranked_applications = [first, excluded, second, third]
    retrieval_hits = [
        SearchRetrievalTrace(
            application_id=application.id,
            candidate_id=application.candidate_id,
            score=1 / rank,
            sources=["postgres"],
            postgres_rank=rank,
        )
        for rank, application in enumerate(ranked_applications, start=1)
    ]
    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(
            with_retrieval=True,
            application_ids=[application.id for application in ranked_applications],
            retrieval_hits=retrieval_hits,
            database_matches=4,
            retrieval_matches=4,
        ),
    ):
        response = client.get(
            "/api/v1/applications",
            params={
                "nl_query": "ranked candidates",
                "pipeline_stage": "review",
                "limit": 1,
                "offset": 1,
                "include_stage_counts": "false",
            },
            headers=headers,
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 3
    assert [item["id"] for item in payload["items"]] == [second.id]
    assert payload["nl_coverage"]["retrieval_matches"] == 4
    assert payload["nl_coverage"]["filtered_matches"] == 3
    assert payload["nl_retrieval"]["total_hits"] == 4
    assert payload["nl_retrieval"]["filtered_hits"] == 3
    assert payload["nl_retrieval"]["returned_hits"] == 1
    assert [hit["application_id"] for hit in payload["nl_retrieval"]["hits"]] == [
        second.id
    ]


def test_backend_independent_search_plan_is_exposed_for_audit(client):
    headers, _ = auth_headers(client)
    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(with_plan=True),
    ):
        resp = client.get(
            "/api/v1/applications?nl_query=AWS Glue experience",
            headers=headers,
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["nl_search_plan"]["version"] == "1.0"
    assert resp.json()["nl_search_plan"]["query"] == (
        "candidates with AWS Glue experience"
    )


def test_rerank_false_propagates_to_runner(client):
    headers, _ = auth_headers(client)
    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(),
    ) as mocked:
        resp = client.get(
            "/api/v1/applications?nl_query=ignored&rerank=false",
            headers=headers,
        )
    assert resp.status_code == 200
    assert mocked.call_args.kwargs["rerank_enabled"] is False


def test_provider_forbid_propagates_and_is_auditable(client, monkeypatch):
    headers, _ = auth_headers(client)
    release_sha = "a" * 40
    monkeypatch.setattr(
        "app.domains.assessments_runtime.application_search_support.runtime_release_sha",
        lambda: release_sha,
    )
    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(),
    ) as mocked:
        response = client.get(
            "/api/v1/applications",
            params={"nl_query": "Python", "provider_mode": "forbid"},
            headers=headers,
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["nl_provider_mode"] == "forbid"
    assert payload["deployment_sha"] == release_sha
    assert mocked.call_args.kwargs["provider_mode"] == "forbid"


@pytest.mark.parametrize("params", [{"rerank": "true"}, {"view": "graph"}])
def test_provider_forbid_rejects_explicit_provider_paths_before_runner(
    client, params
):
    headers, _ = auth_headers(client)
    with patch(
        "app.candidate_search.runner.run_search",
        side_effect=AssertionError("runner must not execute"),
    ) as mocked:
        response = client.get(
            "/api/v1/applications",
            params={"nl_query": "Python", "provider_mode": "forbid", **params},
            headers=headers,
        )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == (
        "candidate_search_provider_path_forbidden"
    )
    mocked.assert_not_called()


def test_provider_forbid_surfaces_ambiguous_query_as_typed_422(client):
    headers, _ = auth_headers(client)
    with patch(
        "app.candidate_search.runner.run_search",
        side_effect=ProviderCallsForbiddenError(
            "This query requires the model parser and cannot run with providers forbidden."
        ),
    ):
        response = client.get(
            "/api/v1/applications",
            params={"nl_query": "ambiguous request", "provider_mode": "forbid"},
            headers=headers,
        )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == (
        "candidate_search_provider_path_forbidden"
    )


def test_deep_verification_coverage_and_failures_are_exposed(client):
    headers, _ = auth_headers(client)
    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(with_verification=True),
    ):
        resp = client.get(
            "/api/v1/applications?nl_query=banking&rerank=true",
            headers=headers,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["nl_coverage"] == {
        "database_matches": 0,
        "retrieval_matches": 0,
        "deep_checked": 2,
        "evidence_succeeded": 1,
        "evidence_failed": 1,
        "qualified": 1,
        "capped": True,
        "exhaustive": False,
        "is_exact_empty": False,
        "filtered_matches": 0,
    }
    # Coverage remains global, but off-page candidate details are not exposed.
    assert data["nl_verification"] == []


def test_single_role_nl_query_is_role_scoped_and_role_metered(client):
    headers, _ = auth_headers(client)
    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(),
    ) as mocked:
        resp = client.get(
            "/api/v1/applications?nl_query=python&role_id=77",
            headers=headers,
        )

    assert resp.status_code == 200, resp.text
    kwargs = mocked.call_args.kwargs
    assert kwargs["role_id"] == 77
    assert "candidate_applications.role_id" in str(kwargs["base_query"])


def test_multi_role_nl_query_remains_workspace_metered(client):
    headers, _ = auth_headers(client)
    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(),
    ) as mocked:
        resp = client.get(
            "/api/v1/applications?nl_query=python&role_ids=77,88",
            headers=headers,
        )

    assert resp.status_code == 200, resp.text
    assert mocked.call_args.kwargs["role_id"] is None


def test_invalid_role_ids_fail_before_nl_provider_work(client):
    headers, _ = auth_headers(client)
    with patch(
        "app.candidate_search.runner.run_search",
        side_effect=AssertionError("provider-backed search must not run"),
    ) as mocked:
        resp = client.get(
            "/api/v1/applications?nl_query=python&role_ids=77,nope",
            headers=headers,
        )

    assert resp.status_code == 422, resp.text
    mocked.assert_not_called()


def test_legacy_search_is_ignored_when_nl_query_set(client):
    headers, _ = auth_headers(client)
    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(),
    ):
        # Even with `search=Alice`, the response uses parsed_filter only.
        resp = client.get(
            "/api/v1/applications?nl_query=AWS&search=Alice",
            headers=headers,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "parsed_filter" in data
    # No items exist regardless; the lack of a 500 here is the regression
    # we're guarding (legacy search would otherwise apply a Candidate join
    # that conflicts with the nl_query path).


def test_per_org_rate_limit_returns_429(client, monkeypatch):
    headers, _ = auth_headers(client)
    monkeypatch.setattr(nl_rate_limit, "MAX_PER_WINDOW", 2)
    nl_rate_limit.reset()
    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(),
    ):
        for _ in range(2):
            assert (
                client.get("/api/v1/applications?nl_query=foo", headers=headers).status_code
                == 200
            )
        third = client.get("/api/v1/applications?nl_query=foo", headers=headers)
        assert third.status_code == 429
        assert "natural-language" in third.json()["detail"].lower()


def test_no_nl_query_keeps_legacy_path_unchanged(client):
    headers, _ = auth_headers(client)
    # Without nl_query, parsed_filter should not appear and run_search must
    # NOT be invoked.
    with patch(
        "app.candidate_search.runner.run_search",
        side_effect=AssertionError("run_search must not be called"),
    ):
        resp = client.get("/api/v1/applications", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "parsed_filter" not in data


def test_graphiti_healthcheck_unconfigured(client):
    resp = client.get("/healthz/graphiti")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("unconfigured", "ok", "error")
    # In the test environment NEO4J_URI / VOYAGE_API_KEY are unset, so:
    assert body["status"] == "unconfigured"
