"""Tests for the org-wide default_additional_requirements feature.

Three behaviors:
1. PATCH /organizations/me sets/clears the field
2. POST /roles inherits the org default when no additional_requirements provided
3. POST /roles keeps explicit additional_requirements when provided

Workable-import inheritance is exercised in the sync_service tests.
"""

from __future__ import annotations

import pytest

from tests.conftest import auth_headers


def _create_role(client, headers, **kwargs):
    payload = {"name": kwargs.pop("name", "Senior Engineer")}
    payload.update(kwargs)
    return client.post("/api/v1/roles", json=payload, headers=headers)


def _set_org_default(client, headers, value):
    return client.patch(
        "/api/v1/organizations/me",
        json={"default_additional_requirements": value},
        headers=headers,
    )


# ---------- PATCH /organizations/me ----------


def test_org_default_can_be_set_and_read_back(client):
    headers, _ = auth_headers(client)
    resp = _set_org_default(client, headers, "Must have: 5+ years AWS")
    assert resp.status_code == 200
    assert resp.json()["default_additional_requirements"] == "Must have: 5+ years AWS"

    # Round-trip via GET
    get_resp = client.get("/api/v1/organizations/me", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["default_additional_requirements"] == "Must have: 5+ years AWS"


def test_org_default_clears_on_empty_string(client):
    headers, _ = auth_headers(client)
    _set_org_default(client, headers, "Some default")
    resp = _set_org_default(client, headers, "")
    assert resp.status_code == 200
    # Empty string normalises to null/None on the wire
    assert resp.json()["default_additional_requirements"] in (None, "")


def test_org_default_starts_unset(client):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/organizations/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json().get("default_additional_requirements") in (None, "")


# ---------- Role create inherits the default ----------


def test_role_create_inherits_org_default(client):
    headers, _ = auth_headers(client)
    default_text = "Must have: 5+ years AWS\nPreferred: Banking domain"
    _set_org_default(client, headers, default_text)

    resp = _create_role(client, headers)
    assert resp.status_code == 201, resp.text
    role = resp.json()
    assert role["additional_requirements"] == default_text


def test_role_create_explicit_overrides_default(client):
    headers, _ = auth_headers(client)
    _set_org_default(client, headers, "Must have: AWS")

    explicit = "Must have: GCP only"
    resp = _create_role(client, headers, additional_requirements=explicit)
    assert resp.status_code == 201
    assert resp.json()["additional_requirements"] == explicit


def test_role_create_no_default_no_explicit_yields_none(client):
    """Sanity: no org default, no explicit → role.additional_requirements is null."""
    headers, _ = auth_headers(client)
    resp = _create_role(client, headers)
    assert resp.status_code == 201
    # Either null or empty — both mean "no recruiter-supplied criteria"
    assert resp.json().get("additional_requirements") in (None, "")


def test_role_create_blank_explicit_falls_back_to_default(client):
    """Empty-string additional_requirements should still pick up the org default,
    matching the "explicit None" path."""
    headers, _ = auth_headers(client)
    default_text = "Must have: AWS"
    _set_org_default(client, headers, default_text)

    resp = _create_role(client, headers, additional_requirements="")
    assert resp.status_code == 201
    assert resp.json()["additional_requirements"] == default_text
