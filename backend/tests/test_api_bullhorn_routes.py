"""Route tests for the Bullhorn sync domain (``/api/v1/bullhorn/*``, PR-6).

Mirrors ``test_api_workable_sync.py``'s structure. Covers:

* the HARD gating rule — EVERY route 503s when ``BULLHORN_ENABLED`` is False;
* the full connect flow against the fake Bullhorn server (discovery → OAuth →
  ping smoke → entitlement pre-flight → status fetch → stage-map seed → encrypted
  persist), including a missing-entitlement failure with a per-entity message;
* SECURITY — the one-time password NEVER appears in the connect response, the
  status/diagnostic responses, or the logs; ``client_secret`` / ``refresh_token``
  are stored as ciphertext and never echoed;
* status / sync trigger (mutex-aware already-running) / sync cancel;
* stage-map list + replace (validation against PIPELINE_STAGES);
* admin diagnostic gating + credential redaction.

The connect flow is driven through the real ``BullhornAuth`` against the live
fake server by monkeypatching the ``connect.build_connect_auth`` seam to inject
the fake's discovery url — the exact analog of how the Workable tests monkeypatch
``WorkableSyncService.sync_org``.
"""

from __future__ import annotations

import logging

import pytest

from app.components.integrations.bullhorn.auth import BullhornAuth
from app.components.integrations.bullhorn import bootstrap as bh_bootstrap
from app.components.integrations.bullhorn import event_state, event_subscriptions
from app.domains.bullhorn_sync import connect as bh_connect
from app.domains.bullhorn_sync import connect_lifecycle as bh_connect_lifecycle
from app.domains.bullhorn_sync import routes as bh_routes
from app.models.ats_stage_map import AtsStageMap
from app.models.organization import Organization
from app.models.user import User
from app.platform.config import settings
from app.platform.secrets import decrypt_text
from tests.conftest import auth_headers
from tests.fakes.bullhorn_state import FakeBullhornState
from tests.fakes.bullhorn_fakes import live_bullhorn_server


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _enable(monkeypatch) -> None:
    """Flip the Bullhorn flag on for the route + connect modules under test."""
    monkeypatch.setattr(bh_routes.settings, "BULLHORN_ENABLED", True)
    monkeypatch.setattr(bh_connect_lifecycle, "_acquire_mutex", lambda _org_id: object())
    monkeypatch.setattr(bh_connect_lifecycle, "_release_mutex", lambda _handle: None)
    # Unit route tests stop at the durable dispatch seam. The route-level E2E
    # suite deliberately leaves this real and runs the eager full sync.
    monkeypatch.setattr(
        bh_bootstrap,
        "_enqueue_initial_full_sync",
        lambda **_kwargs: None,
    )


def _org_for(db, email) -> Organization:
    user = db.query(User).filter(User.email == email).first()
    assert user is not None
    org = db.query(Organization).filter(Organization.id == user.organization_id).first()
    assert org is not None
    return org


def _patch_connect_to_fake(monkeypatch, server, org_state):
    """Point ``connect.build_connect_auth`` at the live fake via its discovery url.

    The route calls the real connect orchestration; only the auth object's
    discovery endpoint is swapped so the flow talks to the fake server. The
    returned auth uses the SAME credentials the connect body will carry.
    """

    def _fake_build_auth(*, username, client_id, client_secret, password):
        return BullhornAuth(
            username=username,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=None,
            persist_tokens=lambda **kw: None,
            discovery_url=server.discovery_url,
            password=password,
        )

    monkeypatch.setattr(bh_connect, "build_connect_auth", _fake_build_auth)


def _connect_body(org_state) -> dict:
    return {
        "username": org_state.username,
        "client_id": org_state.client_id,
        "client_secret": org_state.client_secret,
        "password": org_state.password,
    }


# ---------------------------------------------------------------------------
# gating — every route 503s when the flag is off
# ---------------------------------------------------------------------------


