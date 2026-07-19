"""End-to-end tests for the /mcp server.

Each test goes through the streamable-HTTP transport (not the in-process
tool functions) so that auth, JSON-RPC framing, and response shape are
exercised the same way claude.ai would hit them.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from tests.conftest import auth_headers


MCP_HEADERS_BASE = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _parse_sse_payload(text: str) -> dict[str, Any]:
    """Pluck the JSON-RPC body out of an SSE stream of one ``message`` event."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    raise AssertionError(f"no SSE data: line in body: {text!r}")


def _mcp_call(client, headers: dict[str, str], method: str, params: dict | None = None, *, request_id: int = 1) -> dict:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    resp = client.post("/mcp/", json=body, headers={**MCP_HEADERS_BASE, **headers})
    assert resp.status_code == 200, f"MCP call failed: {resp.status_code} {resp.text!r}"
    if resp.headers.get("content-type", "").startswith("application/json"):
        return resp.json()
    return _parse_sse_payload(resp.text)


def _tool_payload(rpc_response: dict) -> Any:
    """Extract the structured payload from a tools/call response."""
    result = rpc_response.get("result")
    assert result is not None, f"tools/call returned no result: {rpc_response}"
    if result.get("isError"):
        raise AssertionError(f"tool reported error: {result}")
    structured = result.get("structuredContent")
    if structured is not None:
        # FastMCP wraps list/scalar returns under ``result``.
        return structured.get("result", structured)
    # Fallback: parse the first text content block as JSON
    content = result.get("content") or []
    if content:
        text = content[0].get("text", "")
        return json.loads(text)
    raise AssertionError(f"tools/call returned no structuredContent or content: {result}")


