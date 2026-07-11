"""Auth hardening: per-account lockout, sliding-session refresh, auth_events audit trail."""

from datetime import datetime, timedelta, timezone

from app.models.auth_event import (
    AUTH_EVENT_ACCOUNT_LOCKED,
    AUTH_EVENT_LOGIN_FAILED,
    AUTH_EVENT_LOGIN_SUCCESS,
    AUTH_EVENT_MEMBER_INVITED,
    AUTH_EVENT_PASSWORD_RESET_REQUESTED,
    AuthEvent,
)
from app.models.user import User
from app.platform.config import settings

from tests.conftest import (
    TestingSessionLocal,
    auth_headers,
    login_user,
    register_user,
    verify_user,
)


def _get_user(email):
    db = TestingSessionLocal()
    try:
        return db.query(User).filter(User.email == email).first()
    finally:
        db.close()


def _set_user_fields(email, **fields):
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        for k, v in fields.items():
            setattr(user, k, v)
        db.commit()
    finally:
        db.close()


def _events(event_type=None):
    db = TestingSessionLocal()
    try:
        q = db.query(AuthEvent)
        if event_type:
            q = q.filter(AuthEvent.event_type == event_type)
        return q.order_by(AuthEvent.id.asc()).all()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Per-account lockout
# ---------------------------------------------------------------------------


def test_lockout_after_threshold_failures(client):
    email = "lockout-1@test.com"
    register_user(client, email=email)
    verify_user(email)

    for _ in range(settings.AUTH_LOCKOUT_THRESHOLD):
        resp = login_user(client, email, password="WrongPass!!")
        assert resp.status_code == 400

    user = _get_user(email)
    assert user.failed_login_attempts == settings.AUTH_LOCKOUT_THRESHOLD
    assert user.locked_until is not None

    # Even the CORRECT password is rejected while locked
    resp = login_user(client, email)
    assert resp.status_code == 429
    assert "Too many failed login attempts" in resp.json()["detail"]

    locked_events = _events(AUTH_EVENT_ACCOUNT_LOCKED)
    assert len(locked_events) == 1
    assert locked_events[0].email == email
    assert locked_events[0].event_metadata["lock_minutes"] == settings.AUTH_LOCKOUT_MINUTES


