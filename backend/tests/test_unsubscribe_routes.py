"""Tests for the public unsubscribe routes.

GET validates + masks (no write); POST records an org-scoped suppression and is
idempotent; tampered/invalid tokens 404.
"""

from __future__ import annotations

import uuid

from app.models.organization import Organization
from app.services.email_suppression_service import (
    is_suppressed,
    make_unsubscribe_token,
)


def _make_org(db) -> Organization:
    org = Organization(name="Acme Corp", slug=f"org-{uuid.uuid4().hex}")
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def test_get_returns_org_and_masked_email_without_suppressing(client, db):
    org = _make_org(db)
    token = make_unsubscribe_token(org.id, "jane@acme.com")

    resp = client.get(f"/api/v1/public/unsubscribe/{token}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["organization_name"] == "Acme Corp"
    assert body["email_masked"] == "j***@acme.com"

    # GET must NOT suppress.
    assert is_suppressed(db, email="jane@acme.com", organization_id=org.id) is None


def test_post_records_org_scoped_suppression(client, db):
    org = _make_org(db)
    token = make_unsubscribe_token(org.id, "jane@acme.com")

    resp = client.post(f"/api/v1/public/unsubscribe/{token}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "unsubscribed"
    assert is_suppressed(db, email="jane@acme.com", organization_id=org.id) == "unsubscribed"


def test_post_is_idempotent(client, db):
    org = _make_org(db)
    token = make_unsubscribe_token(org.id, "jane@acme.com")
    assert client.post(f"/api/v1/public/unsubscribe/{token}").status_code == 200
    assert client.post(f"/api/v1/public/unsubscribe/{token}").status_code == 200

    from app.models.email_suppression import EmailSuppression

    rows = (
        db.query(EmailSuppression)
        .filter(
            EmailSuppression.organization_id == org.id,
            EmailSuppression.email_normalized == "jane@acme.com",
        )
        .all()
    )
    assert len(rows) == 1


def test_tampered_token_404_on_get_and_post(client, db):
    org = _make_org(db)
    token = make_unsubscribe_token(org.id, "jane@acme.com")
    bad = token[:-3] + "zzz"
    assert client.get(f"/api/v1/public/unsubscribe/{bad}").status_code == 404
    assert client.post(f"/api/v1/public/unsubscribe/{bad}").status_code == 404
    # Nothing recorded.
    assert is_suppressed(db, email="jane@acme.com", organization_id=org.id) is None


def test_unsubscribe_needs_no_auth(client, db):
    org = _make_org(db)
    token = make_unsubscribe_token(org.id, "jane@acme.com")
    # No Authorization header at all.
    assert client.post(f"/api/v1/public/unsubscribe/{token}").status_code == 200