def _create_role_via_db(db, *, organization_id: int, name: str = "Senior Engineer", **kwargs) -> Role:
    role = Role(
        organization_id=organization_id,
        name=name,
        source=kwargs.pop("source", "manual"),
        description=kwargs.pop("description", "Test role"),
        **kwargs,
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


def _create_application(
    db,
    *,
    organization_id: int,
    role: Role,
    full_name: str,
    email: str,
    taali_score: float | None = None,
    pre_screen_score: float | None = None,
    pipeline_stage: str = "review",
    application_outcome: str = "open",
) -> CandidateApplication:
    candidate = Candidate(
        organization_id=organization_id,
        email=email,
        full_name=full_name,
        position="Engineer",
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=organization_id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage=pipeline_stage,
        pipeline_stage_source="recruiter",
        application_outcome=application_outcome,
        source="manual",
        taali_score_cache_100=taali_score,
        pre_screen_score_100=pre_screen_score,
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    return app


@pytest.fixture
def org_user(client, db):
    """Register + login one user, return (headers, user, organization_id)."""
    headers, email = auth_headers(client, organization_name="Test MCP Co")
    from app.models.user import User

    user = db.query(User).filter(User.email == email).first()
    assert user is not None and user.organization_id is not None
    return headers, user, user.organization_id


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_plain_mapping_auth_headers_are_case_insensitive():
    """Direct MCP adapters may supply a normal dict, not Starlette Headers."""

    from app.mcp.auth import _extract_api_key_header, _extract_bearer_token

    assert _extract_bearer_token(
        {"Authorization": "Bearer uppercase-bearer"}
    ) == "uppercase-bearer"
    assert _extract_api_key_header(
        {"X-API-Key": "tali_live_uppercase"}
    ) == "tali_live_uppercase"


def test_mcp_requires_bearer_token(client, db):
    """No Authorization header -> tool call fails with auth error."""
    # ``tools/list`` itself does not invoke the tool body, so it succeeds
    # without auth. We test auth by issuing ``tools/call``.
    rpc = _mcp_call(client, {}, "tools/call", {"name": "list_roles", "arguments": {}})
    result = rpc.get("result", {})
    assert result.get("isError") is True
    text = (result.get("content") or [{}])[0].get("text", "")
    assert "authorization" in text.lower()


def test_mcp_rejects_invalid_token(client, db):
    headers = {"Authorization": "Bearer not.a.valid.jwt"}
    rpc = _mcp_call(client, headers, "tools/call", {"name": "list_roles", "arguments": {}})
    result = rpc.get("result", {})
    assert result.get("isError") is True


def test_mcp_invalid_token_does_not_reflect_decoder_detail(db, monkeypatch):
    from app.mcp import auth

    private_marker = "private-jwt-decoder-detail"

    def reject_token(*_args, **_kwargs):
        raise auth.jwt.PyJWTError(private_marker)

    monkeypatch.setattr(auth, "decode_jwt", reject_token)

    with pytest.raises(auth.MCPAuthError, match=r"^invalid token$") as caught:
        auth._authenticate_jwt("untrusted-token", db)

    assert private_marker not in str(caught.value)


def test_mcp_lists_all_tools(client, db):
    rpc = _mcp_call(client, {}, "tools/list")
    names = {t["name"] for t in rpc["result"]["tools"]}
    assert names == {
        "list_roles",
        "get_role",
        "search_applications",
        "get_application",
        "get_candidate",
        "compare_applications",
        "nl_search_candidates",
        "graph_search_candidates",
        "get_candidate_cv",
        "get_recruiting_overview",
        "list_assessments",
    }


def test_public_mcp_mount_uses_non_streaming_json_transport(client, db):
    response = client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 91, "method": "tools/list"},
        headers=MCP_HEADERS_BASE,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["id"] == 91
    assert {tool["name"] for tool in payload["result"]["tools"]} >= {
        "list_roles",
        "nl_search_candidates",
    }


def test_recruiting_overview_tool_runs_through_public_mcp(client, db, org_user):
    headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id, name="Platform")
    _create_application(
        db,
        organization_id=org_id,
        role=role,
        full_name="Overview Candidate",
        email="overview@x.test",
        taali_score=81.0,
    )

    payload = _tool_payload(
        _mcp_call(
            client,
            headers,
            "tools/call",
            {"name": "get_recruiting_overview", "arguments": {"role_id": role.id}},
        )
    )
    assert payload["scope"]["role_id"] == role.id
    assert payload["applications"]["total"] == 1
    assert payload["links"]["role"].endswith(f"/jobs/{role.id}")


def test_list_assessments_tool_runs_through_public_mcp(client, db, org_user):
    headers, _user, _org_id = org_user
    payload = _tool_payload(
        _mcp_call(
            client,
            headers,
            "tools/call",
            {
                "name": "list_assessments",
                "arguments": {"attention": "needs_attention", "limit": 10},
            },
        )
    )
    assert payload["items"] == []
    assert payload["total"] == 0
    assert payload["filters"]["attention"] == "needs_attention"
    assert payload["limit"] == 10


# ---------------------------------------------------------------------------
# list_roles / get_role
# ---------------------------------------------------------------------------


def test_list_roles_returns_org_roles_only(client, db, org_user):
    headers, _user, org_id = org_user
    role_a = _create_role_via_db(
        db,
        organization_id=org_id,
        name="A",
        job_status="open",
        workable_job_data={"state": "published"},
    )
    _create_role_via_db(db, organization_id=org_id, name="B")
    # Role for a different org — should not leak.
    from app.models.organization import Organization

    other_org = Organization(name="Other", slug="other")
    db.add(other_org)
    db.commit()
    _create_role_via_db(db, organization_id=other_org.id, name="Leak")

    rpc = _mcp_call(client, headers, "tools/call", {"name": "list_roles", "arguments": {}})
    rows = _tool_payload(rpc)
    names = {r["name"] for r in rows}
    assert names == {"A", "B"}
    # Frontend URL is a deep link
    sample = next(r for r in rows if r["name"] == "A")
    assert sample["frontend_url"].endswith(f"/jobs/{role_a.id}")
    assert sample["role_id"] == role_a.id
    assert sample["job_status"] == "open"
    assert sample["workable_job_state"] == "published"


