"""End-to-end smoke tests for the new chip-based criteria flow:

- workspace criteria CRUD via ``/organizations/me/criteria``
- role criteria CRUD + sync + reset via ``/roles/{id}/criteria/...``
- bucket inference + provenance + suppression behavior

These exercise the API surface the new chip composer UI will call. The
underlying service helpers are also covered indirectly here.
"""

from __future__ import annotations

from tests.conftest import auth_headers


# ---------------------------------------------------------------------------
# Workspace criteria CRUD
# ---------------------------------------------------------------------------


def _list_org_criteria(client, headers):
    return client.get("/api/v1/organizations/me/criteria", headers=headers)


def _post_org_criterion(client, headers, **kwargs):
    return client.post("/api/v1/organizations/me/criteria", json=kwargs, headers=headers)


def test_workspace_criteria_crud_round_trip(client):
    headers, _ = auth_headers(client)
    resp = _list_org_criteria(client, headers)
    assert resp.status_code == 200
    assert resp.json() == []

    create = _post_org_criterion(
        client, headers, text="Senior backend (5+ yrs)", bucket="must"
    )
    assert create.status_code == 201, create.text
    chip_id = create.json()["id"]
    assert create.json()["bucket"] == "must"
    assert create.json()["text"] == "Senior backend (5+ yrs)"

    _post_org_criterion(client, headers, text="Worked with LLMs in prod", bucket="preferred")
    _post_org_criterion(client, headers, text="EU timezone (±2h CET)", bucket="constraint")

    listed = _list_org_criteria(client, headers).json()
    assert len(listed) == 3
    assert {c["bucket"] for c in listed} == {"must", "preferred", "constraint"}

    # Edit the must
    patch = client.patch(
        f"/api/v1/organizations/me/criteria/{chip_id}",
        json={"text": "Senior backend (7+ yrs)"},
        headers=headers,
    )
    assert patch.status_code == 200
    assert patch.json()["text"] == "Senior backend (7+ yrs)"
    assert patch.json()["bucket"] == "must"

    # Delete
    delete = client.delete(
        f"/api/v1/organizations/me/criteria/{chip_id}",
        headers=headers,
    )
    assert delete.status_code == 204
    listed_after = _list_org_criteria(client, headers).json()
    assert len(listed_after) == 2
    assert chip_id not in {c["id"] for c in listed_after}


def test_workspace_criteria_mirrors_into_legacy_text(client):
    """Legacy ``default_role_requirements`` + ``default_additional_requirements``
    must stay populated from chip state — older readers (system_prompt
    fallback, exports) keep working until the cleanup PR retires them."""
    headers, _ = auth_headers(client)
    _post_org_criterion(client, headers, text="Python", bucket="must")
    _post_org_criterion(client, headers, text="LLM exp", bucket="preferred")
    _post_org_criterion(client, headers, text="EU TZ", bucket="constraint")

    org = client.get("/api/v1/organizations/me", headers=headers).json()
    assert org["default_role_requirements"], "legacy list must be mirrored"
    blob = org.get("default_additional_requirements") or ""
    assert "MUST HAVE" in blob and "Python" in blob
    assert "PREFERRED" in blob and "LLM exp" in blob
    assert "CONSTRAINTS" in blob and "EU TZ" in blob


# ---------------------------------------------------------------------------
# Role-level chip flow + workspace sync + reset
# ---------------------------------------------------------------------------


def test_role_inherits_workspace_chips_with_provenance(client):
    headers, _ = auth_headers(client)
    _post_org_criterion(client, headers, text="Python", bucket="must")
    _post_org_criterion(client, headers, text="LLMs", bucket="preferred")

    role_resp = client.post("/api/v1/roles", json={"name": "Backend"}, headers=headers)
    assert role_resp.status_code == 201
    role = role_resp.json()
    chips = role.get("criteria") or []
    # All 2 workspace chips inherited, both carry org_criterion_id provenance.
    inherited = [c for c in chips if c["source"] == "recruiter" and c.get("org_criterion_id") is not None]
    assert len(inherited) == 2
    assert {c["text"] for c in inherited} == {"Python", "LLMs"}


