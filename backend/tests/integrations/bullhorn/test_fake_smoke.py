"""Smoke tests for the fake Bullhorn server (tests/fakes/bullhorn_*).

Validates the fake itself against the fact-sheet contract BEFORE the real
Bullhorn client (PR-3) exists, so the 9 contract-test classes (PR-4) can trust
it. Covers the happy path (discovery -> oauth -> login -> search -> event poll)
plus the edge behaviors the contract suite leans on: single-use refresh
rotation, verb inversion, mandatory fields, destructive event drain + requestId
replay, seven-day unread-event retention, forced subscription disappearance,
and 429 counters.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx

from tests.fakes.bullhorn_fakes import asgi_client, live_bullhorn_server
from tests.fakes.bullhorn_state import FakeBullhornState


# --- helpers ----------------------------------------------------------------


def _authed_session(client: httpx.Client, state: FakeBullhornState, org) -> str:
    """Run discovery -> authorize -> token -> REST login; return BhRestToken."""
    info = client.get("/rest-services/loginInfo", params={"username": org.username})
    assert info.status_code == 200, info.text
    assert info.json()["restUrl"].endswith("/rest-services/fake")

    # The automated auth-code grant answers with a 302 whose Location header
    # carries ?code=... (like real Bullhorn) — NOT a JSON body. Don't follow the
    # redirect; read the code off Location, exactly as the real client does.
    auth = client.post(
        "/oauth/authorize",
        data={
            "client_id": org.client_id,
            "username": org.username,
            "password": org.password,
            "action": "Login",
        },
        follow_redirects=False,
    )
    assert auth.status_code == 302, auth.text
    code = parse_qs(urlsplit(auth.headers["Location"]).query)["code"][0]

    tok = client.post(
        "/oauth/token",
        data={"grant_type": "authorization_code", "code": code, "client_id": org.client_id},
    )
    assert tok.status_code == 200, tok.text
    access = tok.json()["access_token"]

    login = client.post("/rest-services/fake/login", params={"access_token": access, "version": "*"})
    assert login.status_code == 200, login.text
    return login.json()["BhRestToken"]


# --- happy path (in-process ASGI) ------------------------------------------


def test_smoke_login_search_eventpoll_asgi():
    state = FakeBullhornState()
    org = state.make_org("org1")
    cand = state.make_candidate(org, name="Ada Lovelace")
    job = state.make_job_order(org, title="Senior Engineer")
    sub = state.make_job_submission(org, candidate_id=cand["id"], job_order_id=job["id"])

    with asgi_client(state) as (client, st):
        bh = _authed_session(client, st, org)

        # ping
        pong = client.get("/rest-services/fake/ping", params={"BhRestToken": bh})
        assert pong.status_code == 200
        assert pong.json()["sessionExpires"] > 0

        # search JobSubmission with mandatory fields
        res = client.get(
            "/rest-services/fake/search/JobSubmission",
            params={"BhRestToken": bh, "fields": "id,status,candidate,jobOrder", "count": 100},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total"] == 1
        assert body["data"][0]["id"] == sub["id"]
        assert body["data"][0]["status"] == org.status_list[0]

        # create subscription, emit event, destructive drain
        put = client.put(
            "/rest-services/fake/event/subscription/tali-sub",
            params={"BhRestToken": bh, "type": "entity", "names": "JobSubmission",
                    "eventTypes": "UPDATED"},
        )
        assert put.status_code == 200, put.text
        st.emit_event(org, "tali-sub", entity_name="JobSubmission", entity_id=sub["id"],
                      event_type="UPDATED", updated_properties=["status"])

        poll = client.get(
            "/rest-services/fake/event/subscription/tali-sub",
            params={"BhRestToken": bh, "maxEvents": 100},
        )
        assert poll.status_code == 200, poll.text
        events = poll.json()["events"]
        assert len(events) == 1
        assert events[0]["entityId"] == sub["id"]
        assert events[0]["updatedProperties"] == ["status"]  # field NAMES only


# --- happy path (live uvicorn socket) --------------------------------------


def test_smoke_live_socket_happy_path():
    state = FakeBullhornState()
    org = state.make_org("org1")
    state.make_candidate(org)

    with live_bullhorn_server(state) as server:
        # discovery_url is what the real client is handed
        assert server.discovery_url.endswith("/rest-services/loginInfo")
        with httpx.Client(base_url=server.base_url, timeout=10.0) as client:
            bh = _authed_session(client, server.state, org)
            res = client.get(
                "/rest-services/fake/search/Candidate",
                params={"BhRestToken": bh, "fields": "id,name,email"},
            )
            assert res.status_code == 200, res.text
            assert res.json()["total"] == 1
            assert "name" in res.json()["data"][0]


# --- fields mandatory -------------------------------------------------------


def test_search_without_fields_returns_only_ids():
    state = FakeBullhornState()
    org = state.make_org("org1")
    state.make_candidate(org)
    with asgi_client(state) as (client, st):
        bh = _authed_session(client, st, org)
        res = client.get("/rest-services/fake/search/Candidate", params={"BhRestToken": bh})
        assert res.status_code == 200
        row = res.json()["data"][0]
        assert set(row.keys()) == {"id"}  # no fields => ids only, like real


# --- single-use refresh rotation (strand detection) ------------------------


def test_refresh_token_is_single_use():
    state = FakeBullhornState()
    org = state.make_org("org1")
    with asgi_client(state) as (client, st):
        _authed_session(client, st, org)
        # obtain a refresh token via a fresh code exchange
        code = st.mint_auth_code(org)
        first = client.post(
            "/oauth/token",
            data={"grant_type": "authorization_code", "code": code, "client_id": org.client_id},
        ).json()
        refresh = first["refresh_token"]

        rotated = client.post(
            "/oauth/token", data={"grant_type": "refresh_token", "refresh_token": refresh}
        )
        assert rotated.status_code == 200
        new_refresh = rotated.json()["refresh_token"]
        assert new_refresh != refresh  # rotated

        # re-presenting the OLD refresh token -> invalid_grant (strand signal)
        reused = client.post(
            "/oauth/token", data={"grant_type": "refresh_token", "refresh_token": refresh}
        )
        assert reused.status_code == 400
        assert reused.json()["error"] == "invalid_grant"

        # the NEW one still works exactly once
        ok = client.post(
            "/oauth/token", data={"grant_type": "refresh_token", "refresh_token": new_refresh}
        )
        assert ok.status_code == 200


# --- verb inversion ---------------------------------------------------------


def test_verb_inversion_enforced():
    state = FakeBullhornState()
    org = state.make_org("org1")
    existing = state.make_candidate(org)
    with asgi_client(state) as (client, st):
        bh = _authed_session(client, st, org)

        # PUT (create) on an EXISTING id -> error
        put = client.put(
            "/rest-services/fake/entity/Candidate",
            params={"BhRestToken": bh},
            json={"id": existing["id"], "name": "Dup"},
        )
        assert put.status_code == 400
        assert "PUT-create on existing" in put.json()["errorMessage"]

        # POST (update) on a MISSING id -> error
        post = client.post(
            "/rest-services/fake/entity/Candidate/999999",
            params={"BhRestToken": bh},
            json={"status": "X"},
        )
        assert post.status_code == 400
        assert "POST-update on missing" in post.json()["errorMessage"]

        # PUT create of a NEW record -> ok; POST update of it -> ok
        created = client.put(
            "/rest-services/fake/entity/Candidate",
            params={"BhRestToken": bh},
            json={"name": "New Person"},
        )
        assert created.status_code == 200
        new_id = created.json()["changedEntityId"]
        upd = client.post(
            f"/rest-services/fake/entity/Candidate/{new_id}",
            params={"BhRestToken": bh},
            json={"status": "Contacted"},
        )
        assert upd.status_code == 200
        assert upd.json()["data"]["status"] == "Contacted"


# --- destructive event queue + requestId replay ----------------------------


def test_event_poll_is_destructive_and_requestid_replays_last_batch():
    state = FakeBullhornState()
    org = state.make_org("org1")
    with asgi_client(state) as (client, st):
        bh = _authed_session(client, st, org)
        client.put(
            "/rest-services/fake/event/subscription/s1",
            params={"BhRestToken": bh, "names": "Candidate", "eventTypes": "UPDATED"},
        )
        for i in range(3):
            st.emit_event(org, "s1", entity_name="Candidate", entity_id=100 + i)

        first = client.get(
            "/rest-services/fake/event/subscription/s1",
            params={"BhRestToken": bh, "maxEvents": 2},
        ).json()
        assert len(first["events"]) == 2
        req_id = first["requestId"]

        # requestId replay -> SAME batch, no further drain
        replay = client.get(
            "/rest-services/fake/event/subscription/s1",
            params={"BhRestToken": bh, "maxEvents": 2, "requestId": req_id},
        ).json()
        assert replay["events"] == first["events"]

        # a fresh drain returns the remaining 1 (destructive read consumed 2)
        second = client.get(
            "/rest-services/fake/event/subscription/s1",
            params={"BhRestToken": bh, "maxEvents": 2},
        ).json()
        assert len(second["events"]) == 1
        assert second["events"][0]["entityId"] == 102


# --- event retention and distinct subscription disappearance ---------------


def test_forced_subscription_disappearance_returns_404():
    """A server-removed subscription is a distinct signal from session TTL.

    Bullhorn documents seven-day unread-event retention, not a matching
    subscription TTL, so the fake uses the explicit ``expired`` control to
    isolate the subscription-gone path (404).
    """
    state = FakeBullhornState()
    org = state.make_org("org1")
    with asgi_client(state) as (client, st):
        bh = _authed_session(client, st, org)
        client.put(
            "/rest-services/fake/event/subscription/s1",
            params={"BhRestToken": bh, "names": "Candidate"},
        )
        org.subscriptions["s1"].expired = True
        poll = client.get(
            "/rest-services/fake/event/subscription/s1", params={"BhRestToken": bh}
        )
        assert poll.status_code == 404


def test_unread_events_purge_after_seven_days_without_killing_subscription():
    state = FakeBullhornState()
    org = state.make_org("org1")
    with asgi_client(state) as (client, st):
        bh = _authed_session(client, st, org)
        client.put(
            "/rest-services/fake/event/subscription/s1",
            params={"BhRestToken": bh, "names": "Candidate"},
        )
        st.emit_event(org, "s1", entity_name="Candidate", entity_id=42)
        st.advance_clock(7 * 24 * 3600 + 1)
        fresh_session = _authed_session(client, st, org)

        anchor = client.get(
            "/rest-services/fake/event/subscription/s1/lastRequestId",
            params={"BhRestToken": fresh_session},
        )
        poll = client.get(
            "/rest-services/fake/event/subscription/s1",
            params={"BhRestToken": fresh_session, "maxEvents": 100},
        )

        assert anchor.status_code == 200
        assert anchor.json() == {"result": 0}
        assert poll.status_code == 200
        assert poll.json()["events"] == []


# --- 429 injection + counters ----------------------------------------------


def test_429_injection_and_counters():
    state = FakeBullhornState()
    org = state.make_org("org1")
    state.make_candidate(org)
    with asgi_client(state) as (client, st):
        bh = _authed_session(client, st, org)
        st.fail_next_requests_with_429(2)
        r1 = client.get("/rest-services/fake/search/Candidate",
                        params={"BhRestToken": bh, "fields": "id"})
        r2 = client.get("/rest-services/fake/search/Candidate",
                        params={"BhRestToken": bh, "fields": "id"})
        r3 = client.get("/rest-services/fake/search/Candidate",
                        params={"BhRestToken": bh, "fields": "id"})
        assert r1.status_code == 429
        assert r2.status_code == 429
        assert r3.status_code == 200
        assert st.count_429_served == 2
        assert r1.headers["Retry-After"] == "1"


# --- session TTL expiry -----------------------------------------------------


def test_session_expires_after_ttl_yields_401():
    state = FakeBullhornState()
    org = state.make_org("org1")
    with asgi_client(state) as (client, st):
        bh = _authed_session(client, st, org)
        st.advance_clock(700)  # > SESSION_TTL (600)
        res = client.get("/rest-services/fake/ping", params={"BhRestToken": bh})
        assert res.status_code == 401


# --- per-org status lists differ -------------------------------------------


def test_two_orgs_have_independent_status_lists():
    state = FakeBullhornState()
    org_a = state.make_org("orgA", status_list=["A-New", "A-Placed"])
    org_b = state.make_org("orgB", status_list=["B-Open", "B-Hired", "B-Rejected"])
    with asgi_client(state) as (client, st):
        bh_a = _authed_session(client, st, org_a)
        bh_b = _authed_session(client, st, org_b)
        sl_a = client.get("/rest-services/fake/settings/jobResponseStatusList",
                          params={"BhRestToken": bh_a}).json()
        sl_b = client.get("/rest-services/fake/settings/jobResponseStatusList",
                          params={"BhRestToken": bh_b}).json()
        assert sl_a["jobResponseStatusList"] == ["A-New", "A-Placed"]
        assert sl_b["jobResponseStatusList"] == ["B-Open", "B-Hired", "B-Rejected"]

        # categorization settings addressable per org
        rej = client.get("/rest-services/fake/settings/rejectedJobResponseStatus",
                         params={"BhRestToken": bh_a}).json()
        assert rej["rejectedJobResponseStatus"] == "Client Rejected"