def test_all_routes_503_when_bullhorn_disabled(client, monkeypatch):
    """HARD RULE: with BULLHORN_ENABLED False (the default), every endpoint 503s."""
    monkeypatch.setattr(bh_routes.settings, "BULLHORN_ENABLED", False)
    headers, _ = auth_headers(client, email="bh-off@example.com")

    calls = [
        ("post", "/api/v1/bullhorn/connect", {"username": "u", "client_id": "c", "client_secret": "s", "password": "p"}),
        ("get", "/api/v1/bullhorn/status", None),
        ("post", "/api/v1/bullhorn/sync", {}),
        ("get", "/api/v1/bullhorn/sync/status", None),
        ("post", "/api/v1/bullhorn/sync/cancel", {}),
        ("get", "/api/v1/bullhorn/stage-map", None),
        ("put", "/api/v1/bullhorn/stage-map", {"mappings": []}),
    ]
    for method, path, body in calls:
        fn = getattr(client, method)
        resp = fn(path, headers=headers, json=body) if body is not None else fn(path, headers=headers)
        assert resp.status_code == 503, f"{method.upper()} {path} -> {resp.status_code}, expected 503"
        assert "disabled" in resp.json().get("detail", "").lower()

    # admin diagnostic is 503 too (checked before the admin-secret gate).
    resp = client.get(
        "/api/v1/bullhorn/admin/diagnostic",
        params={"email": "bh-off@example.com"},
        headers={"X-Admin-Secret": settings.ADMIN_SECRET},
    )
    assert resp.status_code == 503


def test_routes_require_auth(client, monkeypatch):
    """Recruiter routes are authed (401 without a token) even when enabled."""
    _enable(monkeypatch)
    assert client.get("/api/v1/bullhorn/status").status_code == 401
    assert client.post("/api/v1/bullhorn/sync", json={}).status_code == 401
    assert client.get("/api/v1/bullhorn/stage-map").status_code == 401


# ---------------------------------------------------------------------------
# connect flow
# ---------------------------------------------------------------------------


def test_connect_success_persists_encrypted_creds_and_seeds_stage_map(client, db, monkeypatch):
    """Full connect: discovery → OAuth → ping → entitlements → status → seed →
    encrypted persist. Asserts the org is connected, the secret + refresh token
    land as CIPHERTEXT (decryptable, not plaintext), and categorization defaults
    seeded stage-map rows."""
    _enable(monkeypatch)
    headers, email = auth_headers(client, email="bh-connect@example.com")

    state = FakeBullhornState()
    org_state = state.make_org("org1")

    with live_bullhorn_server(state) as server:
        _patch_connect_to_fake(monkeypatch, server, org_state)
        resp = client.post("/api/v1/bullhorn/connect", headers=headers, json=_connect_body(org_state))

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["status"] == "connected"
    assert payload["bullhorn_connected"] is True
    assert payload["rest_url_configured"] is True
    assert "rest_url" not in payload
    # DEFAULT_CATEGORIZATION has 3 settings -> 3 seeded rows (interview/confirmed
    # both -> advanced, rejected -> review).
    assert payload["seeded_stage_rows"] == 3

    db.expire_all()
    org = _org_for(db, email)
    assert org.bullhorn_connected is True
    assert org.bullhorn_username == org_state.username
    # Stored as ciphertext, NOT plaintext, and round-trips via decrypt.
    assert org.bullhorn_client_secret != org_state.client_secret
    assert decrypt_text(org.bullhorn_client_secret, settings.SECRET_KEY) == org_state.client_secret
    assert org.bullhorn_refresh_token is not None
    assert decrypt_text(org.bullhorn_refresh_token, settings.SECRET_KEY)  # non-empty

    rows = db.query(AtsStageMap).filter(AtsStageMap.org_id == org.id, AtsStageMap.ats == "bullhorn").all()
    assert {r.remote_status for r in rows} == {"Interview Scheduled", "Placed", "Client Rejected"}