def test_get_role_returns_full_payload(client, db, org_user):
    headers, _user, org_id = org_user
    role = _create_role_via_db(
        db, organization_id=org_id, name="Backend", job_spec_text="Build APIs"
    )
    rpc = _mcp_call(
        client, headers, "tools/call", {"name": "get_role", "arguments": {"role_id": role.id}}
    )
    payload = _tool_payload(rpc)
    assert payload["role_id"] == role.id
    assert payload["job_spec_text"] == "Build APIs"
    assert "stage_counts" in payload
    assert payload["frontend_url"].endswith(f"/jobs/{role.id}")


def test_get_role_404_for_missing(client, db, org_user):
    headers, _user, _org_id = org_user
    rpc = _mcp_call(
        client, headers, "tools/call", {"name": "get_role", "arguments": {"role_id": 999_999}}
    )
    result = rpc["result"]
    assert result["isError"] is True


# ---------------------------------------------------------------------------
# search_applications
# ---------------------------------------------------------------------------


def test_search_applications_filters_by_min_score(client, db, org_user):
    headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id)
    _create_application(
        db,
        organization_id=org_id,
        role=role,
        full_name="Low Score",
        email="low@x.test",
        taali_score=42.0,
    )
    high = _create_application(
        db,
        organization_id=org_id,
        role=role,
        full_name="High Score",
        email="high@x.test",
        taali_score=88.0,
    )

    rpc = _mcp_call(
        client,
        headers,
        "tools/call",
        {
            "name": "search_applications",
            "arguments": {"role_id": role.id, "min_score": 70},
        },
    )
    rows = _tool_payload(rpc)
    assert [r["application_id"] for r in rows] == [high.id]
    assert rows[0]["candidate_name"] == "High Score"
    assert rows[0]["frontend_url"].endswith(f"/candidates/{high.id}?from=jobs/{role.id}")


def test_search_applications_threshold_accepts_0_to_10_scale(client, db, org_user):
    """A 0-10 threshold is auto-scaled to 0-100 to match recruiter UI behaviour."""
    headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id)
    _create_application(
        db, organization_id=org_id, role=role, full_name="A", email="a@x.test", taali_score=49.0
    )
    keep = _create_application(
        db, organization_id=org_id, role=role, full_name="B", email="b@x.test", taali_score=72.0
    )
    rpc = _mcp_call(
        client,
        headers,
        "tools/call",
        {"name": "search_applications", "arguments": {"role_id": role.id, "min_score": 7}},
    )
    rows = _tool_payload(rpc)
    assert [r["application_id"] for r in rows] == [keep.id]


def test_search_applications_offset_pages_the_stable_score_order(client, db, org_user):
    headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id)
    apps = [
        _create_application(
            db,
            organization_id=org_id,
            role=role,
            full_name=f"Candidate {score}",
            email=f"candidate-{score}@x.test",
            taali_score=float(score),
        )
        for score in (90, 80, 70)
    ]

    first = _tool_payload(
        _mcp_call(
            client,
            headers,
            "tools/call",
            {
                "name": "search_applications",
                "arguments": {"role_id": role.id, "limit": 2, "offset": 0},
            },
        )
    )
    second = _tool_payload(
        _mcp_call(
            client,
            headers,
            "tools/call",
            {
                "name": "search_applications",
                "arguments": {"role_id": role.id, "limit": 2, "offset": 2},
            },
        )
    )

    assert [row["application_id"] for row in first] == [apps[0].id, apps[1].id]
    assert [row["application_id"] for row in second] == [apps[2].id]


