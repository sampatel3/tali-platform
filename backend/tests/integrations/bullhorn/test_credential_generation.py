"""Atomic Bullhorn refresh-token lineage fencing."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event

from app.components.integrations.bullhorn import credential_state
from app.components.integrations.bullhorn.credential_state import (
    BullhornCredentialSuperseded,
)
from app.models.organization import Organization
from app.platform.config import settings
from app.platform.secrets import decrypt_text, encrypt_text
from tests.conftest import TestingSessionLocal, engine


def _org(db) -> Organization:
    org = Organization(
        name="Bullhorn credential fence",
        slug=f"bh-cred-{uuid.uuid4().hex[:10]}",
        bullhorn_connected=True,
        bullhorn_credential_generation=1,
        bullhorn_refresh_token=encrypt_text("lineage-one", settings.SECRET_KEY),
    )
    db.add(org)
    db.commit()
    return org


def test_current_generation_rotation_commits(db, monkeypatch):
    org = _org(db)
    monkeypatch.setattr(credential_state, "SessionLocal", TestingSessionLocal)

    credential_state.persist_rotated_credentials(
        org_id=org.id,
        expected_generation=1,
        refresh_token="rotated-one",
        rest_url="https://rest.example.test/rest-services/acme/",
    )

    db.expire_all()
    fresh = db.query(Organization).filter(Organization.id == org.id).one()
    assert decrypt_text(fresh.bullhorn_refresh_token, settings.SECRET_KEY) == "rotated-one"
    assert fresh.bullhorn_credential_generation == 1
    assert fresh.bullhorn_rest_url == "https://rest.example.test/rest-services/acme/"


def test_reconnect_committing_between_stale_writer_start_and_update_wins(
    db, monkeypatch
):
    """Force the interleaving the CAS exists to fence.

    The old client starts its persistence call. Immediately before its
    conditional UPDATE reaches SQLite, a separate reconnect transaction commits
    generation 2 plus the new refresh token. The stale UPDATE must then affect
    zero rows and can never overwrite generation 2.
    """
    org = _org(db)
    monkeypatch.setattr(credential_state, "SessionLocal", TestingSessionLocal)
    injected = {"done": False}

    def _commit_reconnect_before_stale_update(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ):
        normalized = " ".join(statement.lower().split())
        if (
            injected["done"]
            or not normalized.startswith("update organizations")
            or "bullhorn_credential_generation" not in normalized
        ):
            return
        injected["done"] = True
        reconnect_db = TestingSessionLocal()
        try:
            reconnect_org = (
                reconnect_db.query(Organization)
                .filter(Organization.id == org.id)
                .one()
            )
            reconnect_org.bullhorn_credential_generation = 2
            reconnect_org.bullhorn_refresh_token = encrypt_text(
                "lineage-two", settings.SECRET_KEY
            )
            reconnect_db.commit()
        finally:
            reconnect_db.close()

    event.listen(engine, "before_cursor_execute", _commit_reconnect_before_stale_update)
    try:
        with pytest.raises(BullhornCredentialSuperseded):
            credential_state.persist_rotated_credentials(
                org_id=org.id,
                expected_generation=1,
                refresh_token="late-lineage-one",
            )
    finally:
        event.remove(
            engine,
            "before_cursor_execute",
            _commit_reconnect_before_stale_update,
        )

    assert injected["done"] is True
    db.expire_all()
    fresh = db.query(Organization).filter(Organization.id == org.id).one()
    assert fresh.bullhorn_credential_generation == 2
    assert decrypt_text(fresh.bullhorn_refresh_token, settings.SECRET_KEY) == "lineage-two"