def test_connect_durably_enqueues_tracked_initial_full_sync(client, db, monkeypatch):
    """Connect commits a FULL-sync outbox marker, then dispatches that run."""
    _enable(monkeypatch)
    headers, email = auth_headers(client, email="bh-bootstrap@example.com")
    state = FakeBullhornState()
    org_state = state.make_org("org1")
    dispatched: list[dict] = []
    monkeypatch.setattr(
        bh_bootstrap,
        "_enqueue_initial_full_sync",
        lambda **kwargs: dispatched.append(kwargs),
    )

    with live_bullhorn_server(state) as server:
        _patch_connect_to_fake(monkeypatch, server, org_state)
        resp = client.post(
            "/api/v1/bullhorn/connect",
            headers=headers,
            json=_connect_body(org_state),
        )

    assert resp.status_code == 200, resp.text
    signal = resp.json()["initial_sync"]
    assert signal["mode"] == "full"
    assert signal["status"] == "queued"
    assert signal["status_path"] == "/api/v1/bullhorn/sync/status"
    assert dispatched == [
        {
            "org_id": _org_for(db, email).id,
            "run_id": signal["run_id"],
            "mode": "full",
            "trigger": bh_bootstrap.CONNECT_BOOTSTRAP_TRIGGER,
        }
    ]

    db.expire_all()
    org = _org_for(db, email)
    assert org.bullhorn_sync_progress["run_id"] == signal["run_id"]
    assert org.bullhorn_sync_progress["mode"] == "full"
    assert org.bullhorn_sync_progress["trigger"] == "connect_bootstrap"
    assert org.bullhorn_sync_progress["dispatch_attempts"] == 1

    polled = client.get(signal["status_path"], headers=headers)
    assert polled.status_code == 200, polled.text
    assert polled.json()["sync_progress"]["run_id"] == signal["run_id"]
    assert polled.json()["sync_progress"]["mode"] == "full"
    assert polled.json()["initial_sync"]["run_id"] == signal["run_id"]


def test_connect_password_never_in_response_or_logs(client, db, monkeypatch, caplog):
    """SECURITY: the one-time password must NOT appear in the connect response,
    nor in any log line emitted during connect. The secret + refresh token must
    also never be echoed in the response body."""
    _enable(monkeypatch)
    headers, email = auth_headers(client, email="bh-secret@example.com")

    state = FakeBullhornState()
    # Distinctive, greppable secrets so a leak is unambiguous.
    org_state = state.make_org(
        "org1",
        password="SUPER-SECRET-PW-9271",
        client_secret="CLIENT-SECRET-XYZZY-8842",
    )

    with live_bullhorn_server(state) as server:
        _patch_connect_to_fake(monkeypatch, server, org_state)
        with caplog.at_level(logging.DEBUG):
            resp = client.post("/api/v1/bullhorn/connect", headers=headers, json=_connect_body(org_state))

    assert resp.status_code == 200, resp.text
    raw_body = resp.text
    assert "SUPER-SECRET-PW-9271" not in raw_body
    assert "CLIENT-SECRET-XYZZY-8842" not in raw_body
    # refresh token must not be echoed either.
    db.expire_all()
    org = _org_for(db, email)
    plain_refresh = decrypt_text(org.bullhorn_refresh_token, settings.SECRET_KEY)
    assert plain_refresh not in raw_body

    # No captured log record (message OR args) may carry the password or secret.
    for rec in caplog.records:
        rendered = rec.getMessage()
        assert "SUPER-SECRET-PW-9271" not in rendered, f"password leaked in log: {rec.name}"
        assert "CLIENT-SECRET-XYZZY-8842" not in rendered, f"secret leaked in log: {rec.name}"


def test_connect_missing_entitlement_fails_with_per_entity_message(client, db, monkeypatch):
    """When the API user lacks a required verb on an entity, connect fails 400
    with a message naming that entity — and writes nothing durable."""
    _enable(monkeypatch)
    headers, email = auth_headers(client, email="bh-noent@example.com")

    state = FakeBullhornState()
    org_state = state.make_org("org1")
    # Strip the PUT verb from Note so the Note pre-flight fails.
    state.set_entitlements(org_state, "Note", ["GET"])

    with live_bullhorn_server(state) as server:
        _patch_connect_to_fake(monkeypatch, server, org_state)
        resp = client.post("/api/v1/bullhorn/connect", headers=headers, json=_connect_body(org_state))

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "Note" in detail
    assert "PUT" in detail

    # Nothing persisted — the org stays disconnected.
    db.expire_all()
    org = _org_for(db, email)
    assert not org.bullhorn_connected
    assert org.bullhorn_refresh_token is None