def test_search_applications_offset_has_deterministic_equal_score_tiebreaker(
    client, db, org_user
):
    headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id)
    apps = [
        _create_application(
            db,
            organization_id=org_id,
            role=role,
            full_name=f"Equal {index}",
            email=f"equal-{index}@x.test",
            taali_score=80.0,
        )
        for index in range(4)
    ]

    pages = []
    for offset in (0, 2):
        pages.extend(
            _tool_payload(
                _mcp_call(
                    client,
                    headers,
                    "tools/call",
                    {
                        "name": "search_applications",
                        "arguments": {
                            "role_id": role.id,
                            "limit": 2,
                            "offset": offset,
                        },
                    },
                )
            )
        )

    assert [row["application_id"] for row in pages] == sorted(
        [app.id for app in apps], reverse=True
    )


def test_search_applications_filters_by_stage_and_outcome(client, db, org_user):
    headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id)
    _create_application(
        db, organization_id=org_id, role=role, full_name="Rejected", email="r@x.test",
        taali_score=90.0, application_outcome="rejected",
    )
    open_app = _create_application(
        db, organization_id=org_id, role=role, full_name="Open", email="o@x.test",
        taali_score=80.0, application_outcome="open",
    )
    rpc = _mcp_call(
        client, headers, "tools/call",
        {"name": "search_applications", "arguments": {"role_id": role.id}},
    )
    rows = _tool_payload(rpc)
    assert [r["application_id"] for r in rows] == [open_app.id]


def test_search_applications_score_type_pre_screen(client, db, org_user):
    headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id)
    bad = _create_application(
        db, organization_id=org_id, role=role, full_name="B", email="b@x.test",
        taali_score=20.0, pre_screen_score=10.0,
    )
    good = _create_application(
        db, organization_id=org_id, role=role, full_name="G", email="g@x.test",
        taali_score=20.0, pre_screen_score=85.0,
    )
    rpc = _mcp_call(
        client, headers, "tools/call",
        {
            "name": "search_applications",
            "arguments": {"role_id": role.id, "min_score": 50, "score_type": "pre_screen"},
        },
    )
    rows = _tool_payload(rpc)
    assert [r["application_id"] for r in rows] == [good.id]
    assert bad.id not in {r["application_id"] for r in rows}


@pytest.mark.parametrize("threshold", [7, 70])
def test_search_applications_workable_threshold_accepts_either_scale(
    client, db, org_user, threshold
):
    headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id)
    low = _create_application(
        db,
        organization_id=org_id,
        role=role,
        full_name="Low Workable",
        email=f"low-workable-{threshold}@x.test",
        taali_score=90.0,
    )
    high = _create_application(
        db,
        organization_id=org_id,
        role=role,
        full_name="High Workable",
        email=f"high-workable-{threshold}@x.test",
        taali_score=40.0,
    )
    low.workable_score = 6.9
    high.workable_score = 8.2
    db.commit()

    rows = _tool_payload(
        _mcp_call(
            client,
            headers,
            "tools/call",
            {
                "name": "search_applications",
                "arguments": {
                    "role_id": role.id,
                    "score_type": "workable",
                    "min_score": threshold,
                    "sort_by": "workable_score",
                },
            },
        )
    )

    assert [row["application_id"] for row in rows] == [high.id]
    assert rows[0]["workable_score"] == 8.2
    assert rows[0]["workable_score_100"] == 82.0


def test_search_applications_supports_assessment_score_filter_and_sort(client, db, org_user):
    headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id)
    low = _create_application(
        db,
        organization_id=org_id,
        role=role,
        full_name="Low assessment",
        email="low-assessment@x.test",
        taali_score=90.0,
    )
    high = _create_application(
        db,
        organization_id=org_id,
        role=role,
        full_name="High assessment",
        email="high-assessment@x.test",
        taali_score=40.0,
    )
    low.assessment_score_cache_100 = 55.0
    high.assessment_score_cache_100 = 88.0
    db.commit()

    rows = _tool_payload(
        _mcp_call(
            client,
            headers,
            "tools/call",
            {
                "name": "search_applications",
                "arguments": {
                    "role_id": role.id,
                    "score_type": "assessment",
                    "min_score": 60,
                    "sort_by": "assessment_score",
                },
            },
        )
    )
    assert [row["application_id"] for row in rows] == [high.id]
    assert rows[0]["assessment_score"] == 88.0


