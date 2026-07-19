"""Contract tests: the connective Bullhorn surface the sync engine leans on.

Companion to ``test_contract_client.py`` (the headline crash-safety / 429-storm /
verb-discipline classes from BULLHORN_BUILD_PLAN.md §5). Split out purely to keep
each file small; same harness — the REAL client (PR-3) against the live FAKE
server (PR-4a), deterministic (test-advanced clock, no wall-clock waits, only the
uvicorn transport is real).

Covers: discovery (loginInfo → oauth → REST login), a session 401 → refresh →
re-login EXACTLY once, mandatory ``fields=`` behavior, per-org status-list +
categorization differences, entitlement fetch, file download + convertToText
round-trip, and event subscription create/poll/refetch basics.
"""

from __future__ import annotations

import pytest

from app.components.integrations.bullhorn import BullhornAuth, BullhornService
from tests.fakes.bullhorn_fakes import live_bullhorn_server
from tests.fakes.bullhorn_state import FakeBullhornState


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------


def _make_auth(server, org, *, persist, refresh_token=None, password="pw"):
    """A BullhornAuth pointed at the live fake via its discovery url."""
    return BullhornAuth(
        username=org.username,
        client_id=org.client_id,
        client_secret=org.client_secret,
        refresh_token=refresh_token,
        persist_tokens=persist,
        discovery_url=server.discovery_url,
        password=password,
    )


def _authorized(server, org) -> BullhornAuth:
    """Connect (discovery + authorize + first token) and return the live auth."""
    auth = _make_auth(server, org, persist=lambda **kw: None)
    auth.authorize_with_password()
    return auth


# ===========================================================================
# Discovery + session lifecycle
# ===========================================================================


def test_discovery_logininfo_to_oauth_to_rest_login():
    """The connect chain end-to-end: loginInfo discovery yields the oauth+rest
    swimlanes, the automated auth-code grant + token exchange succeed, and a REST
    login establishes a usable session (proven by a real read)."""
    state = FakeBullhornState()
    org = state.make_org("org1")
    state.make_candidate(org, name="Grace Hopper")

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=lambda **kw: None)
        # No swimlane known up front — discovery must fill it in.
        assert auth.rest_url is None
        auth.authorize_with_password()
        svc = BullhornService(auth, client_id=org.client_id)

        rows = svc.search_candidates(fields="id,name")
        assert [r["name"] for r in rows] == ["Grace Hopper"]
        assert auth.rest_url is not None  # a live corpToken-bearing url now held


def test_session_401_triggers_refresh_and_relogin_exactly_once():
    """A 401 (forced via session TTL fast-forward) triggers ONE refresh + relogin,
    and the retried call succeeds on the fresh session — the client never logs in
    per request and never loops."""
    state = FakeBullhornState()
    org = state.make_org("org1")
    state.make_candidate(org)
    reauths = {"n": 0}

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=lambda **kw: None)
        auth.authorize_with_password()
        svc = BullhornService(auth, client_id=org.client_id)
        assert svc.ping()["sessionExpires"] > 0

        # Wrap reauthenticate to count invocations without changing its behavior.
        real_reauth = auth.reauthenticate

        def _counting_reauth():
            reauths["n"] += 1
            return real_reauth()

        auth.reauthenticate = _counting_reauth  # type: ignore[assignment]

        state.advance_clock(700)  # > SESSION_TTL (600) → next REST call 401s once
        pong = svc.ping()
        assert pong["sessionExpires"] > 0
        assert reauths["n"] == 1  # exactly one refresh+relogin, then success


# ===========================================================================
# Mandatory fields
# ===========================================================================


def test_reads_send_mandatory_fields_and_client_guards_empty_fields():
    """``fields=`` is mandatory. The client sends it on every read (so the fake
    returns full rows, not id-only), and passing an empty fields value is a loud
    client-side error rather than a silent id-only read."""
    state = FakeBullhornState()
    org = state.make_org("org1")
    state.make_candidate(org, name="Ada Lovelace", email="ada@example.com")

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=lambda **kw: None)
        auth.authorize_with_password()
        svc = BullhornService(auth, client_id=org.client_id)

        rows = svc.search_candidates(fields="id,name,email")
        assert rows[0]["name"] == "Ada Lovelace"
        assert rows[0]["email"] == "ada@example.com"

        # Empty fields is a bug, guarded before any network id-only degrade.
        with pytest.raises(ValueError):
            svc.search_candidates(fields="")


# ===========================================================================
# Per-org status lists
# ===========================================================================


