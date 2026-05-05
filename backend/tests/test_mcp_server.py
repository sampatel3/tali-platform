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


def test_mcp_lists_six_tools(client, db):
    rpc = _mcp_call(client, {}, "tools/list")
    names = {t["name"] for t in rpc["result"]["tools"]}
    assert names == {
        "list_roles",
        "get_role",
        "search_applications",
        "get_application",
        "get_candidate",
        "compare_applications",
    }


# ---------------------------------------------------------------------------
# list_roles / get_role
# ---------------------------------------------------------------------------


def test_list_roles_returns_org_roles_only(client, db, org_user):
    headers, _user, org_id = org_user
    role_a = _create_role_via_db(db, organization_id=org_id, name="A")
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
