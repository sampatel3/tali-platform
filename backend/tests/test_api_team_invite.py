"""API tests for the team-member invite overhaul.

Covers invite → accept → login, resend, revoke/remove, and the derived
``status`` / ``email_sent`` response shape.

RESEND_API_KEY is unset in the test env, so ``email_sent`` is always False and
no real email is sent. Invite tokens are minted directly via
``generate_invite_token`` (the same helper the email path uses) so the
accept-invite flow can be exercised without a mail round-trip.
"""

import time

from fastapi_users.jwt import generate_jwt

from app.domains.identity_access.user_routes import (
    INVITE_TOKEN_AUDIENCE,
    generate_invite_token,
)
from app.models.user import User
from app.platform.config import settings
from tests.conftest import TestingSessionLocal, auth_headers


def _get_user(email: str) -> User | None:
    db = TestingSessionLocal()
    try:
        return db.query(User).filter(User.email == email).first()
    finally:
        db.close()


def _invite(client, headers, email, full_name="Invitee Person"):
    return client.post(
        "/api/v1/users/invite",
        json={"email": email, "full_name": full_name},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Invite response shape
# ---------------------------------------------------------------------------


def test_invite_response_includes_email_sent_and_status(client):
    headers, _ = auth_headers(client)
    resp = _invite(client, headers, "shape@example.com")
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["email_sent"] is False  # no RESEND key in test env
    assert data["status"] == "invited"
    assert data["is_verified"] is False


# ---------------------------------------------------------------------------
# Happy path: invite → accept → login
# ---------------------------------------------------------------------------


def test_invite_accept_then_login(client):
    headers, _ = auth_headers(client)
    email = "happy@example.com"
    assert _invite(client, headers, email).status_code == 201

    user = _get_user(email)
    assert user is not None and not user.is_verified
    token = generate_invite_token(user)

    accept = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "password": "NewPass123!"},
    )
    assert accept.status_code == 200, accept.text
    body = accept.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]

    # User is now verified and can log in with the new password.
    assert _get_user(email).is_verified is True
    login = client.post(
        "/api/v1/auth/jwt/login",
        data={"username": email, "password": "NewPass123!"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login.status_code == 200, login.text
    assert login.json()["access_token"]

    # The minted accept-invite token authenticates against a protected route.
    me = client.get(
        "/api/v1/users/",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert me.status_code == 200


# ---------------------------------------------------------------------------
# accept-invite error cases
# ---------------------------------------------------------------------------


def test_accept_invite_invalid_token_400(client):
    resp = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": "not-a-real-token", "password": "NewPass123!"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVITE_TOKEN_INVALID"


def test_accept_invite_expired_token_400(client):
    headers, _ = auth_headers(client)
    email = "expired@example.com"
    assert _invite(client, headers, email).status_code == 201
    user = _get_user(email)

    # Forge a token that expired one hour ago.
    data = {"sub": str(user.id), "email": user.email, "aud": INVITE_TOKEN_AUDIENCE}
    expired = generate_jwt(data, settings.SECRET_KEY, lifetime_seconds=-3600)

    resp = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": expired, "password": "NewPass123!"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVITE_TOKEN_INVALID"


def test_accept_invite_already_accepted_400(client):
    headers, _ = auth_headers(client)
    email = "already@example.com"
    assert _invite(client, headers, email).status_code == 201
    user = _get_user(email)
    token = generate_invite_token(user)

    first = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "password": "NewPass123!"},
    )
    assert first.status_code == 200

    second = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "password": "OtherPass123!"},
    )
    assert second.status_code == 400
    assert second.json()["detail"] == "INVITE_ALREADY_ACCEPTED"


def test_accept_invite_short_password_422(client):
    headers, _ = auth_headers(client)
    email = "shortpw@example.com"
    assert _invite(client, headers, email).status_code == 201
    token = generate_invite_token(_get_user(email))

    resp = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "password": "short"},
    )
    assert resp.status_code == 422


