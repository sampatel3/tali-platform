"""Contract tests for the exact-SHA, read-only production search canary."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest

from app.candidate_search.schemas import (
    ParsedFilter,
    SearchOutput,
    SearchRetrievalSummary,
    SearchRetrievalTrace,
)
from app.models.api_key import ApiKey
from app.models.candidate_application import CandidateApplication
from app.scripts import provision_search_canary as provisioner


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "qa" / "prod_candidate_search_canary.py"
SPEC = importlib.util.spec_from_file_location("prod_candidate_search_canary", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
canary = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = canary
SPEC.loader.exec_module(canary)


def _config() -> canary.CanaryConfig:
    return canary.CanaryConfig(
        base_url="https://api.example.test",
        expected_sha="a" * 40,
        token="secret-canary-token",
        role_id=135,
        wait_seconds=1,
        poll_seconds=1,
    )


def _inventory_params(role_id: int) -> dict[str, object]:
    return {
        "role_id": role_id,
        "application_outcome": "all",
        "view": "list",
        "rerank": "false",
        "provider_mode": "forbid",
        "include_stage_counts": "false",
        "include_cv_text": "false",
        "limit": 50,
        "offset": 0,
    }


def _search_params(role_id: int) -> dict[str, object]:
    return {
        **_inventory_params(role_id),
        "assessment_status": "completed",
        "nl_query": canary.CANARY_QUERY,
    }


def _valid_inventory_payload(role_id: int = 135) -> dict:
    items = []
    for index, (email, truth) in enumerate(canary.FIXTURE_TRUTH.items(), start=1):
        items.append(
            {
                "id": 100 + index,
                "candidate_id": 200 + index,
                "candidate_email": email,
                "candidate_skills": truth["skills"],
                "candidate_location": truth["country"],
                "score_summary": {
                    "assessment_status": truth["assessment_status"]
                },
                "application_outcome": "open",
                "source": "manual",
                "role_id": role_id,
                "external_refs": {"internal_canary": "search-v1"},
            }
        )
    return {
        "deployment_sha": "a" * 40,
        "items": items,
        "total": 4,
    }


def _valid_payload() -> dict:
    return {
        "deployment_sha": "a" * 40,
        "nl_provider_mode": "forbid",
        "items": [
            {
                "id": 101,
                "candidate_id": 201,
                "candidate_email": canary.EXPECTED_EMAIL,
            }
        ],
        "total": 1,
        "parsed_filter": {
            "skills_all": ["Python", "PostgreSQL"],
            "skills_any": [],
            "titles_all": [],
            "titles_any": [],
            "locations_country": ["United Arab Emirates"],
            "locations_region": [],
            "min_years_experience": None,
            "graph_predicates": [],
            "graph_predicate_operator": "all",
            "soft_criteria": [],
            "preferred_criteria": [],
            "keywords": [],
            "free_text": canary.CANARY_QUERY,
            "parse_degraded": False,
        },
        "nl_warnings": [],
        "nl_rerank_applied": False,
        "nl_verification": [],
        "nl_coverage": {
            "database_matches": 1,
            "retrieval_matches": 1,
            "deep_checked": 0,
            "evidence_succeeded": 0,
            "evidence_failed": 0,
            "qualified": None,
            "capped": False,
            "exhaustive": True,
            "is_exact_empty": False,
            "filtered_matches": 1,
        },
        "nl_retrieval": {
            "mode": "postgres_only",
            "graph_status": "not_selected",
            "capped": False,
            "exhaustive": True,
            "is_exact_empty": False,
            "total_hits": 1,
            "filtered_hits": 1,
            "returned_hits": 1,
            "hits": [
                {
                    "application_id": 101,
                    "candidate_id": 201,
                    "sources": ["postgres"],
                }
            ],
        },
    }


def _stub_search_output(application_id: int, candidate_id: int) -> SearchOutput:
    return SearchOutput(
        application_ids=[application_id],
        parsed_filter=ParsedFilter(
            skills_all=["Python", "PostgreSQL"],
            locations_country=["United Arab Emirates"],
            free_text=canary.CANARY_QUERY,
        ),
        database_matches=1,
        retrieval_matches=1,
        retrieval=SearchRetrievalSummary(
            mode="postgres_only",
            graph_status="not_selected",
            hits=[
                SearchRetrievalTrace(
                    application_id=application_id,
                    candidate_id=candidate_id,
                    score=1.0,
                    sources=["postgres"],
                )
            ],
        ),
    )


def _provision(db) -> tuple[int, str]:
    role, token = provisioner.provision(db)
    role_id = int(role.id)
    db.commit()
    return role_id, token


def test_canary_is_read_only_exact_sha_and_provider_forbidden(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def _request(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/ready"):
            return 200, {
                "status": "healthy",
                "deployment": {"commit_sha": "a" * 40},
            }
        query = parse_qs(urlsplit(url).query)
        if "nl_query" in query:
            return 200, _valid_payload()
        return 200, _valid_inventory_payload()

    monkeypatch.setattr(canary, "_get_json", _request)
    canary.run(_config())

    assert len(calls) == 3
    inventory_url, inventory_kwargs = calls[1]
    search_url, search_kwargs = calls[2]
    inventory_query = parse_qs(urlsplit(inventory_url).query)
    search_query = parse_qs(urlsplit(search_url).query)
    assert "nl_query" not in inventory_query
    assert search_query["provider_mode"] == ["forbid"]
    assert search_query["rerank"] == ["false"]
    assert search_query["view"] == ["list"]
    assert search_query["assessment_status"] == ["completed"]
    assert search_query["role_id"] == ["135"]
    assert "secret-canary-token" not in inventory_url + search_url
    expected_header = {"Authorization": "Bearer secret-canary-token"}
    assert inventory_kwargs["headers"] == expected_header
    assert search_kwargs["headers"] == expected_header


def test_redirects_are_rejected_before_authorization_can_be_forwarded():
    request = Request(
        "https://api.example.test/api/v1/applications",
        headers={"Authorization": "Bearer secret-canary-token"},
    )
    handler = canary._RejectRedirects()

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {"Location": "https://attacker.example/collect"},
        "https://attacker.example/collect",
    )

    assert redirected is None


def test_route_specific_key_can_inventory_truth_without_writing_usage(
    db, client, monkeypatch
):
    role_id, token = _provision(db)
    release_sha = "a" * 40
    monkeypatch.setattr(
        "app.domains.assessments_runtime.application_search_support.runtime_release_sha",
        lambda: release_sha,
    )

    response = client.get(
        "/api/v1/applications",
        params=_inventory_params(role_id),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    canary._assert_inventory(
        replace(_config(), token=token, role_id=role_id),
        response.json(),
    )
    db.expire_all()
    assert db.query(ApiKey).one().last_used_at is None


def test_route_specific_key_can_execute_only_the_exact_nl_get(
    db, client, monkeypatch
):
    role_id, token = _provision(db)
    expected_app = next(
        app
        for app in db.query(CandidateApplication).all()
        if app.candidate.email == canary.EXPECTED_EMAIL
    )
    app_id = int(expected_app.id)
    candidate_id = int(expected_app.candidate_id)
    monkeypatch.setattr(
        "app.domains.assessments_runtime.application_search_support.runtime_release_sha",
        lambda: "a" * 40,
    )

    with patch(
        "app.candidate_search.runner.run_search",
        return_value=_stub_search_output(app_id, candidate_id),
    ) as mocked:
        response = client.get(
            "/api/v1/applications",
            params=_search_params(role_id),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200, response.text
    assert mocked.call_args.kwargs["provider_mode"] == "forbid"
    canary._assert_truth(
        replace(_config(), token=token, role_id=role_id),
        response.json(),
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda params, _role: params.pop("provider_mode"),
        lambda params, _role: params.update({"unexpected": "value"}),
        lambda params, role: params.update({"role_id": role + 1}),
        lambda params, _role: params.update({"rerank": "true"}),
        lambda params, _role: params.update({"view": "graph"}),
        lambda params, _role: params.update(
            {"nl_query": "different query", "assessment_status": "completed"}
        ),
        lambda params, _role: params.update({"assessment_status": "completed"}),
    ],
)
def test_route_specific_key_rejects_altered_request_before_runner(
    db, client, mutation
):
    role_id, token = _provision(db)
    params = _inventory_params(role_id)
    mutation(params, role_id)

    with patch(
        "app.candidate_search.runner.run_search",
        side_effect=AssertionError("runner must not execute"),
    ) as mocked:
        response = client.get(
            "/api/v1/applications",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403, response.text
    mocked.assert_not_called()


def test_route_specific_key_rejects_duplicate_parameters(db, client):
    role_id, token = _provision(db)
    params = list(_inventory_params(role_id).items())
    params.append(("role_id", role_id))

    response = client.get(
        "/api/v1/applications",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403, response.text


def test_route_specific_key_is_rejected_by_other_read_and_write_surfaces(
    db, client
):
    role_id, token = _provision(db)
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/v1/users/", headers=headers).status_code == 401
    assert (
        client.post(
            "/api/v1/taali-chat/turn",
            json={"message": "do not execute"},
            headers=headers,
        ).status_code
        == 401
    )
    assert (
        client.post(
            f"/api/v1/roles/{role_id}/applications",
            json={"candidate_email": "must-not-write@example.com"},
            headers=headers,
        ).status_code
        == 401
    )
    assert (
        client.get(
            f"/public/v1/roles/{role_id}/applications",
            headers=headers,
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("deployment_sha",), "b" * 40, "different release SHA"),
        (("nl_provider_mode",), "auto", "provider-forbidden"),
        (("total",), 2, "inclusion truth"),
        (("nl_rerank_applied",), True, "model reranking"),
        (("nl_retrieval", "graph_status"), "ok", "PostgreSQL-only"),
        (
            ("nl_retrieval", "hits"),
            [{"application_id": 101, "candidate_id": 201, "sources": ["graph"]}],
            "provenance",
        ),
    ],
)
def test_canary_fails_closed_on_contract_drift(path, value, message):
    payload = _valid_payload()
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(canary.CanaryFailure, match=message):
        canary._assert_truth(_config(), payload)


def test_inventory_fails_if_one_negative_control_is_missing():
    payload = _valid_inventory_payload()
    payload["items"].pop()
    payload["total"] = 3

    with pytest.raises(canary.CanaryFailure, match="fixture count"):
        canary._assert_inventory(_config(), payload)


def test_environment_validation_never_echoes_token(monkeypatch):
    monkeypatch.setenv("TALI_PROD_URL", "http://not-https.example.test")
    monkeypatch.setenv("TALI_SEARCH_CANARY_TOKEN", "do-not-print-this-token")
    monkeypatch.setenv("TALI_SEARCH_CANARY_ROLE_ID", "135")

    with pytest.raises(canary.CanaryFailure) as failure:
        canary._config_from_env("a" * 40, 1)

    assert "do-not-print-this-token" not in str(failure.value)


def test_search_http_failure_never_echoes_bearer_token(monkeypatch):
    monkeypatch.setattr(canary, "_get_json", lambda *_args, **_kwargs: (401, {}))

    with pytest.raises(canary.CanaryFailure) as failure:
        canary._search(_config(), "do-not-print-this-token")

    assert "do-not-print-this-token" not in str(failure.value)