def test_role_sync_pulls_in_new_workspace_chips_and_keeps_role_only(client):
    headers, _ = auth_headers(client)
    _post_org_criterion(client, headers, text="Python", bucket="must")
    role_resp = client.post("/api/v1/roles", json={"name": "Backend"}, headers=headers)
    role_id = role_resp.json()["id"]

    # Recruiter adds a role-only chip.
    add = client.post(
        f"/api/v1/roles/{role_id}/criteria",
        json={"text": "Built async messaging at scale", "bucket": "must"},
        headers=headers,
    )
    assert add.status_code == 201
    assert add.json()["org_criterion_id"] is None

    # Workspace adds a brand new criterion AFTER the role was created.
    _post_org_criterion(client, headers, text="Postgres", bucket="must")

    # Sync pulls in the new workspace chip; role-only chip is preserved.
    sync = client.post(f"/api/v1/roles/{role_id}/criteria/sync", headers=headers)
    assert sync.status_code == 200
    chips = sync.json()["criteria"]
    texts = {c["text"] for c in chips}
    assert {"Python", "Postgres", "Built async messaging at scale"}.issubset(texts)

    # Provenance is intact: role-only chip stays ``role`` source, workspace
    # chips have org_criterion_id populated.
    role_only = [c for c in chips if c["text"] == "Built async messaging at scale"]
    assert len(role_only) == 1 and role_only[0]["org_criterion_id"] is None
    workspace_chips = [c for c in chips if c["text"] in {"Python", "Postgres"}]
    assert all(c["org_criterion_id"] is not None for c in workspace_chips)


def test_deleting_workspace_inherited_chip_on_role_records_suppression(client):
    headers, _ = auth_headers(client)
    org_resp = _post_org_criterion(client, headers, text="Python", bucket="must")
    org_chip_id = org_resp.json()["id"]
    role_id = client.post("/api/v1/roles", json={"name": "Backend"}, headers=headers).json()["id"]

    # Find the role chip linked to the workspace chip.
    role_chips = client.get(f"/api/v1/roles/{role_id}", headers=headers).json()["criteria"]
    role_chip = next(c for c in role_chips if c.get("org_criterion_id") == org_chip_id)

    # Delete it on the role.
    deleted = client.delete(
        f"/api/v1/roles/{role_id}/criteria/{role_chip['id']}",
        headers=headers,
    )
    assert deleted.status_code == 204

    # Sync workspace must NOT re-add it because it's suppressed.
    sync = client.post(f"/api/v1/roles/{role_id}/criteria/sync", headers=headers)
    assert sync.status_code == 200
    after_sync = sync.json()["criteria"]
    assert org_chip_id not in {c.get("org_criterion_id") for c in after_sync}


def test_reset_role_to_workspace_drops_role_only_and_clears_suppression(client):
    headers, _ = auth_headers(client)
    org_chip = _post_org_criterion(client, headers, text="Python", bucket="must").json()
    role_id = client.post("/api/v1/roles", json={"name": "Backend"}, headers=headers).json()["id"]

    # Add a role-only chip.
    client.post(
        f"/api/v1/roles/{role_id}/criteria",
        json={"text": "role-only", "bucket": "preferred"},
        headers=headers,
    )
    # Suppress the workspace chip.
    role_chip = next(
        c for c in client.get(f"/api/v1/roles/{role_id}", headers=headers).json()["criteria"]
        if c.get("org_criterion_id") == org_chip["id"]
    )
    client.delete(f"/api/v1/roles/{role_id}/criteria/{role_chip['id']}", headers=headers)

    # Reset → the role-only chip is gone, suppression cleared so workspace chip is back.
    reset = client.post(f"/api/v1/roles/{role_id}/criteria/reset", headers=headers)
    assert reset.status_code == 200
    chips = reset.json()["criteria"]
    texts = {c["text"] for c in chips}
    assert "role-only" not in texts
    assert "Python" in texts


def test_editing_workspace_chip_on_role_marks_customized_and_blocks_sync_overwrite(client):
    headers, _ = auth_headers(client)
    org_chip = _post_org_criterion(client, headers, text="Python", bucket="must").json()
    role_id = client.post("/api/v1/roles", json={"name": "Backend"}, headers=headers).json()["id"]

    role_chip = next(
        c for c in client.get(f"/api/v1/roles/{role_id}", headers=headers).json()["criteria"]
        if c.get("org_criterion_id") == org_chip["id"]
    )
    # Recruiter customizes the chip on the role.
    edit = client.patch(
        f"/api/v1/roles/{role_id}/criteria/{role_chip['id']}",
        json={"text": "Python 3.11+"},
        headers=headers,
    )
    assert edit.status_code == 200
    assert edit.json()["customized_at"] is not None

    # Workspace edits the same chip.
    client.patch(
        f"/api/v1/organizations/me/criteria/{org_chip['id']}",
        json={"text": "Python (any version)"},
        headers=headers,
    )

    # Sync must NOT overwrite the recruiter customization.
    sync = client.post(f"/api/v1/roles/{role_id}/criteria/sync", headers=headers).json()
    same = next(c for c in sync["criteria"] if c.get("org_criterion_id") == org_chip["id"])
    assert same["text"] == "Python 3.11+"