def test_accept_invite_common_password_422(client):
    headers, _ = auth_headers(client)
    email = "commonpw@example.com"
    assert _invite(client, headers, email).status_code == 201
    token = generate_invite_token(_get_user(email))

    resp = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "password": "password123"},
    )
    assert resp.status_code == 422
    assert "common" in str(resp.json().get("detail", "")).lower()


def test_accept_invite_revoked_400(client):
    headers, _ = auth_headers(client)
    email = "revoked-accept@example.com"
    assert _invite(client, headers, email).status_code == 201
    user = _get_user(email)
    token = generate_invite_token(user)

    # Revoke before accepting.
    del_resp = client.delete(f"/api/v1/users/{user.id}", headers=headers)
    assert del_resp.status_code == 204

    resp = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "password": "NewPass123!"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVITE_REVOKED"


# ---------------------------------------------------------------------------
# resend-invite
# ---------------------------------------------------------------------------


def test_resend_invite_success(client):
    headers, _ = auth_headers(client)
    email = "resend@example.com"
    assert _invite(client, headers, email).status_code == 201
    user = _get_user(email)

    resp = client.post(f"/api/v1/users/{user.id}/resend-invite", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["email_sent"] is False  # no RESEND key in test env


def test_resend_invite_non_pending_400(client):
    headers, _ = auth_headers(client)
    email = "resend-active@example.com"
    assert _invite(client, headers, email).status_code == 201
    user = _get_user(email)
    token = generate_invite_token(user)
    client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "password": "NewPass123!"},
    )

    resp = client.post(f"/api/v1/users/{user.id}/resend-invite", headers=headers)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "NOT_PENDING_INVITE"


def test_resend_invite_cross_org_404(client):
    headers_a, _ = auth_headers(client, email="owner-a@a.com", organization_name="Org A")
    email = "invitee-a@a.com"
    assert _invite(client, headers_a, email).status_code == 201
    target = _get_user(email)

    headers_b, _ = auth_headers(client, email="owner-b@b.com", organization_name="Org B")
    resp = client.post(f"/api/v1/users/{target.id}/resend-invite", headers=headers_b)
    assert resp.status_code == 404


def test_resend_invite_no_auth_401(client):
    resp = client.post("/api/v1/users/999/resend-invite")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# invite-link (manual-delivery recovery)
# ---------------------------------------------------------------------------


def test_invite_link_pending_returns_accept_link(client):
    headers, _ = auth_headers(client)
    email = "copylink@example.com"
    assert _invite(client, headers, email).status_code == 201
    user = _get_user(email)

    resp = client.post(f"/api/v1/users/{user.id}/invite-link", headers=headers)
    assert resp.status_code == 200, resp.text
    assert "/accept-invite?token=" in resp.json()["accept_link"]


def test_invite_link_non_pending_400(client):
    headers, _ = auth_headers(client)
    email = "copylink-active@example.com"
    assert _invite(client, headers, email).status_code == 201
    user = _get_user(email)
    token = generate_invite_token(user)
    client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "password": "NewPass123!"},
    )

    resp = client.post(f"/api/v1/users/{user.id}/invite-link", headers=headers)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "NOT_PENDING_INVITE"


def test_invite_link_cross_org_404(client):
    headers_a, _ = auth_headers(client, email="link-a@a.com", organization_name="Link Org A")
    email = "link-invitee-a@a.com"
    assert _invite(client, headers_a, email).status_code == 201
    target = _get_user(email)

    headers_b, _ = auth_headers(client, email="link-b@b.com", organization_name="Link Org B")
    resp = client.post(f"/api/v1/users/{target.id}/invite-link", headers=headers_b)
    assert resp.status_code == 404


def test_invite_link_no_auth_401(client):
    resp = client.post("/api/v1/users/999/invite-link")
    assert resp.status_code == 401