def test_search_applications_q_matches_name(client, db, org_user):
    headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id)
    _create_application(
        db, organization_id=org_id, role=role, full_name="Alice Anderson", email="alice@x.test",
        taali_score=60.0,
    )
    bob = _create_application(
        db, organization_id=org_id, role=role, full_name="Bob Brown", email="bob@x.test",
        taali_score=60.0,
    )
    rpc = _mcp_call(
        client, headers, "tools/call",
        {"name": "search_applications", "arguments": {"q": "bob"}},
    )
    rows = _tool_payload(rpc)
    assert [r["application_id"] for r in rows] == [bob.id]


# ---------------------------------------------------------------------------
# get_application / get_candidate / compare_applications
# ---------------------------------------------------------------------------


def test_get_application_returns_detail_payload(client, db, org_user):
    headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id)
    app = _create_application(
        db, organization_id=org_id, role=role, full_name="Cara", email="cara@x.test",
        taali_score=77.0, pre_screen_score=64.0,
    )
    rpc = _mcp_call(
        client, headers, "tools/call",
        {"name": "get_application", "arguments": {"application_id": app.id}},
    )
    payload = _tool_payload(rpc)
    assert payload["application_id"] == app.id
    assert payload["taali_score"] == 77.0
    assert payload["pre_screen_score"] == 64.0
    assert payload["candidate_name"] == "Cara"
    assert payload["cv_text"] is None  # default include_cv_text=False


def test_get_application_cross_org_404(client, db, org_user):
    """A different org's application id must not be returnable."""
    headers, _user, _org_id = org_user
    from app.models.organization import Organization

    other_org = Organization(name="Other", slug="other")
    db.add(other_org)
    db.commit()
    other_role = _create_role_via_db(db, organization_id=other_org.id, name="Foreign")
    foreign_app = _create_application(
        db, organization_id=other_org.id, role=other_role,
        full_name="Hidden", email="hidden@x.test", taali_score=99.0,
    )
    rpc = _mcp_call(
        client, headers, "tools/call",
        {"name": "get_application", "arguments": {"application_id": foreign_app.id}},
    )
    assert rpc["result"]["isError"] is True


def test_get_candidate_includes_cross_role_applications(client, db, org_user):
    headers, _user, org_id = org_user
    role_a = _create_role_via_db(db, organization_id=org_id, name="Role A")
    role_b = _create_role_via_db(db, organization_id=org_id, name="Role B")
    app_a = _create_application(
        db, organization_id=org_id, role=role_a, full_name="Sam", email="sam@x.test",
        taali_score=70.0,
    )
    # Reuse same candidate id by upserting a second application
    candidate_id = app_a.candidate_id
    second = CandidateApplication(
        organization_id=org_id,
        candidate_id=candidate_id,
        role_id=role_b.id,
        status="applied",
        pipeline_stage="applied",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        taali_score_cache_100=55.0,
    )
    db.add(second)
    db.commit()

    rpc = _mcp_call(
        client, headers, "tools/call",
        {"name": "get_candidate", "arguments": {"candidate_id": candidate_id}},
    )
    payload = _tool_payload(rpc)
    role_ids = {a["role_id"] for a in payload["applications"]}
    assert role_ids == {role_a.id, role_b.id}


def test_compare_applications_returns_scores(client, db, org_user):
    headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id)
    a = _create_application(db, organization_id=org_id, role=role, full_name="A",
                            email="a@x.test", taali_score=70.0)
    b = _create_application(db, organization_id=org_id, role=role, full_name="B",
                            email="b@x.test", taali_score=85.0)
    rpc = _mcp_call(
        client, headers, "tools/call",
        {"name": "compare_applications", "arguments": {"application_ids": [a.id, b.id]}},
    )
    payload = _tool_payload(rpc)
    assert [r["application_id"] for r in payload["applications"]] == [a.id, b.id]
    assert payload["applications"][0]["scores"]["taali"] == 70.0
    assert payload["applications"][1]["scores"]["taali"] == 85.0
    assert "score_legend" in payload


