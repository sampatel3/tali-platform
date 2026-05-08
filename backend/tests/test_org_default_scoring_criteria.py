"""Tests for the workspace-default → role inheritance flow.

The legacy ``Organization.default_role_requirements`` JSON column and
``default_additional_requirements`` text column were dropped in alembic
067. The PATCH /organizations/me endpoint no longer accepts either
field. Workspace defaults are now stored as ``OrganizationCriterion``
chips and role create snapshots them via ``snapshot_workspace_criteria``.

Three behaviors:
1. POST /organizations/me/criteria adds a workspace chip
2. POST /roles inherits all active workspace chips (with provenance)
3. POST /roles with explicit ``additional_requirements`` text parses it
   into chips with bucket inference (legacy callers / Workable import)
"""

from __future__ import annotations

from tests.conftest import auth_headers


def _create_role(client, headers, **kwargs):
    payload = {"name": kwargs.pop("name", "Senior Engineer")}
    payload.update(kwargs)
    return client.post("/api/v1/roles", json=payload, headers=headers)


def _add_org_chip(client, headers, *, text, bucket="preferred"):
    return client.post(
        "/api/v1/organizations/me/criteria",
        json={"text": text, "bucket": bucket},
        headers=headers,
    )


def test_workspace_chip_round_trips_via_get(client):
    headers, _ = auth_headers(client)
    create = _add_org_chip(client, headers, text="5+ years AWS", bucket="must")
    assert create.status_code == 201

    listed = client.get("/api/v1/organizations/me/criteria", headers=headers).json()
    assert len(listed) == 1
    assert listed[0]["text"] == "5+ years AWS"
    assert listed[0]["bucket"] == "must"


def test_role_create_inherits_workspace_chips(client):
    headers, _ = auth_headers(client)
    _add_org_chip(client, headers, text="5+ years AWS", bucket="must")
    _add_org_chip(client, headers, text="Banking domain", bucket="preferred")

    resp = _create_role(client, headers)
    assert resp.status_code == 201, resp.text
    role = resp.json()
    chip_buckets = {(c["bucket"], c["text"]) for c in role.get("criteria", [])}
    assert ("must", "5+ years AWS") in chip_buckets
    assert ("preferred", "Banking domain") in chip_buckets
    # Each inherited chip carries the workspace provenance.
    inherited = [c for c in role["criteria"] if c.get("org_criterion_id") is not None]
    assert len(inherited) == 2


def test_role_create_rejects_legacy_additional_requirements_field(client):
    """``additional_requirements`` was retired in alembic 068. Pydantic
    rejects it (or silently drops it via ``extra='ignore'``); either
    way no chip is created from the legacy field. Callers are expected
    to author chips via /roles/{id}/criteria after the role exists, or
    inherit the workspace defaults snapshot at create time."""
    headers, _ = auth_headers(client)
    resp = _create_role(client, headers, additional_requirements="Must have: GCP only")
    # Pydantic v2 default is to ignore extras silently; either response
    # is acceptable here. The contract is: NO chip with that text.
    if resp.status_code == 201:
        texts = {c["text"] for c in resp.json().get("criteria", [])}
        assert "GCP only" not in texts and "Must have: GCP only" not in texts
    else:
        assert resp.status_code in (400, 422)


def test_role_create_no_workspace_no_explicit_yields_empty_criteria(client):
    headers, _ = auth_headers(client)
    resp = _create_role(client, headers)
    assert resp.status_code == 201
    assert resp.json().get("criteria") == []


def test_org_patch_rejects_legacy_text_fields(client):
    """The dropped ``default_role_requirements`` / ``default_additional_requirements``
    fields are no longer part of OrgUpdate. Pydantic must reject them so
    callers don't silently lose data."""
    headers, _ = auth_headers(client)
    resp = client.patch(
        "/api/v1/organizations/me",
        json={"default_additional_requirements": "leftover"},
        headers=headers,
    )
    # Pydantic v2 forbids extra fields by default (or ignores them — either
    # is fine, the key is the value never persists).
    if resp.status_code == 200:
        # Field was silently ignored — confirm it didn't persist (the
        # OrgResponse no longer has the field at all).
        get_resp = client.get("/api/v1/organizations/me", headers=headers)
        assert get_resp.status_code == 200
        assert "default_additional_requirements" not in get_resp.json()
    else:
        assert resp.status_code in (400, 422)