def test_connect_bad_credentials_fails_cleanly(client, db, monkeypatch):
    """Bad API-user password -> a clean 400, no persisted connection."""
    _enable(monkeypatch)
    headers, email = auth_headers(client, email="bh-badcreds@example.com")

    state = FakeBullhornState()
    org_state = state.make_org("org1")

    with live_bullhorn_server(state) as server:
        _patch_connect_to_fake(monkeypatch, server, org_state)
        body = _connect_body(org_state)
        body["password"] = "wrong-password"
        resp = client.post("/api/v1/bullhorn/connect", headers=headers, json=body)

    assert resp.status_code == 400, resp.text
    db.expire_all()
    org = _org_for(db, email)
    assert not org.bullhorn_connected


def test_reconnect_waits_for_live_bullhorn_owner_without_changing_credentials(
    client, db, monkeypatch
):
    """Reconnect cannot race a live sync/write token rotation."""
    _enable(monkeypatch)
    headers, email = auth_headers(client, email="bh-connect-busy@example.com")
    org = _org_for(db, email)
    org.bullhorn_connected = True
    org.bullhorn_username = "existing-user"
    org.bullhorn_client_id = "existing-client"
    org.bullhorn_refresh_token = "existing-ciphertext"
    org.bullhorn_credential_generation = 7
    db.commit()
    monkeypatch.setattr(bh_connect_lifecycle, "_acquire_mutex", lambda _org_id: None)
    monkeypatch.setattr(
        bh_connect_lifecycle,
        "run_connect",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("connect must not start without mutex ownership")
        ),
    )

    response = client.post(
        "/api/v1/bullhorn/connect",
        headers=headers,
        json={
            "username": "replacement-user",
            "client_id": "replacement-client",
            "client_secret": "replacement-secret",
            "password": "replacement-password",
        },
    )

    assert response.status_code == 409
    db.expire_all()
    fresh = _org_for(db, email)
    assert fresh.bullhorn_username == "existing-user"
    assert fresh.bullhorn_client_id == "existing-client"
    assert fresh.bullhorn_refresh_token == "existing-ciphertext"
    assert fresh.bullhorn_credential_generation == 7


