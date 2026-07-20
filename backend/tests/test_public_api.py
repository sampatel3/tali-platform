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


def test_role_metrics_scope_and_org(client):
    headers, _ = auth_headers(client, organization_name="OrgMetricsScope")
    # roles:read only — the applications/metrics endpoints need applications:read.
    secret = _mint_key(client, headers, scopes=["roles:read"])["secret"]
    assert client.get("/public/v1/roles/999/applications", headers=_key_headers(secret)).status_code == 403
    assert client.get("/public/v1/roles/999/metrics", headers=_key_headers(secret)).status_code == 403
    # With the scope, a missing role is a clean 404.
    secret2 = _mint_key(client, headers, scopes=["applications:read"])["secret"]
    assert client.get("/public/v1/roles/999/metrics", headers=_key_headers(secret2)).status_code == 404