def test_status_list_and_categorization_differ_per_org():
    """Two orgs with different free-text status lists each round-trip their own
    list + categorization settings — never a hardcoded set."""
    state = FakeBullhornState()
    org_a = state.make_org(
        "orgA",
        status_list=["A-New", "A-Placed", "A-Rejected"],
        categorization={
            "interviewScheduledJobResponseStatus": None,
            "confirmedJobResponseStatus": "A-Placed",
            "rejectedJobResponseStatus": "A-Rejected",
        },
    )
    org_b = state.make_org(
        "orgB",
        status_list=["B-Open", "B-Hired"],
        categorization={
            "interviewScheduledJobResponseStatus": "B-Open",
            "confirmedJobResponseStatus": "B-Hired",
            "rejectedJobResponseStatus": None,
        },
    )

    with live_bullhorn_server(state) as server:
        svc_a = BullhornService(_authorized(server, org_a), client_id=org_a.client_id)
        svc_b = BullhornService(_authorized(server, org_b), client_id=org_b.client_id)

        got_a = svc_a.get_status_list()
        got_b = svc_b.get_status_list()

        assert got_a["statuses"] == ["A-New", "A-Placed", "A-Rejected"]
        assert got_b["statuses"] == ["B-Open", "B-Hired"]
        assert got_a["categorization"]["confirmedJobResponseStatus"] == "A-Placed"
        assert got_b["categorization"]["confirmedJobResponseStatus"] == "B-Hired"
        # Categorization values an org leaves unset come back as None (the sync
        # layer treats those as needs-mapping rather than guessing).
        assert got_a["categorization"]["interviewScheduledJobResponseStatus"] is None
        assert got_b["categorization"]["rejectedJobResponseStatus"] is None


# ===========================================================================
# Entitlements
# ===========================================================================


def test_entitlements_fetch_reflects_seeded_verbs():
    """Entitlement pre-flight: the client returns exactly the verbs the fake was
    seeded with for an entity (a read-only API user shows GET only)."""
    state = FakeBullhornState()
    org = state.make_org("org1")
    state.set_entitlements(org, "JobSubmission", ["GET"])  # read-only user

    with live_bullhorn_server(state) as server:
        svc = BullhornService(_authorized(server, org), client_id=org.client_id)
        assert svc.get_entitlements("JobSubmission") == ["GET"]
        # An unseeded entity defaults to full CRUD in the fake.
        assert set(svc.get_entitlements("Candidate")) == {"GET", "PUT", "POST", "DELETE"}


# ===========================================================================
# Files: download + convertToText round-trip
# ===========================================================================


def test_file_attachment_download_and_convert_to_text_round_trip():
    """The CV path: list a candidate's fileAttachments, pull the raw bytes, and
    round-trip them through convertToText — the fallback text-extraction route."""
    state = FakeBullhornState()
    org = state.make_org("org1")
    cand = state.make_candidate(org)
    file_id = state.add_file_attachment(
        org, candidate_id=cand["id"], raw=b"Ada CV body", name="ada.pdf", file_type="Resume"
    )

    with live_bullhorn_server(state) as server:
        svc = BullhornService(_authorized(server, org), client_id=org.client_id)

        metas = svc.list_file_attachments(candidate_id=cand["id"], fields="id,name,type")
        assert any(m["id"] == file_id and m["type"] == "Resume" for m in metas)

        raw = svc.get_file_raw(candidate_id=cand["id"], file_id=file_id)
        assert raw == b"Ada CV body"

        text = svc.convert_resume_to_text(
            filename="ada.pdf", content=raw, content_type="application/pdf"
        )
        assert text == "[resume-text] Ada CV body"


# ===========================================================================
# Event subscription create / poll / refetch
# ===========================================================================


def test_event_subscription_create_poll_and_refetch_basics():
    """Subscription lifecycle at the client contract level: create, destructive
    poll (drains + stamps a requestId), refetch-by-requestId replays ONLY that
    batch without draining more, and a fresh poll returns the remainder."""
    state = FakeBullhornState()
    org = state.make_org("org1")

    with live_bullhorn_server(state) as server:
        svc = BullhornService(_authorized(server, org), client_id=org.client_id)

        created = svc.create_subscription(
            subscription_id="tali-sub", entity_names=["JobSubmission", "Candidate"]
        )
        assert created["subscriptionId"] == "tali-sub"

        # Seed three events onto the queue (server-side control surface).
        for i in range(3):
            state.emit_event(
                org, "tali-sub", entity_name="Candidate", entity_id=100 + i,
                updated_properties=["status"],
            )

        first = svc.poll_events(subscription_id="tali-sub", max_events=2)
        assert len(first["events"]) == 2
        assert first["events"][0]["updatedProperties"] == ["status"]  # names only
        req_id = first["requestId"]
        assert svc.get_last_request_id(subscription_id="tali-sub") == {
            "result": req_id
        }

        # Refetch the SAME batch by requestId — crash-replay, no further drain.
        replay = svc.refetch_events(subscription_id="tali-sub", request_id=req_id)
        assert replay["events"] == first["events"]

        # A fresh destructive poll returns the remaining 1 (the 2 were consumed).
        second = svc.poll_events(subscription_id="tali-sub", max_events=2)
        assert len(second["events"]) == 1
        assert second["events"][0]["entityId"] == 102

        assert svc.delete_subscription(subscription_id="tali-sub")["result"] is True
