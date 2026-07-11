"""Tests for the email suppression service — upsert, precedence, bulk check,
and the signed unsubscribe token round-trip / tamper rejection.
"""

from __future__ import annotations

import uuid

from app.models.organization import Organization
from app.services.email_suppression_service import (
    is_suppressed,
    make_unsubscribe_token,
    normalize_email,
    suppress,
    suppressed_set,
    verify_unsubscribe_token,
)


def _org(db, name="Acme") -> Organization:
    org = Organization(name=name, slug=f"org-{uuid.uuid4().hex}")
    db.add(org)
    db.flush()
    return org


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------


def test_normalize_lowercases_and_trims():
    assert normalize_email("  Jane@ACME.com ") == "jane@acme.com"
    assert normalize_email(None) == ""


# ---------------------------------------------------------------------------
# upsert idempotency + reason precedence
# ---------------------------------------------------------------------------


def test_suppress_is_idempotent_no_duplicate(db):
    org = _org(db)
    suppress(db, email="a@x.test", reason="unsubscribed", source="link", organization_id=org.id)
    suppress(db, email="A@X.test", reason="unsubscribed", source="link", organization_id=org.id)
    from app.models.email_suppression import EmailSuppression

    rows = db.query(EmailSuppression).filter(EmailSuppression.organization_id == org.id).all()
    assert len(rows) == 1


def test_stronger_reason_overwrites_weaker(db):
    org = _org(db)
    suppress(db, email="a@x.test", reason="unsubscribed", source="link", organization_id=org.id)
    # complained > bounced > unsubscribed > manual
    suppress(db, email="a@x.test", reason="complained", source="webhook", organization_id=org.id)
    assert is_suppressed(db, email="a@x.test", organization_id=org.id) == "complained"


def test_weaker_reason_does_not_downgrade(db):
    org = _org(db)
    suppress(db, email="a@x.test", reason="bounced", source="webhook", organization_id=org.id)
    suppress(db, email="a@x.test", reason="manual", source="recruiter", organization_id=org.id)
    assert is_suppressed(db, email="a@x.test", organization_id=org.id) == "bounced"


# ---------------------------------------------------------------------------
# global vs org rows
# ---------------------------------------------------------------------------


def test_global_row_deduped_in_code(db):
    # Postgres treats NULL as distinct in the unique constraint; the service
    # must dedupe global rows itself.
    suppress(db, email="g@x.test", reason="bounced", source="webhook", organization_id=None)
    suppress(db, email="g@x.test", reason="bounced", source="webhook", organization_id=None)
    from app.models.email_suppression import EmailSuppression

    rows = (
        db.query(EmailSuppression)
        .filter(EmailSuppression.organization_id.is_(None))
        .filter(EmailSuppression.email_normalized == "g@x.test")
        .all()
    )
    assert len(rows) == 1


def test_global_row_suppresses_across_orgs(db):
    org_a = _org(db, "A")
    org_b = _org(db, "B")
    suppress(db, email="g@x.test", reason="complained", source="webhook", organization_id=None)
    assert is_suppressed(db, email="g@x.test", organization_id=org_a.id) == "complained"
    assert is_suppressed(db, email="g@x.test", organization_id=org_b.id) == "complained"


def test_org_row_is_isolated_to_that_org(db):
    org_a = _org(db, "A")
    org_b = _org(db, "B")
    suppress(db, email="u@x.test", reason="unsubscribed", source="link", organization_id=org_a.id)
    assert is_suppressed(db, email="u@x.test", organization_id=org_a.id) == "unsubscribed"
    assert is_suppressed(db, email="u@x.test", organization_id=org_b.id) is None


def test_is_suppressed_returns_strongest_when_both_rows_exist(db):
    org = _org(db)
    suppress(db, email="c@x.test", reason="manual", source="recruiter", organization_id=org.id)
    suppress(db, email="c@x.test", reason="complained", source="webhook", organization_id=None)
    # Global complaint outranks the org-scoped manual block.
    assert is_suppressed(db, email="c@x.test", organization_id=org.id) == "complained"


def test_not_suppressed_returns_none(db):
    org = _org(db)
    assert is_suppressed(db, email="nobody@x.test", organization_id=org.id) is None


# ---------------------------------------------------------------------------
# bulk check
# ---------------------------------------------------------------------------


def test_suppressed_set_bulk(db):
    org = _org(db)
    suppress(db, email="a@x.test", reason="unsubscribed", source="link", organization_id=org.id)
    suppress(db, email="g@x.test", reason="bounced", source="webhook", organization_id=None)
    result = suppressed_set(
        db, emails=["A@X.test", "g@x.test", "clean@x.test"], organization_id=org.id
    )
    assert result == {"a@x.test": "unsubscribed", "g@x.test": "bounced"}


def test_suppressed_set_empty_input(db):
    org = _org(db)
    assert suppressed_set(db, emails=[], organization_id=org.id) == {}


# ---------------------------------------------------------------------------
# signed unsubscribe token
# ---------------------------------------------------------------------------


def test_token_roundtrip():
    token = make_unsubscribe_token(42, "Jane@Acme.com")
    parsed = verify_unsubscribe_token(token)
    assert parsed == (42, "jane@acme.com")


def test_token_tamper_rejected():
    token = make_unsubscribe_token(42, "jane@acme.com")
    payload_b64, _, sig = token.partition(".")
    tampered = f"{payload_b64}.{sig[:-2]}xy"
    assert verify_unsubscribe_token(tampered) is None


def test_token_garbage_rejected():
    assert verify_unsubscribe_token("") is None
    assert verify_unsubscribe_token("not-a-token") is None
    assert verify_unsubscribe_token("abc.def") is None
