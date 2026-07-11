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


def test_reinvite_existing_active_email_still_400(client):
    headers, _ = auth_headers(client)
    email = "dup@example.com"
    assert _invite(client, headers, email).status_code == 201
    resp = _invite(client, headers, email)
    assert resp.status_code == 400
    assert "Email already exists" in resp.json()["detail"]