def test_compare_applications_reports_missing_ids(client, db, org_user):
    headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id)
    a = _create_application(db, organization_id=org_id, role=role, full_name="A",
                            email="a@x.test", taali_score=70.0)
    rpc = _mcp_call(
        client, headers, "tools/call",
        {"name": "compare_applications",
         "arguments": {"application_ids": [a.id, 999_999]}},
    )
    payload = _tool_payload(rpc)
    assert [r["application_id"] for r in payload["applications"]] == [a.id]
    assert payload["missing_ids"] == [999_999]


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


def test_resource_role_returns_markdown(client, db, org_user):
    headers, _user, org_id = org_user
    role = _create_role_via_db(
        db, organization_id=org_id, name="Platform Eng",
        job_spec_text="Distributed systems work.",
    )
    rpc = _mcp_call(
        client, headers, "resources/read",
        {"uri": f"tali://role/{role.id}"},
    )
    contents = rpc["result"]["contents"]
    assert contents and contents[0]["mimeType"] == "text/markdown"
    body = contents[0]["text"]
    assert "Platform Eng" in body
    assert "Distributed systems work." in body


# ---------------------------------------------------------------------------
# API-key auth (tali_* keys alongside JWT on the same /mcp mount)
# ---------------------------------------------------------------------------


def _mint_key(db, *, organization_id, scopes=None, expires_at=None, revoked=False):
    """Mint a tali_* API key and return its one-time plaintext secret."""
    from app.services.api_key_service import mint_api_key

    minted = mint_api_key(
        db,
        organization_id=organization_id,
        name="test-key",
        scopes=scopes,
        is_test=True,
        expires_at=expires_at,
    )
    if revoked:
        from app.services.api_key_service import _utcnow

        minted.api_key.revoked_at = _utcnow()
        db.commit()
    return minted.secret


def _key_headers(secret):
    return {"Authorization": f"Bearer {secret}"}


def test_api_key_happy_path_returns_org_scoped_data(client, db, org_user):
    """A tali_* key with default read scopes reaches org-scoped tool data."""
    _headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id, name="KeyRole")
    secret = _mint_key(db, organization_id=org_id)  # default scopes = full read

    rpc = _mcp_call(
        client, _key_headers(secret), "tools/call",
        {"name": "list_roles", "arguments": {}},
    )
    rows = _tool_payload(rpc)
    assert {r["name"] for r in rows} == {"KeyRole"}
    assert rows[0]["role_id"] == role.id


def test_api_key_via_x_api_key_header(client, db, org_user):
    """The X-API-Key header is accepted as an alternative to the bearer slot."""
    _headers, _user, org_id = org_user
    _create_role_via_db(db, organization_id=org_id, name="HeaderRole")
    secret = _mint_key(db, organization_id=org_id)

    rpc = _mcp_call(
        client, {"X-API-Key": secret}, "tools/call",
        {"name": "list_roles", "arguments": {}},
    )
    rows = _tool_payload(rpc)
    assert {r["name"] for r in rows} == {"HeaderRole"}


def test_api_key_cross_org_isolation(client, db, org_user):
    """A key minted for org B cannot see org A's data."""
    _headers, _user, org_a = org_user
    _create_role_via_db(db, organization_id=org_a, name="OrgARole")

    from app.models.organization import Organization

    org_b = Organization(name="Org B", slug="org-b")
    db.add(org_b)
    db.commit()
    _create_role_via_db(db, organization_id=org_b.id, name="OrgBRole")

    secret_b = _mint_key(db, organization_id=org_b.id)
    rpc = _mcp_call(
        client, _key_headers(secret_b), "tools/call",
        {"name": "list_roles", "arguments": {}},
    )
    rows = _tool_payload(rpc)
    assert {r["name"] for r in rows} == {"OrgBRole"}