def test_invite_link_non_owner_member_403(client):
    # A non-owner member must not be able to mint an invite link (the token
    # could set a pending teammate's password). Gated like the rest of
    # member management via require_org_owner.
    headers, _ = auth_headers(client, email="link-owner@ex.com", organization_name="Link Gate Org")
    member_email = "link-member@ex.com"
    assert _invite(client, headers, member_email).status_code == 201
    member = _get_user(member_email)
    accept = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": generate_invite_token(member), "password": "MemberPass123!"},
    )
    assert accept.status_code == 200, accept.text
    member_headers = {"Authorization": f"Bearer {accept.json()['access_token']}"}

    target_email = "link-target@ex.com"
    assert _invite(client, headers, target_email).status_code == 201
    target = _get_user(target_email)

    resp = client.post(f"/api/v1/users/{target.id}/invite-link", headers=member_headers)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE user (revoke / remove)
# ---------------------------------------------------------------------------


def test_delete_revokes_pending_invite_and_hides_from_list(client):
    headers, _ = auth_headers(client)
    email = "to-revoke@example.com"
    assert _invite(client, headers, email).status_code == 201
    user = _get_user(email)

    resp = client.delete(f"/api/v1/users/{user.id}", headers=headers)
    assert resp.status_code == 204

    list_resp = client.get("/api/v1/users/", headers=headers)
    emails = [m["email"] for m in list_resp.json()]
    assert email not in emails


def test_delete_removes_active_member(client):
    headers, _ = auth_headers(client)
    email = "active-member@example.com"
    assert _invite(client, headers, email).status_code == 201
    user = _get_user(email)
    token = generate_invite_token(user)
    client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "password": "NewPass123!"},
    )

    resp = client.delete(f"/api/v1/users/{user.id}", headers=headers)
    assert resp.status_code == 204

    list_resp = client.get("/api/v1/users/", headers=headers)
    emails = [m["email"] for m in list_resp.json()]
    assert email not in emails


def test_delete_self_400(client):
    headers, owner_email = auth_headers(client)
    me = _get_user(owner_email)
    resp = client.delete(f"/api/v1/users/{me.id}", headers=headers)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "CANNOT_REMOVE_SELF"


def test_delete_cross_org_404(client):
    headers_a, _ = auth_headers(client, email="del-a@a.com", organization_name="Del Org A")
    email = "del-invitee-a@a.com"
    assert _invite(client, headers_a, email).status_code == 201
    target = _get_user(email)

    headers_b, _ = auth_headers(client, email="del-b@b.com", organization_name="Del Org B")
    resp = client.delete(f"/api/v1/users/{target.id}", headers=headers_b)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Re-invite of a revoked email re-activates the row
# ---------------------------------------------------------------------------


def test_reinvite_revoked_email_reactivates(client):
    headers, _ = auth_headers(client)
    email = "reinvite@example.com"
    assert _invite(client, headers, email).status_code == 201
    user_id = _get_user(email).id

    # Revoke.
    assert client.delete(f"/api/v1/users/{user_id}", headers=headers).status_code == 204

    # Re-invite same email → re-activates the SAME row and succeeds.
    resp = _invite(client, headers, email, full_name="Reinvited Person")
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["id"] == user_id
    assert data["status"] == "invited"

    refreshed = _get_user(email)
    assert refreshed.is_active is True
    assert refreshed.full_name == "Reinvited Person"

    # Shows up in the list again.
    list_resp = client.get("/api/v1/users/", headers=headers)
    assert email in [m["email"] for m in list_resp.json()]


def test_reinvite_pending_invite_gives_resend_hint(client):
    # Re-inviting a still-pending (not yet accepted) invite is rejected with a
    # message pointing at Resend invite — not a terse "Email already exists".
    headers, _ = auth_headers(client)
    email = "dup@example.com"
    assert _invite(client, headers, email).status_code == 201
    resp = _invite(client, headers, email)
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "pending invite" in detail
    assert "Resend invite" in detail


