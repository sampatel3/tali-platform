"""Tests for the public API substrate: per-org API keys + the /public/v1 surface.

Focus is the security-critical behaviour — minting/revoke, scope enforcement,
missing/invalid/revoked-key rejection, and (the important one) tenant isolation
via the key→organization resolution.
"""
from tests.conftest import auth_headers, create_task_via_api


def _mint_key(client, headers, scopes=None, name="test key", is_test=False):
    payload = {"name": name, "is_test": is_test}
    if scopes is not None:
        payload["scopes"] = scopes
    r = client.post("/api/v1/api-keys", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _key_headers(secret):
    return {"Authorization": f"Bearer {secret}"}


# ---- Management API -------------------------------------------------------
def test_mint_list_revoke_api_key(client):
    headers, _ = auth_headers(client, organization_name="OrgKeys")

    created = _mint_key(client, headers, scopes=["roles:read"], name="warehouse")
    assert created["secret"].startswith("tali_live_")
    assert created["prefix"].startswith("tali_live_")
    assert created["scopes"] == ["roles:read"]

    listed = client.get("/api/v1/api-keys", headers=headers)
    assert listed.status_code == 200
    body = listed.json()
    assert any(k["id"] == created["id"] for k in body["keys"])
    # The plaintext secret must NEVER appear after creation.
    assert all("secret" not in k for k in body["keys"])
    assert "roles:read" in body["available_scopes"]

    revoked = client.delete(f"/api/v1/api-keys/{created['id']}", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None


def test_test_key_prefix(client):
    headers, _ = auth_headers(client, organization_name="OrgTestKey")
    created = _mint_key(client, headers, is_test=True)
    assert created["secret"].startswith("tali_test_")
    assert created["is_test"] is True


def test_unknown_scope_rejected(client):
    headers, _ = auth_headers(client, organization_name="OrgBadScope")
    r = client.post(
        "/api/v1/api-keys",
        json={"name": "x", "scopes": ["bogus:scope"]},
        headers=headers,
    )
    assert r.status_code == 400


# ---- Public surface auth --------------------------------------------------
def test_public_requires_valid_key(client):
    assert client.get("/public/v1/tests").status_code == 401
    assert (
        client.get("/public/v1/tests", headers=_key_headers("tali_live_nope")).status_code
        == 401
    )
    # A non-tali bearer is rejected too.
    assert (
        client.get("/public/v1/tests", headers={"Authorization": "Bearer abc"}).status_code
        == 401
    )


def test_public_key_auth_and_revocation(client):
    headers, _ = auth_headers(client, organization_name="OrgE2E")
    created = _mint_key(client, headers, scopes=["roles:read"])
    kh = _key_headers(created["secret"])

    ok = client.get("/public/v1/tests", headers=kh)
    assert ok.status_code == 200
    assert "tests" in ok.json()

    # Revoke → the same key is now rejected.
    client.delete(f"/api/v1/api-keys/{created['id']}", headers=headers)
    assert client.get("/public/v1/tests", headers=kh).status_code == 401


def test_scope_enforcement(client):
    headers, _ = auth_headers(client, organization_name="OrgScope")

    # A key without roles:read can't list tests.
    no_roles = _mint_key(client, headers, scopes=["assessments:read"])
    assert (
        client.get("/public/v1/tests", headers=_key_headers(no_roles["secret"])).status_code
        == 403
    )

    # The scope gate runs before the handler, so a missing share-links:write
    # scope is a 403 even for a non-existent application id...
    assert (
        client.post(
            "/public/v1/applications/999/share-links",
            json={},
            headers=_key_headers(no_roles["secret"]),
        ).status_code
        == 403
    )
    # ...and with the scope, the same call is a clean org-scoped 404.
    can_share = _mint_key(client, headers, scopes=["share-links:write"])
    assert (
        client.post(
            "/public/v1/applications/999/share-links",
            json={},
            headers=_key_headers(can_share["secret"]),
        ).status_code
        == 404
    )


# ---- Tenant isolation -----------------------------------------------------
def test_tenant_isolation_via_tests(client):
    # Org A owns a task; it must be visible to A's key and invisible to B's.
    headers_a, _ = auth_headers(client, organization_name="OrgA-iso")
    task = create_task_via_api(client, headers_a)
    assert task.status_code == 201, task.text
    task_name = task.json()["name"]

    key_a = _mint_key(client, headers_a, scopes=["roles:read"])
    tests_a = client.get(
        "/public/v1/tests", headers=_key_headers(key_a["secret"])
    ).json()["tests"]
    assert any(t["name"] == task_name for t in tests_a)

    headers_b, _ = auth_headers(client, organization_name="OrgB-iso")
    key_b = _mint_key(client, headers_b, scopes=["roles:read"])
    tests_b = client.get(
        "/public/v1/tests", headers=_key_headers(key_b["secret"])
    ).json()["tests"]
    assert all(t["name"] != task_name for t in tests_b)


def test_api_key_list_is_org_scoped(client):
    headers_a, _ = auth_headers(client, organization_name="OrgA-keys")
    key_a = _mint_key(client, headers_a, name="a-only")

    headers_b, _ = auth_headers(client, organization_name="OrgB-keys")
    listed_b = client.get("/api/v1/api-keys", headers=headers_b).json()
    assert all(k["id"] != key_a["id"] for k in listed_b["keys"])


def test_cross_org_owner_cannot_revoke_api_key(client):
    headers_a, _ = auth_headers(client, organization_name="OrgA-revoke")
    key_a = _mint_key(client, headers_a, scopes=["roles:read"])

    headers_b, _ = auth_headers(client, organization_name="OrgB-revoke")
    denied = client.delete(f"/api/v1/api-keys/{key_a['id']}", headers=headers_b)
    assert denied.status_code == 404
    assert denied.json()["detail"] == "API key not found"

    # A cross-org attempt must neither expose nor revoke the key.
    listed_a = client.get("/api/v1/api-keys", headers=headers_a).json()
    preserved = next(key for key in listed_a["keys"] if key["id"] == key_a["id"])
    assert preserved["revoked_at"] is None
    assert (
        client.get("/public/v1/tests", headers=_key_headers(key_a["secret"])).status_code
        == 200
    )


# ---- Workable stage + job metrics ----------------------------------------
def test_role_applications_expose_workable_stage_and_metrics(client, db):
    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication
    from app.models.role import Role
    from app.models.user import User

    headers, email = auth_headers(client, organization_name="OrgMetrics")
    org_id = db.query(User).filter(User.email == email).first().organization_id

    role = Role(organization_id=org_id, name="Backend Eng", source="manual")
    db.add(role)
    db.flush()

    # (email, pipeline_stage, workable_stage, application_outcome)
    specs = [
        ("a@ex.com", "applied", "Applied", "open"),
        ("b@ex.com", "advanced", "Technical Interview", "open"),
        ("c@ex.com", "applied", "Applied", "rejected"),
    ]
    for em, pstage, wstage, outcome in specs:
        cand = Candidate(organization_id=org_id, email=em, full_name=em.split("@")[0])
        db.add(cand)
        db.flush()
        db.add(
            CandidateApplication(
                organization_id=org_id,
                candidate_id=cand.id,
                role_id=role.id,
                pipeline_stage=pstage,
                application_outcome=outcome,
                workable_stage=wstage,
            )
        )
    db.commit()

    secret = _mint_key(client, headers, scopes=["applications:read"])["secret"]
    kh = _key_headers(secret)

    # List exposes the Workable stage alongside Taali's pipeline_stage.
    listed = client.get(f"/public/v1/roles/{role.id}/applications", headers=kh)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 3
    assert len(body["applications"]) == 3
    assert {a["workable_stage"] for a in body["applications"]} == {"Applied", "Technical Interview"}
    assert all("pipeline_stage" in a and "workable_stage" in a for a in body["applications"])

    # Filter by Workable stage.
    filtered = client.get(
        f"/public/v1/roles/{role.id}/applications?workable_stage=Applied", headers=kh
    ).json()
    assert filtered["total"] == 2

    # Metrics: totals + Workable-stage + outcome + the canonical Taali funnel.
    metrics = client.get(f"/public/v1/roles/{role.id}/metrics", headers=kh).json()
    assert metrics["total_applications"] == 3
    assert metrics["by_workable_stage"]["Applied"] == 2
    assert metrics["by_workable_stage"]["Technical Interview"] == 1
    assert metrics["by_application_outcome"]["rejected"] == 1
    assert metrics["by_application_outcome"]["open"] == 2
    assert isinstance(metrics["taali_funnel"], dict)


def test_related_role_public_applications_use_independent_pool_and_ats_link(
    client, db
):
    """The API-key list obeys the same related-role truth as agent tools."""

    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication
    from app.models.role import Role
    from app.models.sister_role_evaluation import SisterRoleEvaluation
    from app.models.user import User

    headers, email = auth_headers(client, organization_name="OrgPublicRelated")
    org_id = db.query(User).filter(User.email == email).one().organization_id
    owner = Role(organization_id=org_id, name="ATS owner", source="manual")
    related = Role(
        organization_id=org_id,
        name="Independent related role",
        source="sister",
        # Rolling compatibility oracle: the canonical boundary also recognizes
        # the explicit ATS-owner link when an older row still has role_kind=standard.
        role_kind="standard",
        ats_owner_role=owner,
    )
    db.add_all([owner, related])
    db.flush()

    candidate = Candidate(
        organization_id=org_id,
        email="member@public-related.test",
        full_name="Related Member",
    )
    owner_only = Candidate(
        organization_id=org_id,
        email="owner-only@public-related.test",
        full_name="Owner Only",
    )
    db.add_all([candidate, owner_only])
    db.flush()
    transport = CandidateApplication(
        organization_id=org_id,
        candidate_id=candidate.id,
        role_id=owner.id,
        source="workable",
        status="applied",
        pipeline_stage="review",
        application_outcome="open",
        workable_candidate_id="wk-public-related-member",
        workable_stage="Technical Interview",
        external_stage_raw="Technical Interview",
        taali_score_cache_100=7,
    )
    local = CandidateApplication(
        organization_id=org_id,
        candidate_id=candidate.id,
        role_id=related.id,
        source="manual",
        status="applied",
        # Deliberately contradictory storage state: the public result must use
        # the related role's evaluation and its explicit ATS transport.
        pipeline_stage="applied",
        application_outcome="rejected",
        workable_stage="Final Interview",
        external_stage_raw="Final Interview",
        taali_score_cache_100=3,
    )
    owner_only_application = CandidateApplication(
        organization_id=org_id,
        candidate_id=owner_only.id,
        role_id=owner.id,
        source="workable",
        status="applied",
        pipeline_stage="advanced",
        application_outcome="open",
        workable_candidate_id="wk-public-related-owner-only",
        workable_stage="Technical Interview",
        external_stage_raw="Technical Interview",
        taali_score_cache_100=100,
    )
    db.add_all([transport, local, owner_only_application])
    db.flush()
    db.add(
        SisterRoleEvaluation(
            organization_id=org_id,
            role_id=related.id,
            candidate_id=candidate.id,
            source_application_id=local.id,
            ats_application_id=transport.id,
            status="done",
            pipeline_stage="advanced",
            application_outcome="open",
            membership_source="direct_application",
            spec_fingerprint="public-related-oracle",
            role_fit_score=93,
            details={"fixture": "public-related-oracle"},
        )
    )
    db.commit()

    secret = _mint_key(client, headers, scopes=["applications:read"])["secret"]
    key_headers = _key_headers(secret)
    response = client.get(
        f"/public/v1/roles/{related.id}/applications",
        headers=key_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    [application] = body["applications"]
    assert application["id"] == local.id
    assert application["candidate"]["full_name"] == "Related Member"
    assert application["role_id"] == related.id
    assert application["role_name"] == related.name
    assert application["pipeline_stage"] == "advanced"
    assert application["application_outcome"] == "open"
    assert application["taali_score_100"] == 93
    assert application["workable_stage"] == "Technical Interview"

    linked_filter = client.get(
        f"/public/v1/roles/{related.id}/applications",
        params={"workable_stage": "Technical Interview"},
        headers=key_headers,
    )
    assert linked_filter.status_code == 200, linked_filter.text
    assert linked_filter.json()["total"] == 1

    stale_local_filter = client.get(
        f"/public/v1/roles/{related.id}/applications",
        params={"workable_stage": "Final Interview"},
        headers=key_headers,
    )
    assert stale_local_filter.status_code == 200, stale_local_filter.text
    assert stale_local_filter.json() == {"applications": [], "total": 0}

    # The frozen Workable filter is exact, not a provider-neutral substring.
    partial_filter = client.get(
        f"/public/v1/roles/{related.id}/applications",
        params={"workable_stage": "Technical"},
        headers=key_headers,
    )
    assert partial_filter.status_code == 200, partial_filter.text
    assert partial_filter.json() == {"applications": [], "total": 0}

    metrics = client.get(
        f"/public/v1/roles/{related.id}/metrics",
        headers=key_headers,
    )
    assert metrics.status_code == 200, metrics.text
    metrics_body = metrics.json()
    assert metrics_body["total_applications"] == 1
    assert metrics_body["by_application_outcome"] == {"open": 1}
    assert metrics_body["by_workable_stage"] == {"Technical Interview": 1}
    assert metrics_body["taali_funnel"]["advanced"] == 1

    detail = client.get(
        f"/public/v1/applications/{local.id}",
        params={"view_role_id": related.id},
        headers=key_headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json() == application

    wrong_role_detail = client.get(
        f"/public/v1/applications/{local.id}",
        params={"view_role_id": owner.id},
        headers=key_headers,
    )
    assert wrong_role_detail.status_code == 404

    share_secret = _mint_key(
        client,
        headers,
        scopes=["share-links:write"],
    )["secret"]
    shared = client.post(
        f"/public/v1/applications/{local.id}/share-links",
        json={
            "mode": "client",
            "expiry": "7d",
            "view_role_id": related.id,
        },
        headers=_key_headers(share_secret),
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["view_role_id"] == related.id

    shared_view = client.get(f"/share/{shared.json()['token']}")
    assert shared_view.status_code == 200, shared_view.text
    shared_body = shared_view.json()
    assert shared_body["view_role_id"] == related.id
    assert shared_body["application"]["role_id"] == related.id
    assert shared_body["application"]["pipeline_stage"] == "advanced"
    assert shared_body["application"]["application_outcome"] == "open"
    assert shared_body["application"]["taali_score"] == 93
    assert "source_role_score" not in shared_body["application"]

    wrong_role_share = client.post(
        f"/public/v1/applications/{local.id}/share-links",
        json={
            "mode": "client",
            "expiry": "7d",
            "view_role_id": owner.id,
        },
        headers=_key_headers(share_secret),
    )
    assert wrong_role_share.status_code == 404


def test_role_metrics_scope_and_org(client):
    headers, _ = auth_headers(client, organization_name="OrgMetricsScope")
    # roles:read only — the applications/metrics endpoints need applications:read.
    secret = _mint_key(client, headers, scopes=["roles:read"])["secret"]
    assert client.get("/public/v1/roles/999/applications", headers=_key_headers(secret)).status_code == 403
    assert client.get("/public/v1/roles/999/metrics", headers=_key_headers(secret)).status_code == 403
    # With the scope, a missing role is a clean 404.
    secret2 = _mint_key(client, headers, scopes=["applications:read"])["secret"]
    assert client.get("/public/v1/roles/999/metrics", headers=_key_headers(secret2)).status_code == 404