def test_api_key_revoked_is_rejected(client, db, org_user):
    _headers, _user, org_id = org_user
    secret = _mint_key(db, organization_id=org_id, revoked=True)
    rpc = _mcp_call(
        client, _key_headers(secret), "tools/call",
        {"name": "list_roles", "arguments": {}},
    )
    result = rpc["result"]
    assert result["isError"] is True
    text = (result.get("content") or [{}])[0].get("text", "").lower()
    assert "api key" in text


def test_api_key_expired_is_rejected(client, db, org_user):
    from datetime import timedelta

    from app.services.api_key_service import _utcnow

    _headers, _user, org_id = org_user
    secret = _mint_key(
        db, organization_id=org_id, expires_at=_utcnow() - timedelta(hours=1)
    )
    rpc = _mcp_call(
        client, _key_headers(secret), "tools/call",
        {"name": "list_roles", "arguments": {}},
    )
    assert rpc["result"]["isError"] is True


def test_api_key_scope_missing_denies_application_tools(client, db, org_user):
    """A roles:read-only key is denied on applications:read tools but allowed on roles."""
    from app.models.api_key import SCOPE_ROLES_READ

    _headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id, name="ScopeRole")
    app = _create_application(
        db, organization_id=org_id, role=role, full_name="Scoped",
        email="scoped@x.test", taali_score=80.0,
    )
    secret = _mint_key(db, organization_id=org_id, scopes=[SCOPE_ROLES_READ])

    # roles:read tool -> allowed
    rpc_roles = _mcp_call(
        client, _key_headers(secret), "tools/call",
        {"name": "list_roles", "arguments": {}},
    )
    rows = _tool_payload(rpc_roles)
    assert {r["name"] for r in rows} == {"ScopeRole"}

    # applications:read tool -> denied on scope
    rpc_apps = _mcp_call(
        client, _key_headers(secret), "tools/call",
        {"name": "get_application", "arguments": {"application_id": app.id}},
    )
    result = rpc_apps["result"]
    assert result["isError"] is True
    text = (result.get("content") or [{}])[0].get("text", "").lower()
    assert "scope" in text


def test_api_key_assessment_scope_is_independent(client, db, org_user):
    """Assessment queues require assessments:read, not applications:read."""
    from app.models.api_key import (
        SCOPE_APPLICATIONS_READ,
        SCOPE_ASSESSMENTS_READ,
    )

    _headers, _user, org_id = org_user
    assessment_key = _mint_key(
        db, organization_id=org_id, scopes=[SCOPE_ASSESSMENTS_READ]
    )
    allowed = _mcp_call(
        client,
        _key_headers(assessment_key),
        "tools/call",
        {"name": "list_assessments", "arguments": {}},
    )
    assert _tool_payload(allowed)["items"] == []

    applications_key = _mint_key(
        db, organization_id=org_id, scopes=[SCOPE_APPLICATIONS_READ]
    )
    denied = _mcp_call(
        client,
        _key_headers(applications_key),
        "tools/call",
        {"name": "list_assessments", "arguments": {}},
    )
    result = denied["result"]
    assert result["isError"] is True
    text = (result.get("content") or [{}])[0].get("text", "").lower()
    assert "assessments:read" in text