def test_lockout_expires_and_failed_count_restarts(client):
    email = "lockout-2@test.com"
    register_user(client, email=email)
    verify_user(email)

    _set_user_fields(
        email,
        failed_login_attempts=settings.AUTH_LOCKOUT_THRESHOLD,
        locked_until=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    # Lock expired: bad password is a normal 400 and the count restarts at 1
    resp = login_user(client, email, password="WrongPass!!")
    assert resp.status_code == 400
    user = _get_user(email)
    assert user.failed_login_attempts == 1
    assert user.locked_until is None


def test_successful_login_resets_counter(client):
    email = "lockout-3@test.com"
    register_user(client, email=email)
    verify_user(email)

    for _ in range(2):
        assert login_user(client, email, password="WrongPass!!").status_code == 400
    assert _get_user(email).failed_login_attempts == 2

    resp = login_user(client, email)
    assert resp.status_code == 200
    user = _get_user(email)
    assert user.failed_login_attempts == 0
    assert user.locked_until is None


def test_locked_account_correct_password_does_not_unlock(client):
    email = "lockout-4@test.com"
    register_user(client, email=email)
    verify_user(email)

    _set_user_fields(
        email,
        failed_login_attempts=settings.AUTH_LOCKOUT_THRESHOLD,
        locked_until=datetime.now(timezone.utc) + timedelta(minutes=10),
    )

    resp = login_user(client, email)
    assert resp.status_code == 429
    # Lock stays in place
    assert _get_user(email).locked_until is not None


# ---------------------------------------------------------------------------
# Sliding-session refresh
# ---------------------------------------------------------------------------


def test_refresh_returns_working_token(client):
    headers, email = auth_headers(client)

    resp = client.post("/api/v1/auth/jwt/refresh", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    new_token = body["access_token"]
    assert new_token

    me = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {new_token}"})
    assert me.status_code == 200
    assert me.json()["email"] == email


def test_refresh_requires_auth(client):
    resp = client.post("/api/v1/auth/jwt/refresh")
    assert resp.status_code == 401


def test_refresh_refuses_token_minted_before_password_change(client):
    headers, email = auth_headers(client)

    # Password changed AFTER this token was minted → the token must not slide
    _set_user_fields(
        email, password_changed_at=datetime.now(timezone.utc) + timedelta(seconds=5)
    )
    resp = client.post("/api/v1/auth/jwt/refresh", headers=headers)
    assert resp.status_code == 401

    # Change in the past (before mint) → refresh works
    _set_user_fields(
        email, password_changed_at=datetime.now(timezone.utc) - timedelta(hours=1)
    )
    resp = client.post("/api/v1/auth/jwt/refresh", headers=headers)
    assert resp.status_code == 200


def test_password_reset_stamps_refresh_anchor_and_clears_lockout(client):
    email = "reset-anchor@test.com"
    register_user(client, email=email)
    verify_user(email)
    _set_user_fields(
        email,
        failed_login_attempts=3,
        locked_until=datetime.now(timezone.utc) + timedelta(minutes=10),
    )

    # Build the reset token exactly as fastapi-users' forgot_password does
    # (it binds the token to a fingerprint of the current password hash).
    from fastapi_users.jwt import generate_jwt
    from fastapi_users.password import PasswordHelper

    user = _get_user(email)
    reset_token = generate_jwt(
        {
            "sub": str(user.id),
            "password_fgpt": PasswordHelper().hash(user.hashed_password),
            "aud": "fastapi-users:reset",
        },
        settings.SECRET_KEY,
        3600,
    )
    resp = client.post(
        "/api/v1/auth/reset-password",
        json={"token": reset_token, "password": "BrandNewPass1!"},
    )
    assert resp.status_code == 200, resp.text

    user = _get_user(email)
    assert user.failed_login_attempts == 0
    assert user.locked_until is None
    assert user.password_changed_at is not None


def test_accept_invite_clears_lockout_and_stamps_anchor(client):
    headers, _ = auth_headers(client, organization_name="InviteLockOrg")
    resp = client.post(
        "/api/v1/users/invite",
        json={"email": "locked-invitee@test.com", "full_name": "Locked Invitee"},
        headers=headers,
    )
    assert resp.status_code == 201

    # Attacker hammered the pending invite's login before it was accepted
    _set_user_fields(
        "locked-invitee@test.com",
        failed_login_attempts=settings.AUTH_LOCKOUT_THRESHOLD,
        locked_until=datetime.now(timezone.utc) + timedelta(minutes=10),
    )

    from app.domains.identity_access.user_routes import generate_invite_token

    invite_token = generate_invite_token(_get_user("locked-invitee@test.com"))
    resp = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": invite_token, "password": "InviteePass1!"},
    )
    assert resp.status_code == 200, resp.text

    user = _get_user("locked-invitee@test.com")
    assert user.failed_login_attempts == 0
    assert user.locked_until is None
    assert user.password_changed_at is not None

    # The freshly minted accept-invite token can slide
    token = resp.json()["access_token"]
    resp = client.post(
        "/api/v1/auth/jwt/refresh", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# auth_events audit trail
# ---------------------------------------------------------------------------


def test_login_success_and_failure_events(client):
    email = "audit-1@test.com"
    register_user(client, email=email)
    verify_user(email)

    assert login_user(client, email, password="WrongPass!!").status_code == 400
    assert login_user(client, email).status_code == 200

    failed = _events(AUTH_EVENT_LOGIN_FAILED)
    assert len(failed) == 1
    assert failed[0].email == email
    assert failed[0].user_id is not None
    assert failed[0].event_metadata["reason"] == "bad_password"
    assert failed[0].ip_address  # captured via request-context middleware

    success = _events(AUTH_EVENT_LOGIN_SUCCESS)
    assert len(success) == 1
    assert success[0].email == email


def test_unknown_email_failed_login_recorded(client):
    resp = login_user(client, "nobody@test.com", password="whatever123")
    assert resp.status_code == 400

    failed = _events(AUTH_EVENT_LOGIN_FAILED)
    assert len(failed) == 1
    assert failed[0].email == "nobody@test.com"
    assert failed[0].user_id is None
    assert failed[0].event_metadata["reason"] == "unknown_email"


def test_forgot_password_event(client):
    email = "audit-2@test.com"
    register_user(client, email=email)
    verify_user(email)

    resp = client.post("/api/v1/auth/forgot-password", json={"email": email})
    assert resp.status_code == 202

    events = _events(AUTH_EVENT_PASSWORD_RESET_REQUESTED)
    assert len(events) == 1
    assert events[0].email == email


def test_invite_event_records_actor(client):
    headers, inviter_email = auth_headers(client, organization_name="AuditOrg")

    resp = client.post(
        "/api/v1/users/invite",
        json={"email": "invitee@test.com", "full_name": "New Member"},
        headers=headers,
    )
    assert resp.status_code == 201

    events = _events(AUTH_EVENT_MEMBER_INVITED)
    assert len(events) == 1
    event = events[0]
    assert event.email == "invitee@test.com"
    assert event.actor_user_id == _get_user(inviter_email).id
    assert event.organization_id is not None