def test_reconnect_fails_closed_when_mutex_state_is_unavailable(
    client, monkeypatch
):
    _enable(monkeypatch)
    headers, _ = auth_headers(client, email="bh-connect-lock-down@example.com")

    def _unavailable(_org_id):
        raise bh_connect_lifecycle.BullhornMutexUnavailable("redis unavailable")

    monkeypatch.setattr(bh_connect_lifecycle, "_acquire_mutex", _unavailable)
    response = client.post(
        "/api/v1/bullhorn/connect",
        headers=headers,
        json={
            "username": "api-user",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "password": "one-time-password",
        },
    )

    assert response.status_code == 503
    assert "retry" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_reports_connection_and_unmapped_counts(client, db, monkeypatch):
    """GET /status surfaces connection, last-sync, subscription health, and the
    needs-mapping count derived from applications with an unmapped status."""
    _enable(monkeypatch)
    headers, email = auth_headers(client, email="bh-status@example.com")
    org = _org_for(db, email)
    org.bullhorn_connected = True
    org.bullhorn_refresh_token = "ciphertext"
    org.bullhorn_username = "api@corp"
    org.bullhorn_rest_url = (
        "https://rest.example.test/rest-services/private-corp-token/"
    )
    subscription_id = event_subscriptions.deterministic_subscription_id(org)
    org.bullhorn_event_subscription_id = subscription_id
    org.bullhorn_config = {
        event_state.SUBSCRIPTION_STATE_KEY: {
            "version": 1,
            "subscription_id": subscription_id,
            "environment_namespace": event_state.deployment_namespace(),
            "state": "active",
            "anchor_epoch": event_state.new_epoch(),
            "last_completed_request_id": "17",
        }
    }
    org.bullhorn_last_sync_status = "success"
    db.commit()

    resp = client.get("/api/v1/bullhorn/status", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["bullhorn_connected"] is True
    assert data["event_subscription_active"] is True
    assert data["event_subscription_health"] == "active"
    assert data["last_sync_status"] == "success"
    assert data["unmapped_status_count"] == 0
    assert data["sync_in_progress"] is False
    assert data["bullhorn_rest_url_configured"] is True
    assert "bullhorn_rest_url" not in data
    # No credential is exposed in status.
    assert "refresh_token" not in resp.text
    assert "ciphertext" not in resp.text
    assert "api@corp" not in resp.text
    assert "private-corp-token" not in resp.text


def test_status_fails_closed_for_unproven_subscription_state(client, db, monkeypatch):
    _enable(monkeypatch)
    headers, email = auth_headers(client, email="bh-invalid-subscription@example.com")
    org = _org_for(db, email)
    org.bullhorn_connected = True
    org.bullhorn_refresh_token = "ciphertext"
    org.bullhorn_username = "api@corp"
    org.bullhorn_event_subscription_id = "legacy-or-cloned-subscription"
    db.commit()

    response = client.get("/api/v1/bullhorn/status", headers=headers)

    assert response.status_code == 200
    assert response.json()["event_subscription_active"] is False
    assert response.json()["event_subscription_health"] == "invalid_provenance"


# ---------------------------------------------------------------------------
# sync trigger / cancel
# ---------------------------------------------------------------------------


def test_sync_requires_connection(client, db, monkeypatch):
    """POST /sync 400s when the org isn't connected (before any enqueue)."""
    _enable(monkeypatch)
    headers, _ = auth_headers(client, email="bh-sync-nc@example.com")
    resp = client.post("/api/v1/bullhorn/sync", headers=headers, json={})
    assert resp.status_code == 400
    assert "not connected" in resp.json()["detail"].lower()


def test_sync_started_enqueues_task(client, db, monkeypatch):
    """POST /sync on a connected org returns started and enqueues the Celery task
    exactly once (dispatch mocked)."""
    _enable(monkeypatch)
    headers, email = auth_headers(client, email="bh-sync@example.com")
    org = _org_for(db, email)
    org.bullhorn_connected = True
    org.bullhorn_refresh_token = "ct"
    org.bullhorn_username = "api@corp"
    db.commit()

    dispatched: list[dict] = []
    monkeypatch.setattr(
        bh_bootstrap,
        "_enqueue_initial_full_sync",
        lambda **kwargs: dispatched.append(kwargs),
    )

    resp = client.post("/api/v1/bullhorn/sync", headers=headers, json={"mode": "full"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "started"
    assert resp.json()["mode"] == "full"
    run_id = resp.json()["run_id"]
    assert dispatched == [
        {
            "org_id": org.id,
            "mode": "full",
            "run_id": run_id,
            "trigger": bh_bootstrap.MANUAL_FULL_SYNC_TRIGGER,
        }
    ]
    db.expire_all()
    fresh = db.query(Organization).filter(Organization.id == org.id).one()
    assert fresh.bullhorn_sync_progress["run_id"] == run_id
    assert fresh.bullhorn_sync_progress["trigger"] == bh_bootstrap.MANUAL_FULL_SYNC_TRIGGER


def test_sync_already_running_short_circuits(client, db, monkeypatch):
    """When a run is in flight (live progress marker, non-terminal phase), POST
    /sync returns 202 already_running and does NOT enqueue a second."""
    _enable(monkeypatch)
    headers, email = auth_headers(client, email="bh-sync-run@example.com")
    org = _org_for(db, email)
    org.bullhorn_connected = True
    org.bullhorn_refresh_token = "ct"
    org.bullhorn_username = "api@corp"
    org.bullhorn_sync_progress = {"phase": "candidates", "jobs_total": 5}
    db.commit()

    called = {"n": 0}
    monkeypatch.setattr(
        bh_bootstrap,
        "_enqueue_initial_full_sync",
        lambda **_kwargs: called.__setitem__("n", called["n"] + 1),
    )

    resp = client.post("/api/v1/bullhorn/sync", headers=headers, json={})
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "already_running"
    assert called["n"] == 0


def test_sync_cancel_sets_flag_in_progress(client, db, monkeypatch):
    """POST /sync/cancel writes cancel_requested into the live progress JSON."""
    _enable(monkeypatch)
    headers, email = auth_headers(client, email="bh-cancel@example.com")
    org = _org_for(db, email)
    org.bullhorn_connected = True
    org.bullhorn_sync_progress = {"phase": "candidates"}
    db.commit()

    resp = client.post("/api/v1/bullhorn/sync/cancel", headers=headers, json={})
    assert resp.status_code == 200, resp.text
    db.expire_all()
    org = _org_for(db, email)
    assert org.bullhorn_sync_progress.get("cancel_requested") is True


def test_sync_cancel_noop_when_idle(client, db, monkeypatch):
    """Cancel is a clean no-op when there's no live run."""
    _enable(monkeypatch)
    headers, email = auth_headers(client, email="bh-cancel-idle@example.com")
    resp = client.post("/api/v1/bullhorn/sync/cancel", headers=headers, json={})
    assert resp.status_code == 200
    assert "No sync in progress" in resp.json()["message"]


# ---------------------------------------------------------------------------
# stage-map list + replace
# ---------------------------------------------------------------------------


def test_stage_map_list_and_replace(client, db, monkeypatch):
    """GET returns pipeline stages + current rows; PUT replaces the org's rows."""
    _enable(monkeypatch)
    headers, email = auth_headers(client, email="bh-map@example.com")
    org = _org_for(db, email)
    # Seed one pre-existing row to prove replace REPLACES (doesn't append).
    db.add(AtsStageMap(org_id=org.id, ats="bullhorn", remote_status="Old Status", taali_stage="applied", is_reject=False))
    db.commit()

    got = client.get("/api/v1/bullhorn/stage-map", headers=headers)
    assert got.status_code == 200, got.text
    assert "advanced" in got.json()["pipeline_stages"]
    assert any(m["remote_status"] == "Old Status" for m in got.json()["mappings"])
    assert got.json()["resolved_write_targets"] == {
        "invited": None,
        "in_assessment": None,
        "review": None,
        "advanced": None,
        "rejected": None,
    }

    resp = client.put(
        "/api/v1/bullhorn/stage-map",
        headers=headers,
        json={
            "mappings": [
                {"remote_status": "Submitted", "taali_stage": "applied", "is_reject": False},
                {"remote_status": "Client Rejected", "taali_stage": "review", "is_reject": True},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["mappings_count"] == 2

    db.expire_all()
    rows = db.query(AtsStageMap).filter(AtsStageMap.org_id == org.id, AtsStageMap.ats == "bullhorn").all()
    assert {r.remote_status for r in rows} == {"Submitted", "Client Rejected"}  # Old Status gone
    reject_row = next(r for r in rows if r.remote_status == "Client Rejected")
    assert reject_row.is_reject is True


def test_stage_map_replace_rejects_unknown_taali_stage(client, db, monkeypatch):
    """PUT validates taali_stage against PIPELINE_STAGES (422 on an unknown one)."""
    _enable(monkeypatch)
    headers, _ = auth_headers(client, email="bh-map-bad@example.com")
    resp = client.put(
        "/api/v1/bullhorn/stage-map",
        headers=headers,
        json={"mappings": [{"remote_status": "Submitted", "taali_stage": "not_a_stage", "is_reject": False}]},
    )
    assert resp.status_code == 422, resp.text
    assert "not_a_stage" in resp.json()["detail"]


@pytest.mark.parametrize(
    "mappings",
    [
        [
            {"remote_status": "Rejected A", "taali_stage": "review", "is_reject": True},
            {"remote_status": "Rejected B", "taali_stage": "review", "is_reject": True},
        ],
        [
            {"remote_status": "Interview A", "taali_stage": "advanced", "is_reject": False},
            {"remote_status": "Interview B", "taali_stage": "advanced", "is_reject": False},
        ],
    ],
)
def test_stage_map_editor_rejects_ambiguous_write_targets(
    client, db, monkeypatch, mappings
):
    _enable(monkeypatch)
    headers, email = auth_headers(client, email="bh-map-ambiguous@example.com")
    org = _org_for(db, email)
    org.bullhorn_config = {}
    db.commit()

    response = client.put(
        "/api/v1/bullhorn/stage-map",
        headers=headers,
        json={"mappings": mappings},
    )

    assert response.status_code == 422, response.text
    assert "Multiple Bullhorn" in response.json()["detail"]


def test_stage_map_replace_rejects_unknown_remote_status_when_list_cached(client, db, monkeypatch):
    """When the org has a cached remote status list, PUT rejects a status not in
    it (422). Statuses in the cached list are accepted."""
    _enable(monkeypatch)
    headers, email = auth_headers(client, email="bh-map-status@example.com")
    org = _org_for(db, email)
    org.bullhorn_config = {"status_list": ["Submitted", "Placed"]}
    db.commit()

    bad = client.put(
        "/api/v1/bullhorn/stage-map",
        headers=headers,
        json={"mappings": [{"remote_status": "Ghost Status", "taali_stage": "applied", "is_reject": False}]},
    )
    assert bad.status_code == 422, bad.text
    assert "Ghost Status" in bad.json()["detail"]

    ok = client.put(
        "/api/v1/bullhorn/stage-map",
        headers=headers,
        json={"mappings": [{"remote_status": "Submitted", "taali_stage": "applied", "is_reject": False}]},
    )
    assert ok.status_code == 200, ok.text


# ---------------------------------------------------------------------------
# admin diagnostic
# ---------------------------------------------------------------------------


def test_admin_diagnostic_requires_secret(client, db, monkeypatch):
    """Admin diagnostic 403s without the correct X-Admin-Secret."""
    _enable(monkeypatch)
    _, email = auth_headers(client, email="bh-admin@example.com")

    resp = client.get(
        "/api/v1/bullhorn/admin/diagnostic",
        params={"email": email},
        headers={"X-Admin-Secret": "wrong-secret"},
    )
    assert resp.status_code == 403


def test_org_me_exposes_bullhorn_gate_without_leaking_creds(client, db, monkeypatch):
    """FE gating: /organizations/me surfaces bullhorn_enabled (platform gate) +
    bullhorn_connected, and NEVER the stored credentials."""
    headers, _ = auth_headers(client, email="bh-orgme@example.com")
    resp = client.get("/api/v1/organizations/me", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("bullhorn_enabled") is False  # flag off by default
    assert data.get("bullhorn_connected") is False
    assert "bullhorn_client_secret" not in data
    assert "bullhorn_refresh_token" not in data


def test_admin_diagnostic_redacts_credentials(client, db, monkeypatch):
    """With the right secret: returns presence-only booleans, NEVER the ciphertext
    or plaintext of any credential."""
    _enable(monkeypatch)
    _, email = auth_headers(client, email="bh-admin-ok@example.com")
    org = _org_for(db, email)
    org.bullhorn_connected = True
    org.bullhorn_client_id = "client-abc"
    org.bullhorn_client_secret = "CIPHER-SECRET-4477"
    org.bullhorn_refresh_token = "CIPHER-REFRESH-9931"
    org.bullhorn_username = "private-api-user@corp"
    org.bullhorn_rest_url = (
        "https://rest.example.test/rest-services/private-corp-token/"
    )
    db.commit()

    resp = client.get(
        "/api/v1/bullhorn/admin/diagnostic",
        params={"email": email},
        headers={"X-Admin-Secret": settings.ADMIN_SECRET},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["bullhorn_connected"] is True
    assert data["has_client_secret"] is True
    assert data["has_refresh_token"] is True
    assert data["username_configured"] is True
    assert data["rest_url_configured"] is True
    assert "username" not in data
    assert "rest_url" not in data
    # The ACTUAL credential values must never appear.
    body = resp.text
    assert "CIPHER-SECRET-4477" not in body
    assert "CIPHER-REFRESH-9931" not in body
    assert "private-api-user@corp" not in body
    assert "private-corp-token" not in body