def test_api_key_recruiting_overview_requires_all_source_scopes(
    client, db, org_user
):
    """The aggregate cannot bypass any underlying domain's read grant."""
    from app.models.api_key import (
        SCOPE_APPLICATIONS_READ,
        SCOPE_ASSESSMENTS_READ,
        SCOPE_ROLES_READ,
    )

    _headers, _user, org_id = org_user
    required = {
        SCOPE_ROLES_READ,
        SCOPE_APPLICATIONS_READ,
        SCOPE_ASSESSMENTS_READ,
    }
    for missing in required:
        key = _mint_key(
            db,
            organization_id=org_id,
            scopes=sorted(required - {missing}),
        )
        denied = _mcp_call(
            client,
            _key_headers(key),
            "tools/call",
            {"name": "get_recruiting_overview", "arguments": {}},
        )
        result = denied["result"]
        assert result["isError"] is True
        text = (result.get("content") or [{}])[0].get("text", "").lower()
        assert missing in text

    full_key = _mint_key(
        db,
        organization_id=org_id,
        scopes=sorted(required),
    )
    allowed = _mcp_call(
        client,
        _key_headers(full_key),
        "tools/call",
        {"name": "get_recruiting_overview", "arguments": {}},
    )
    assert _tool_payload(allowed)["scope"]["role_id"] is None


def test_api_key_roles_only_strips_application_counts(client, db, org_user):
    """A roles:read-only key sees the role catalog without funnel volume.

    applications_count / stage_counts are application metrics (the public REST
    API gates role metrics behind applications:read), so they're stripped from
    list_roles and get_role for roles-only keys — even when stage counts are
    explicitly requested.
    """
    from app.models.api_key import SCOPE_ROLES_READ

    _headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id, name="CountsRole")
    _create_application(
        db, organization_id=org_id, role=role, full_name="Counted",
        email="counted@x.test", taali_score=70.0,
    )
    secret = _mint_key(db, organization_id=org_id, scopes=[SCOPE_ROLES_READ])

    rpc_list = _mcp_call(
        client, _key_headers(secret), "tools/call",
        {"name": "list_roles", "arguments": {"include_stage_counts": True}},
    )
    for row in _tool_payload(rpc_list):
        assert "applications_count" not in row
        assert "stage_counts" not in row

    rpc_role = _mcp_call(
        client, _key_headers(secret), "tools/call",
        {"name": "get_role", "arguments": {"role_id": role.id}},
    )
    payload = _tool_payload(rpc_role)
    assert "applications_count" not in payload
    assert "stage_counts" not in payload


def test_api_key_scope_gates_resources(client, db, org_user):
    """Resources honour the same scope mapping as their sibling tools."""
    from app.models.api_key import SCOPE_ROLES_READ

    _headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id, name="ResRole")
    app = _create_application(
        db, organization_id=org_id, role=role, full_name="R",
        email="r2@x.test", taali_score=60.0,
    )
    secret = _mint_key(db, organization_id=org_id, scopes=[SCOPE_ROLES_READ])

    # role resource -> allowed under roles:read
    rpc_role = _mcp_call(
        client, _key_headers(secret), "resources/read",
        {"uri": f"tali://role/{role.id}"},
    )
    assert rpc_role["result"]["contents"][0]["mimeType"] == "text/markdown"

    # application resource -> requires applications:read -> denied
    rpc_app = _mcp_call(
        client, _key_headers(secret), "resources/read",
        {"uri": f"tali://application/{app.id}"},
    )
    assert "error" in rpc_app


def test_jwt_path_unchanged_has_full_read_access(client, db, org_user):
    """JWT (session) principals are exempt from scope gates — no regression."""
    headers, _user, org_id = org_user
    role = _create_role_via_db(db, organization_id=org_id, name="JwtRole")
    app = _create_application(
        db, organization_id=org_id, role=role, full_name="J",
        email="j@x.test", taali_score=91.0,
    )
    # roles tool
    rpc_roles = _mcp_call(client, headers, "tools/call", {"name": "list_roles", "arguments": {}})
    assert {r["name"] for r in _tool_payload(rpc_roles)} == {"JwtRole"}
    # applications tool — no scope check applied to JWT principals
    rpc_app = _mcp_call(
        client, headers, "tools/call",
        {"name": "get_application", "arguments": {"application_id": app.id}},
    )
    assert _tool_payload(rpc_app)["application_id"] == app.id