def test_invite_self_400(client):
    headers, _ = auth_headers(client, email="owner-self@ex.com", organization_name="Self Org")
    resp = _invite(client, headers, "owner-self@ex.com")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "You're already a member of this workspace."


def test_invite_existing_verified_member_400(client):
    # Invite → accept (member becomes verified/active) → re-invite same email.
    headers, _ = auth_headers(client, email="own@ex.com", organization_name="Verified Member Org")
    email = "member-accepted@ex.com"
    assert _invite(client, headers, email).status_code == 201
    member = _get_user(email)
    accept = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": generate_invite_token(member), "password": "MemberPass123!"},
    )
    assert accept.status_code == 200, accept.text
    resp = _invite(client, headers, email)
    assert resp.status_code == 400
    assert "already a member of this workspace" in resp.json()["detail"]


def test_invite_email_from_other_org_400_without_leaking(client):
    # Email registered in org A; org B owner tries to invite it → generic
    # "already in use", never revealing the other-workspace membership.
    headers_a, _ = auth_headers(client, email="a-owner@a.com", organization_name="Leak Org A")
    email = "shared@a.com"
    assert _invite(client, headers_a, email).status_code == 201

    headers_b, _ = auth_headers(client, email="b-owner@b.com", organization_name="Leak Org B")
    resp = _invite(client, headers_b, email)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "This email address is already in use."


def test_unverified_owner_serializes_active_not_invited(client):
    # An owner whose email was never verified (e.g. legacy account) must still
    # read as active — owners are never a pending invite.
    from app.schemas.user import UserResponse

    owner = UserResponse.model_validate(
        {
            "id": 1,
            "email": "legacy-owner@ex.com",
            "is_active": True,
            "is_verified": False,
            "role": "owner",
            "created_at": "2024-01-01T00:00:00",
        }
    )
    assert owner.status == "active"

    member = UserResponse.model_validate(
        {
            "id": 2,
            "email": "pending@ex.com",
            "is_active": True,
            "is_verified": False,
            "role": "member",
            "created_at": "2024-01-01T00:00:00",
        }
    )
    assert member.status == "invited"


def test_reinvite_removed_verified_member_restores_without_email(client):
    headers, _ = auth_headers(client)
    email = "restore-verified@example.com"
    assert _invite(client, headers, email).status_code == 201
    user = _get_user(email)
    user_id = user.id

    # Accept so the member is verified, then remove them.
    token = generate_invite_token(user)
    accept = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "password": "NewPass123!"},
    )
    assert accept.status_code == 200
    assert client.delete(f"/api/v1/users/{user_id}", headers=headers).status_code == 204

    # Re-invite → restores the SAME verified row; no invite email, no rename.
    resp = _invite(client, headers, email, full_name="Should Not Rename")
    assert resp.status_code in (200, 201), resp.text
    data = resp.json()
    assert data["id"] == user_id
    assert data["status"] == "active"
    assert data["email_sent"] is False

    refreshed = _get_user(email)
    assert refreshed.is_active is True
    assert refreshed.is_verified is True
    assert refreshed.full_name != "Should Not Rename"

    # Back in the team list.
    list_resp = client.get("/api/v1/users/", headers=headers)
    assert email in [m["email"] for m in list_resp.json()]


# ---------------------------------------------------------------------------
# SSO enforcement blocks accept-invite
# ---------------------------------------------------------------------------


def test_accept_invite_sso_enforced_400(client):
    headers, _ = auth_headers(client)
    email = "sso-invitee@example.com"
    assert _invite(client, headers, email).status_code == 201
    user = _get_user(email)
    token = generate_invite_token(user)

    # Org enables SSO enforcement AFTER the invite went out.
    patch = client.patch(
        "/api/v1/organizations/me",
        json={"sso_enforced": True},
        headers=headers,
    )
    assert patch.status_code == 200, patch.text

    resp = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "password": "NewPass123!"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVITE_SSO_REQUIRED"

    # Password was NOT set and the user stays unverified.
    assert _get_user(email).is_verified is False
