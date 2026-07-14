"""Bullhorn connect establishes the correct organization ATS posture."""

from app.domains.bullhorn_sync import connect
from app.models.organization import (
    Organization,
    SYNC_MODE_BULLHORN_PRIMARY,
    SYNC_MODE_WORKABLE_PRIMARY,
)


class _Auth:
    _refresh_token = "rotated-refresh-token"
    _cached_rest_url = "https://rest.example.test/rest-services/acme/"

    def discover(self):
        return None

    def authorize_with_password(self):
        return None


class _Service:
    _ENTITLEMENTS = {
        "Candidate": ["GET"],
        "JobOrder": ["GET"],
        "JobSubmission": ["GET", "POST"],
        "Note": ["GET", "PUT"],
    }

    def __init__(self, _auth, *, client_id):
        self.client_id = client_id

    def ping(self):
        return {"ok": True}

    def get_entitlements(self, entity):
        return self._ENTITLEMENTS[entity]

    def get_status_list(self):
        return {"statuses": [], "categorization": {}}


def _run_connect(db, monkeypatch, org):
    monkeypatch.setattr(connect, "build_connect_auth", lambda **_kwargs: _Auth())
    monkeypatch.setattr(connect, "BullhornService", _Service)
    return connect.run_connect(
        db,
        org,
        username="api.user",
        client_id="client-id",
        client_secret="client-secret",
        password="one-time-password",
    )


def test_bullhorn_only_connect_sets_bullhorn_primary(db, monkeypatch):
    org = Organization(name="Bullhorn primary", sync_mode="standalone")
    db.add(org)
    db.flush()

    _run_connect(db, monkeypatch, org)

    assert org.bullhorn_connected is True
    assert org.sync_mode == SYNC_MODE_BULLHORN_PRIMARY


def test_dual_connect_preserves_incumbent_workable_posture(db, monkeypatch):
    org = Organization(
        name="Dual connected",
        sync_mode=SYNC_MODE_WORKABLE_PRIMARY,
        workable_connected=True,
        workable_access_token="workable-token",
        workable_subdomain="deeplight",
    )
    db.add(org)
    db.flush()

    _run_connect(db, monkeypatch, org)

    assert org.bullhorn_connected is True
    assert org.sync_mode == SYNC_MODE_WORKABLE_PRIMARY
