"""Org member roles: owner/member gating on invites, access settings, and role changes.

The first registered user of an org (the signup that created it) is its owner.
Owners manage members and access settings; members get read-only membership.
"""

from app.models.user import User
from app.platform.security import get_password_hash
from tests.conftest import TestingSessionLocal, auth_headers, login_user


MEMBER_PASSWORD = "MemberPass123!"


def _invite_member(client, owner_headers, email, name="Member Person"):
    resp = client.post(
        "/api/v1/users/invite",
        json={"email": email, "full_name": name},
        headers=owner_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _member_headers(client, email):
    """Give an invited member a known password + verified email, then log in."""
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        assert user is not None
        user.hashed_password = get_password_hash(MEMBER_PASSWORD)
        user.is_verified = True
        db.commit()
    finally:
        db.close()
    resp = login_user(client, email, MEMBER_PASSWORD)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_signup_org_creator_is_owner(client):
    headers, _ = auth_headers(client)
    me = client.get("/api/v1/users/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "owner"


def test_invited_user_is_member_and_role_listed(client):
    headers, _ = auth_headers(client)
    invited = _invite_member(client, headers, "invited-member@example.com")
    assert invited["role"] == "member"

    listing = client.get("/api/v1/users/", headers=headers)
    assert listing.status_code == 200
    roles = {row["email"]: row["role"] for row in listing.json()}
    assert roles["invited-member@example.com"] == "member"
    assert "owner" in roles.values()


def test_verified_flag_serialized_from_orm(client):
    """is_email_verified must mirror the ORM's is_verified, not default to False."""
    headers, owner_email = auth_headers(client)  # auth_headers verifies the owner
    _invite_member(client, headers, "unverified@example.com")

    listing = client.get("/api/v1/users/", headers=headers)
    verified = {row["email"]: row["is_email_verified"] for row in listing.json()}
    assert verified[owner_email] is True
    assert verified["unverified@example.com"] is False


def test_member_cannot_invite(client):
    owner_headers, _ = auth_headers(client)
    _invite_member(client, owner_headers, "no-invite-rights@example.com")
    member_headers = _member_headers(client, "no-invite-rights@example.com")

    resp = client.post(
        "/api/v1/users/invite",
        json={"email": "friend@example.com", "full_name": "Friend"},
        headers=member_headers,
    )
    assert resp.status_code == 403


def test_member_cannot_write_access_settings_but_can_edit_other_settings(client):
    owner_headers, _ = auth_headers(client)
    _invite_member(client, owner_headers, "settings-member@example.com")
    member_headers = _member_headers(client, "settings-member@example.com")

    for payload in (
        {"allowed_email_domains": ["example.com"]},
        {"sso_enforced": True},
        {"saml_enabled": True, "saml_metadata_url": "https://idp.example.com/metadata"},
        {"two_factor_required": True},
    ):
        resp = client.patch("/api/v1/organizations/me", json=payload, headers=member_headers)
        assert resp.status_code == 403, f"{payload} → {resp.status_code}"

    # Non-access settings stay open to members.
    resp = client.patch("/api/v1/organizations/me", json={"name": "Member Renamed"}, headers=member_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Member Renamed"


def test_owner_can_write_access_settings(client):
    owner_headers, _ = auth_headers(client)
    resp = client.patch(
        "/api/v1/organizations/me",
        json={"allowed_email_domains": ["example.com"]},
        headers=owner_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["allowed_email_domains"] == ["example.com"]


def test_owner_promotes_then_demotes_member(client):
    owner_headers, _ = auth_headers(client)
    invited = _invite_member(client, owner_headers, "promote-me@example.com")

    promoted = client.patch(
        f"/api/v1/users/{invited['id']}/role",
        json={"role": "owner"},
        headers=owner_headers,
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["role"] == "owner"

    demoted = client.patch(
        f"/api/v1/users/{invited['id']}/role",
        json={"role": "member"},
        headers=owner_headers,
    )
    assert demoted.status_code == 200, demoted.text
    assert demoted.json()["role"] == "member"


def test_promoted_member_can_invite(client):
    owner_headers, _ = auth_headers(client)
    invited = _invite_member(client, owner_headers, "future-owner@example.com")
    client.patch(
        f"/api/v1/users/{invited['id']}/role",
        json={"role": "owner"},
        headers=owner_headers,
    )
    new_owner_headers = _member_headers(client, "future-owner@example.com")

    resp = client.post(
        "/api/v1/users/invite",
        json={"email": "second-gen@example.com", "full_name": "Second Gen"},
        headers=new_owner_headers,
    )
    assert resp.status_code == 201, resp.text


def test_member_cannot_change_roles(client):
    owner_headers, _ = auth_headers(client)
    invited = _invite_member(client, owner_headers, "powerless@example.com")
    member_headers = _member_headers(client, "powerless@example.com")

    resp = client.patch(
        f"/api/v1/users/{invited['id']}/role",
        json={"role": "owner"},
        headers=member_headers,
    )
    assert resp.status_code == 403


def test_last_owner_cannot_be_demoted(client):
    owner_headers, _ = auth_headers(client)
    me = client.get("/api/v1/users/me", headers=owner_headers).json()

    resp = client.patch(
        f"/api/v1/users/{me['id']}/role",
        json={"role": "member"},
        headers=owner_headers,
    )
    assert resp.status_code == 400
    assert "at least one owner" in resp.json()["detail"]


def test_owner_can_remove_co_owner_but_caller_remains(client):
    """Removing another owner is fine — the caller stays as an active owner."""
    owner_headers, _ = auth_headers(client)
    invited = _invite_member(client, owner_headers, "co-owner@example.com")
    client.patch(
        f"/api/v1/users/{invited['id']}/role",
        json={"role": "owner"},
        headers=owner_headers,
    )

    resp = client.delete(f"/api/v1/users/{invited['id']}", headers=owner_headers)
    assert resp.status_code == 204

    listing = client.get("/api/v1/users/", headers=owner_headers)
    emails = [row["email"] for row in listing.json()]
    assert "co-owner@example.com" not in emails


def test_role_change_scoped_to_own_org(client):
    owner_a_headers, _ = auth_headers(client)
    owner_b_headers, _ = auth_headers(client, organization_name="OtherOrg")
    other_org_user = client.get("/api/v1/users/me", headers=owner_b_headers).json()

    resp = client.patch(
        f"/api/v1/users/{other_org_user['id']}/role",
        json={"role": "member"},
        headers=owner_a_headers,
    )
    assert resp.status_code == 404


def test_role_rejects_unknown_value(client):
    owner_headers, _ = auth_headers(client)
    invited = _invite_member(client, owner_headers, "weird-role@example.com")
    resp = client.patch(
        f"/api/v1/users/{invited['id']}/role",
        json={"role": "admin"},
        headers=owner_headers,
    )
    assert resp.status_code == 422
