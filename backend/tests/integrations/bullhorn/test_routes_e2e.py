"""API-level end-to-end for the Bullhorn integration (build plan §7).

Drives the REAL ``/api/v1/bullhorn/*`` surface — the authed FastAPI routes, not
the units underneath — against the live fake Bullhorn server, walking one org
through the whole integration lifecycle in a single flow:

    POST /connect            (real connect: discovery → OAuth → ping → entitlement
                              pre-flight → status fetch → stage-map seed → ENCRYPTED
                              persist → tracked automatic FULL sync)
      → assert creds are ciphertext at rest (org row read raw), stage map seeded
      → GET /sync/status polled to completion (no manual POST /sync)
      → assert imported Roles / Candidates / CandidateApplications, status mapped
    PUT /stage-map           (recruiter remaps a status)
    decision write-back      (op_runner.execute_op → BullhornProvider → write_back)
      → assert the FAKE received the JobSubmission status POST + local-write-wins
        timestamp stamped on the application
    GET /admin/diagnostic    (credential redaction: presence-only booleans)

And the hard gate: with ``BULLHORN_ENABLED`` False the ENTIRE flow 503s (sibling
test), matching the ``MVP_DISABLE_WORKABLE`` analog.

WHY A DISCOVERY MONKEYPATCH (and nothing more):
``run_connect`` persists the fake's absolute ``restUrl`` onto ``org.bullhorn_rest_url``,
so the sync/write REST calls already hit the fake. The only thing that would
escape to the real Bullhorn cluster is the OAuth *refresh* leg, because
``sync_runner._build_service`` / ``BullhornProvider._client`` build ``BullhornAuth``
with the default ``discovery_url`` and no cached ``oauth_url`` (that isn't an org
column). So we patch ``BullhornAuth``'s default discovery URL to the fake for the
duration of the test — the minimal seam that keeps decrypt-at-rest + the token
rotation invariant REAL while making every leg hermetic. This is the exact analog
of how ``test_api_bullhorn_routes`` swaps only the connect's discovery endpoint,
extended to cover the background sync + write-back auth that build from stored
creds. Celery runs eager in the test env (conftest), so ``POST /sync`` executes
the real sync body in-process before returning.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.components.integrations.bullhorn.auth import BullhornAuth
from app.components.integrations.bullhorn import sync_runner as bh_sync_runner
from app.components.integrations.bullhorn.stage_map import ATS_BULLHORN
from app.domains.bullhorn_sync import routes as bh_routes
from app.domains.bullhorn_sync import connect_lifecycle as bh_connect_lifecycle
from app.models.ats_stage_map import AtsStageMap
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from app.platform.config import settings
from app.platform.secrets import decrypt_text
from app.services import workable_op_runner as op_runner
from tests.conftest import auth_headers
from tests.fakes.bullhorn_fakes import live_bullhorn_server
from tests.fakes.bullhorn_state import FakeBullhornState


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _enable(monkeypatch) -> None:
    """Flip the Bullhorn platform flag on (shared settings singleton — global)."""
    monkeypatch.setattr(bh_routes.settings, "BULLHORN_ENABLED", True)
    monkeypatch.setattr(bh_connect_lifecycle, "_acquire_mutex", lambda _org_id: object())
    monkeypatch.setattr(bh_connect_lifecycle, "_release_mutex", lambda _handle: None)
    # The eager connect-triggered worker acquires the same distributed lock via
    # sync_runner. Redis is deliberately unreachable in unit tests; isolate the
    # API/Bullhorn lifecycle here while dedicated mutex tests keep fail-closed
    # production behavior covered.
    monkeypatch.setattr(bh_sync_runner, "_acquire_mutex", lambda _org_id: object())
    monkeypatch.setattr(bh_sync_runner, "_release_mutex", lambda _handle: None)


def _point_auth_discovery_at_fake(monkeypatch, server) -> None:
    """Make EVERY ``BullhornAuth`` (connect + sync + write) discover the fake.

    The connect path swaps the endpoint explicitly, but the background sync and
    the write-back build their own auth from the stored creds via
    ``sync_runner._build_service`` / ``BullhornProvider._client`` — which pass the
    default discovery url. We wrap ``BullhornAuth.__init__`` so a caller that does
    NOT specify ``discovery_url`` gets the fake's; callers that pass one keep it.
    Decryption of the stored secret/refresh token and the rotation persist-hook
    are untouched — only where discovery points changes.
    """
    real_init = BullhornAuth.__init__

    def _patched_init(self, *args, **kwargs):
        if "discovery_url" not in kwargs or kwargs.get("discovery_url") is None:
            kwargs["discovery_url"] = server.discovery_url
        # A default discovery url also implies no baked oauth url; force rediscovery
        # so the fake's oauth swimlane is the one used on refresh.
        kwargs.setdefault("oauth_url", None)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(BullhornAuth, "__init__", _patched_init)


def _org_for(db, email) -> Organization:
    user = db.query(User).filter(User.email == email).first()
    assert user is not None
    org = db.query(Organization).filter(Organization.id == user.organization_id).first()
    assert org is not None
    return org


def _connect_body(org_state) -> dict:
    return {
        "username": org_state.username,
        "client_id": org_state.client_id,
        "client_secret": org_state.client_secret,
        "password": org_state.password,
    }


def _seed_full_org(state: FakeBullhornState):
    """One org on the fake with a JobOrder + Candidate + JobSubmission + history
    + a note. The submission's status is the categorization interview status so
    the seeded stage map maps it → advanced (proves status mapping end-to-end).

    The password / client_secret are DISTINCTIVE, greppable sentinels (not the
    generic "pw"/"secret" defaults) so the credential-leak asserts on the connect
    + diagnostic responses are unambiguous — a plain "secret" would false-positive
    on the ``has_client_secret`` field NAME."""
    bh_org = state.make_org(
        "e2e",
        password="SUPER-SECRET-PW-9271",
        client_secret="CLIENT-SECRET-XYZZY-8842",
        status_list=["New Lead", "Interview Scheduled", "Placed", "Client Rejected"],
    )
    job = state.make_job_order(bh_org, title="Senior Engineer", is_open=True)
    cand = state.make_candidate(bh_org, name="Ada Lovelace", email="ada@example.com")
    old_submission_at = state.now - (30 * 24 * 60 * 60 * 1000)
    sub = state.make_job_submission(
        bh_org,
        candidate_id=cand["id"],
        job_order_id=job["id"],
        status="Interview Scheduled",
        dateAdded=old_submission_at,
        dateLastModified=old_submission_at,
    )
    state.make_job_submission_history(bh_org, job_submission_id=sub["id"], status="New Lead")
    state.make_job_submission_history(
        bh_org, job_submission_id=sub["id"], status="Interview Scheduled"
    )
    # A recruiter note about the candidate (imported as agent-visible context).
    note_id = state._next()  # noqa: SLF001 — deterministic test seeding
    state._put_entity(  # noqa: SLF001
        bh_org,
        "Note",
        {
            "id": note_id,
            "comments": "Strong systems background.",
            "action": "Other",
            "personReference": {"id": cand["id"]},
            "dateAdded": state.now,
        },
    )
    return bh_org, job, cand, sub


# ---------------------------------------------------------------------------
# the full lifecycle, end to end, through the real routes
# ---------------------------------------------------------------------------


def test_bullhorn_full_lifecycle_through_the_api(client, db, monkeypatch):
    _enable(monkeypatch)
    headers, email = auth_headers(client, email="bh-e2e@example.com")

    state = FakeBullhornState()
    bh_org, job, cand, sub = _seed_full_org(state)

    with live_bullhorn_server(state) as server:
        _point_auth_discovery_at_fake(monkeypatch, server)

        # --- 1. CONNECT ----------------------------------------------------
        resp = client.post("/api/v1/bullhorn/connect", headers=headers, json=_connect_body(bh_org))
        assert resp.status_code == 200, resp.text
        connect_payload = resp.json()
        assert connect_payload["status"] == "connected"
        assert connect_payload["bullhorn_connected"] is True
        # Connect itself launches the tracked FULL import. Celery is eager in
        # this test, so it is already complete without a manual POST /sync.
        assert connect_payload["initial_sync"]["mode"] == "full"
        assert connect_payload["initial_sync"]["status"] == "success"
        assert connect_payload["initial_sync"]["run_id"]
        # DEFAULT_CATEGORIZATION → interview + placed (advanced) + rejected (review).
        assert connect_payload["seeded_stage_rows"] == 3
        # SECURITY: the connect response never echoes the one-time password or the
        # client secret (credential-free by construction).
        assert "SUPER-SECRET-PW-9271" not in resp.text
        assert "CLIENT-SECRET-XYZZY-8842" not in resp.text

        # Entitlement pre-flight actually ran against the fake: the connect only
        # reaches "connected" once Candidate/JobOrder/JobSubmission/Note verbs
        # passed. Prove the negative separately (a stripped verb 400s) — see
        # test_connect_missing_entitlement in test_api_bullhorn_routes; here the
        # success itself is the pre-flight-passed signal.

        # --- creds are CIPHERTEXT at rest (read the org row raw) ------------
        db.expire_all()
        org = _org_for(db, email)
        assert org.bullhorn_connected is True
        assert org.bullhorn_username == bh_org.username
        # Stored secret/refresh token are NOT the plaintext, and decrypt back.
        assert org.bullhorn_client_secret != bh_org.client_secret
        assert decrypt_text(org.bullhorn_client_secret, settings.SECRET_KEY) == bh_org.client_secret
        assert org.bullhorn_refresh_token is not None
        assert org.bullhorn_refresh_token != decrypt_text(org.bullhorn_refresh_token, settings.SECRET_KEY)
        assert decrypt_text(org.bullhorn_refresh_token, settings.SECRET_KEY)  # non-empty plaintext

        # --- stage map seeded from categorization --------------------------
        seeded_rows = (
            db.query(AtsStageMap)
            .filter(AtsStageMap.org_id == org.id, AtsStageMap.ats == ATS_BULLHORN)
            .all()
        )
        assert {r.remote_status for r in seeded_rows} == {
            "Interview Scheduled",
            "Placed",
            "Client Rejected",
        }

        # --- 2. poll the connect-triggered sync to completion ---------------
        status_body = _poll_sync_to_done(client, headers)
        assert status_body["sync_in_progress"] is False
        assert status_body["last_sync_status"] == "success"
        assert status_body["initial_sync"]["run_id"] == connect_payload["initial_sync"]["run_id"]
        assert status_body["initial_sync"]["status"] == "success"
        snap = status_body["db_snapshot"]
        assert snap["roles_active"] == 1
        assert snap["candidates_active"] == 1
        assert snap["applications_active"] == 1

        # --- assert the import actually landed the right rows --------------
        db.expire_all()
        role = db.query(Role).filter(Role.organization_id == org.id).one()
        assert role.bullhorn_job_order_id == str(job["id"])
        assert role.source == "bullhorn"

        candidate = db.query(Candidate).filter(Candidate.organization_id == org.id).one()
        assert candidate.bullhorn_candidate_id == str(cand["id"])
        assert candidate.email == "ada@example.com"

        app = db.query(CandidateApplication).filter(
            CandidateApplication.organization_id == org.id
        ).one()
        assert app.bullhorn_job_submission_id == str(sub["id"])
        assert app.source == "bullhorn"
        # A 30-day-old submission proves bootstrap is a FULL walk, not the
        # incremental layer's 24-hour first-run lookback.
        expected_applied = datetime.fromtimestamp(
            sub["dateAdded"] / 1000,
            tz=timezone.utc,
        )
        assert app.workable_created_at.date() == expected_applied.date()
        # raw remote status preserved and mapped (Interview Scheduled → advanced).
        assert app.bullhorn_status == "Interview Scheduled"
        assert app.pipeline_stage == "advanced"

        # --- 4. PUT /stage-map (recruiter remaps) --------------------------
        put_resp = client.put(
            "/api/v1/bullhorn/stage-map",
            headers=headers,
            json={
                "mappings": [
                    {"remote_status": "Interview Scheduled", "taali_stage": "advanced", "is_reject": False},
                    {"remote_status": "Placed", "taali_stage": "advanced", "is_reject": False},
                    {"remote_status": "Client Rejected", "taali_stage": "review", "is_reject": True},
                ]
            },
        )
        assert put_resp.status_code == 200, put_resp.text
        assert put_resp.json()["mappings_count"] == 3
        db.expire_all()
        rows_after = (
            db.query(AtsStageMap)
            .filter(AtsStageMap.org_id == org.id, AtsStageMap.ats == ATS_BULLHORN)
            .all()
        )
        assert {r.remote_status for r in rows_after} == {
            "Interview Scheduled",
            "Placed",
            "Client Rejected",
        }
        assert next(r for r in rows_after if r.remote_status == "Client Rejected").is_reject is True

        # --- 5. decision write-back through the op_runner path -------------
        # A recruiter reject flows op_runner.execute_op → _route_bullhorn_op →
        # BullhornProvider (authed from the STORED creds against the fake) →
        # write_back.reject_submission, resolving "rejected" → the is_reject
        # status "Client Rejected" (never guessed).
        from app.services.ats_writeback_state import set_outcome_writeback_state

        app.application_outcome = "rejected"
        operation_id = f"manual-outcome:{app.id}:{app.version}:e2e"
        set_outcome_writeback_state(
            app,
            provider="bullhorn",
            status="queued",
            target_outcome="rejected",
            expected_application_version=int(app.version),
            expected_local_outcome="rejected",
            operation_id=operation_id,
            provider_target_id=str(app.bullhorn_job_submission_id),
        )
        db.commit()
        result = op_runner.execute_op(
            db,
            organization_id=org.id,
            op_type=op_runner.OP_MANUAL_OUTCOME,
            payload={
                "application_id": app.id,
                "target_outcome": "rejected",
                "expected_application_version": int(app.version),
                "expected_local_outcome": "rejected",
                "operation_id": operation_id,
                "provider": "bullhorn",
                "provider_target_id": str(app.bullhorn_job_submission_id),
                "reason": "not a fit",
            },
        )
        assert result["status"] == "ok"

        # The FAKE received the status POST: the JobSubmission is now rejected.
        assert (
            state.orgs["e2e"].entities["JobSubmission"][sub["id"]]["status"] == "Client Rejected"
        )
        # local-write-wins: our status + write timestamp are stamped on the app.
        db.refresh(app)
        assert app.bullhorn_status == "Client Rejected"
        assert app.bullhorn_status_local_write_at is not None

        # --- 6. GET /admin/diagnostic redaction ----------------------------
        diag = client.get(
            "/api/v1/bullhorn/admin/diagnostic",
            params={"email": email},
            headers={"X-Admin-Secret": settings.ADMIN_SECRET},
        )
        assert diag.status_code == 200, diag.text
        diag_body = diag.json()
        assert diag_body["bullhorn_connected"] is True
        # presence-only booleans, never the value.
        assert diag_body["has_client_secret"] is True
        assert diag_body["has_refresh_token"] is True
        # a live session ping was made from the stored creds against the fake.
        assert diag_body["session_ping"]["ok"] is True
        # NEITHER the ciphertext NOR the plaintext of any credential appears.
        raw = diag.text
        assert org.bullhorn_client_secret not in raw
        assert org.bullhorn_refresh_token not in raw
        assert decrypt_text(org.bullhorn_client_secret, settings.SECRET_KEY) not in raw
        assert decrypt_text(org.bullhorn_refresh_token, settings.SECRET_KEY) not in raw
        assert bh_org.password not in raw


def _poll_sync_to_done(client, headers, *, max_polls: int = 20) -> dict:
    """Poll GET /sync/status until the run is no longer in progress (bounded)."""
    body: dict = {}
    for _ in range(max_polls):
        resp = client.get("/api/v1/bullhorn/sync/status", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if not body.get("sync_in_progress"):
            return body
    raise AssertionError(f"Bullhorn sync did not complete after {max_polls} polls: {body}")


# ---------------------------------------------------------------------------
# hard gate — the entire lifecycle 503s when the flag is off
# ---------------------------------------------------------------------------


def test_full_flow_503s_when_bullhorn_disabled(client, db, monkeypatch):
    """Every step of the lifecycle 503s with BULLHORN_ENABLED False (the default),
    so the whole surface is inert on the live platform — the MVP_DISABLE_WORKABLE
    analog. No fake server is needed: the gate short-circuits before any I/O."""
    monkeypatch.setattr(bh_routes.settings, "BULLHORN_ENABLED", False)
    headers, email = auth_headers(client, email="bh-e2e-off@example.com")

    # The recruiter surface, in lifecycle order.
    lifecycle = [
        ("post", "/api/v1/bullhorn/connect", {"username": "u", "client_id": "c", "client_secret": "s", "password": "p"}),
        ("get", "/api/v1/bullhorn/status", None),
        ("post", "/api/v1/bullhorn/sync", {"mode": "full"}),
        ("get", "/api/v1/bullhorn/sync/status", None),
        ("post", "/api/v1/bullhorn/sync/cancel", {}),
        ("get", "/api/v1/bullhorn/stage-map", None),
        ("put", "/api/v1/bullhorn/stage-map", {"mappings": []}),
    ]
    for method, path, json_body in lifecycle:
        fn = getattr(client, method)
        resp = fn(path, headers=headers, json=json_body) if json_body is not None else fn(path, headers=headers)
        assert resp.status_code == 503, f"{method.upper()} {path} -> {resp.status_code}, expected 503"
        assert "disabled" in resp.json().get("detail", "").lower()

    # The admin diagnostic is gated too — 503 checked BEFORE the admin-secret gate,
    # so even a correct secret gets a 503 when the flag is off.
    diag = client.get(
        "/api/v1/bullhorn/admin/diagnostic",
        params={"email": email},
        headers={"X-Admin-Secret": settings.ADMIN_SECRET},
    )
    assert diag.status_code == 503
