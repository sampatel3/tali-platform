"""Tests for the Resend delivery-webhook service.

Covers Svix signature verification (valid / tampered / wrong-secret) and
event → assessment mapping (delivered / opened / bounced), including the
no-downgrade rule and the failure-wins rule.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone

from app.models.assessment import Assessment
from app.models.candidate import Candidate
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.services.resend_webhook_service import (
    apply_resend_event,
    verify_resend_webhook_signature,
)


_SECRET = "whsec_" + base64.b64encode(b"super-secret-key-bytes").decode()


def _sign(secret: str, svix_id: str, svix_timestamp: str, body: bytes) -> str:
    key = base64.b64decode(secret[len("whsec_"):])
    signed = b"%s.%s.%s" % (svix_id.encode(), svix_timestamp.encode(), body)
    sig = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return f"v1,{sig}"


def _now_ts() -> str:
    return str(int(datetime.now(timezone.utc).timestamp()))


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def test_valid_signature_passes():
    body = b'{"type":"email.delivered","data":{"email_id":"re_1"}}'
    sid, ts = "msg_1", _now_ts()
    sig = _sign(_SECRET, sid, ts, body)
    assert verify_resend_webhook_signature(
        secret=_SECRET, svix_id=sid, svix_timestamp=ts, svix_signature=sig, body=body
    )


def test_tampered_body_fails():
    body = b'{"type":"email.delivered","data":{"email_id":"re_1"}}'
    sid, ts = "msg_1", _now_ts()
    sig = _sign(_SECRET, sid, ts, body)
    assert not verify_resend_webhook_signature(
        secret=_SECRET,
        svix_id=sid,
        svix_timestamp=ts,
        svix_signature=sig,
        body=body + b" ",  # tampered
    )


def test_wrong_secret_fails():
    body = b'{"x":1}'
    sid, ts = "msg_1", _now_ts()
    sig = _sign(_SECRET, sid, ts, body)
    other = "whsec_" + base64.b64encode(b"different-key").decode()
    assert not verify_resend_webhook_signature(
        secret=other, svix_id=sid, svix_timestamp=ts, svix_signature=sig, body=body
    )


def test_stale_timestamp_fails():
    body = b'{"x":1}'
    sid = "msg_1"
    ts = str(int(datetime.now(timezone.utc).timestamp()) - 10_000)  # way out of window
    sig = _sign(_SECRET, sid, ts, body)
    assert not verify_resend_webhook_signature(
        secret=_SECRET, svix_id=sid, svix_timestamp=ts, svix_signature=sig, body=body
    )


# ---------------------------------------------------------------------------
# Event application
# ---------------------------------------------------------------------------


def _make_assessment(db, email_id: str = "re_abc") -> Assessment:
    org = Organization(name="Acme", slug=f"org-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Backend", source="manual")
    task = Task(name="T", task_key=f"t-{id(db)}", organization_id=org.id, is_active=True)
    cand = Candidate(organization_id=org.id, email="a@x.test", full_name="A")
    db.add_all([role, task, cand])
    db.flush()
    a = Assessment(
        organization_id=org.id,
        candidate_id=cand.id,
        task_id=task.id,
        role_id=role.id,
        token="tok",
        duration_minutes=60,
        expires_at=datetime.now(timezone.utc),
        invite_email_id=email_id,
        invite_email_status="sent",
    )
    db.add(a)
    db.flush()
    return a


def test_delivered_event_sets_status_and_timestamp(db):
    a = _make_assessment(db, "re_d")
    res = apply_resend_event(db, {"type": "email.delivered", "data": {"email_id": "re_d"}})
    assert res["status"] == "applied"
    db.refresh(a)
    assert a.invite_email_status == "delivered"
    assert a.invite_delivered_at is not None


def test_opened_event_then_late_delivered_does_not_downgrade(db):
    a = _make_assessment(db, "re_o")
    apply_resend_event(db, {"type": "email.opened", "data": {"email_id": "re_o"}})
    db.refresh(a)
    assert a.invite_email_status == "opened"
    # A late 'delivered' must not downgrade an already-opened invite.
    apply_resend_event(db, {"type": "email.delivered", "data": {"email_id": "re_o"}})
    db.refresh(a)
    assert a.invite_email_status == "opened"
    assert a.invite_opened_at is not None


def test_bounced_event_wins_and_records(db):
    a = _make_assessment(db, "re_b")
    apply_resend_event(db, {"type": "email.delivered", "data": {"email_id": "re_b"}})
    apply_resend_event(db, {"type": "email.bounced", "data": {"email_id": "re_b"}})
    db.refresh(a)
    assert a.invite_email_status == "bounced"
    assert a.invite_bounced_at is not None


def test_unknown_email_id_is_ignored(db):
    _make_assessment(db, "re_known")
    res = apply_resend_event(db, {"type": "email.delivered", "data": {"email_id": "re_other"}})
    assert res["status"] == "ignored"
    assert res["reason"] == "no_matching_assessment"


def test_missing_email_id_is_ignored(db):
    res = apply_resend_event(db, {"type": "email.delivered", "data": {}})
    assert res["status"] == "ignored"
    assert res["reason"] == "no_email_id"


# ---------------------------------------------------------------------------
# Suppression wiring — bounce / complaint → platform-global suppression
# ---------------------------------------------------------------------------


def test_bounce_event_adds_global_suppression(db):
    a = _make_assessment(db, "re_sup_b")
    apply_resend_event(
        db,
        {
            "type": "email.bounced",
            "data": {"email_id": "re_sup_b", "to": ["Bounce@Example.com"]},
        },
    )
    # Suppressed globally (org NULL), so it protects every org.
    from app.services.email_suppression_service import is_suppressed

    db.refresh(a)
    assert is_suppressed(db, email="bounce@example.com", organization_id=a.organization_id) == "bounced"


def test_complaint_event_adds_global_suppression(db):
    apply_resend_event(
        db,
        {
            "type": "email.complained",
            "data": {"email_id": "re_unknown", "to": ["spam@example.com"]},
        },
    )
    # Even with no matching assessment, the complaint suppresses the address.
    from app.models.organization import Organization
    from app.services.email_suppression_service import is_suppressed

    org = Organization(name="Any", slug=f"org-any-{id(db)}")
    db.add(org)
    db.flush()
    assert is_suppressed(db, email="spam@example.com", organization_id=org.id) == "complained"


def test_delivered_event_does_not_suppress(db):
    _make_assessment(db, "re_sup_d")
    apply_resend_event(
        db,
        {
            "type": "email.delivered",
            "data": {"email_id": "re_sup_d", "to": ["fine@example.com"]},
        },
    )
    from app.models.email_suppression import EmailSuppression

    assert (
        db.query(EmailSuppression)
        .filter(EmailSuppression.email_normalized == "fine@example.com")
        .first()
        is None
    )
