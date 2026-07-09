"""Contract tests: the REAL Bullhorn client (PR-3) against the FAKE server (PR-4a).

The client-level contract classes from BULLHORN_BUILD_PLAN.md §5. Sync-level
classes (2 mid-sync 401 resume, 3 event checkpoint replay, 4 dead-subscription
recreate, 5 status round-trip, 6 local-write-wins) are proven at the sync layer
in PR-5 and are NOT built here. This file locks:

* **Class 1 — refresh-rotation crash-safety.** Death between the token exchange
  and first use: killed via the persist hook, then a FRESH client rebuilt from
  the persisted org state recovers with no strand. Plus the strand signal — a
  reused (spent) refresh token surfaces a clean typed auth error, never silently.
* **Class 7 — 429 storm.** A sustained 429 stream drives the client's backoff +
  circuit breaker; we assert on the fake's counter that the 429s we provoke stay
  orders of magnitude under Bullhorn's ~9,000-in-5-min user-disable threshold,
  and that the breaker opens then fails fast WITHOUT serving more.
* **Class 8 — verb discipline.** ``_create`` is PUT and ``_update`` is POST; the
  fake rejects the inverted ops loudly (PUT on an existing id, POST on a missing
  id) through the real client's paths.

The connective surface the sync engine also leans on (discovery, 401 → refresh →
re-login once, mandatory ``fields=``, per-org status lists, entitlements, files,
events) lives in the companion ``test_contract_surface.py`` — split out only to
keep each file small.

Deterministic: the fake's clock is a test-advanced integer and every 429 backoff
sleep is captured via an injected ``time_sleep`` (no wall-clock waits). Only the
uvicorn transport is real, exactly as the green E2E in ``test_client_unit.py``.
"""

from __future__ import annotations

import pytest

from app.components.integrations.bullhorn import (
    BullhornApiError,
    BullhornAuth,
    BullhornAuthError,
    BullhornRateLimitError,
    BullhornService,
)
from app.components.integrations.bullhorn import ratelimit as rl
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


class _Store:
    """Stands in for the org row: the single durable slot the persist hook writes.

    ``save`` mirrors ``persist_tokens`` — the rotated refresh token lands here in
    its own 'transaction'. A fresh client is later rebuilt from ``refresh_token``
    to prove a crash after this point recovers.
    """

    def __init__(self, refresh_token: str | None = None):
        self.refresh_token = refresh_token
        self.rest_url: str | None = None
        self.saves = 0

    def save(self, *, refresh_token: str, rest_url: str | None = None) -> None:
        self.refresh_token = refresh_token
        if rest_url is not None:
            self.rest_url = rest_url
        self.saves += 1


def _fast_bucket() -> rl.TokenBucket:
    """A token bucket that never blocks (huge burst) — pacing isn't under test in
    the 429 storm; the breaker + counters are. Keeps the storm loop instant."""
    return rl.TokenBucket(rate_per_sec=1_000_000.0, burst=1_000_000)


# ===========================================================================
# Class 1 — refresh-rotation crash-safety
# ===========================================================================


def test_crash_between_exchange_and_use_recovers_from_persisted_state():
    """Death between token exchange and first use → a fresh client rebuilt from
    the persisted refresh token recovers, no strand.

    Connect persists refresh R1, then the 'process dies' (we drop the instance). A
    brand-new BullhornAuth built ONLY from the persisted R1 must authenticate and
    serve a real read — proving persist-before-use makes the rotation crash-safe.
    """
    state = FakeBullhornState()
    org = state.make_org("org1")
    state.make_candidate(org, name="Ada Lovelace")
    store = _Store()

    with live_bullhorn_server(state) as server:
        # --- first process: connect, persist R1, then "crash" (drop it) --------
        auth1 = _make_auth(server, org, persist=lambda **kw: store.save(**kw))
        auth1.authorize_with_password()
        assert store.refresh_token  # R1 durably written before any use
        r1 = store.refresh_token
        del auth1  # crash: in-memory access/session lost; only the store survives

        # --- second process: rebuild from the store, no password ---------------
        auth2 = BullhornAuth(
            username=org.username,
            client_id=org.client_id,
            client_secret=org.client_secret,
            refresh_token=store.refresh_token,  # the persisted R1
            persist_tokens=lambda **kw: store.save(**kw),
            discovery_url=server.discovery_url,
        )
        svc = BullhornService(auth2, client_id=org.client_id)

        rows = svc.search_candidates(fields="id,name")
        assert len(rows) == 1 and rows[0]["name"] == "Ada Lovelace"

    # Recovery consumed R1 and rotated to a fresh token, persisted in turn.
    assert store.refresh_token != r1
    assert store.saves == 2  # connect + the recovery refresh, each saved once


def test_persist_death_mid_rotation_leaves_recoverable_state():
    """If the durable write dies DURING a rotation, we must not adopt the new
    access token — the org stays recoverable, never half-applied.

    Connect succeeds, then the store's save raises on the next rotation (a DB
    crash at persist time): the rotation raises a typed auth error and the
    in-memory access token is unchanged, so no rotated token whose refresh half
    was lost is ever used.
    """
    state = FakeBullhornState()
    org = state.make_org("org1")
    store = _Store()

    def _persist(**kw):
        if store.saves >= 1:  # succeed on connect, die on the first rotation
            raise RuntimeError("db write failed mid-rotation")
        store.save(**kw)

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=_persist)
        auth.authorize_with_password()
        token_before = auth._access_token  # noqa: SLF001 — asserting the invariant

        with pytest.raises(BullhornAuthError):
            auth.refresh_access_token()

        assert auth._access_token == token_before  # noqa: SLF001 — not adopted


def test_reused_refresh_token_is_a_clean_typed_auth_error():
    """The strand signal: a spent (single-use) refresh token → invalid_grant →
    BullhornAuthError, never a silent success."""
    state = FakeBullhornState()
    org = state.make_org("org1")

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=lambda **kw: None)
        auth.authorize_with_password()
        spent = auth._refresh_token  # noqa: SLF001
        auth.refresh_access_token()  # spends `spent`, rotates forward

        stale = _make_auth(server, org, persist=lambda **kw: None, refresh_token=spent)
        with pytest.raises(BullhornAuthError):
            stale.refresh_access_token()


# ===========================================================================
# Class 7 — 429 storm
# ===========================================================================


def test_429_storm_backs_off_and_opens_breaker_far_under_disable_threshold():
    """A sustained 429 stream: the client backs off, the breaker opens, and the
    429s we provoke stay orders of magnitude under Bullhorn's ~9,000-in-5-min
    user-disable threshold. Once open, further calls fail fast WITHOUT serving
    another 429 (protecting the customer's API user).
    """
    state = FakeBullhornState()
    org = state.make_org("org1")
    state.make_candidate(org)

    sleeps: list[float] = []
    # Small breaker so "opens" is observable deterministically; the real default
    # (500) is itself ~18x under the 9,000 disable line — we assert the intent,
    # not the production constant.
    breaker = rl.CircuitBreaker(max_429=10, window_sec=300.0)

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=lambda **kw: None)
        auth.authorize_with_password()
        svc = BullhornService(
            auth,
            client_id=org.client_id,
            bucket=_fast_bucket(),
            breaker=breaker,
            time_sleep=lambda s: sleeps.append(s),
        )
        # Warm a session BEFORE the storm (login isn't a counted REST request).
        assert svc.ping()["sessionExpires"] > 0

        # Now every REST request 429s.
        state.fail_every_nth_request_with_429(1)

        # Hammer until the breaker trips and a call fails fast. Bound the loop so a
        # regression that never opens the breaker fails the test instead of hanging.
        opened = False
        for _ in range(50):
            with pytest.raises(BullhornRateLimitError):
                svc.ping()
            if breaker.is_open():
                opened = True
                break
        assert opened, "circuit breaker never opened under a sustained 429 storm"

        # It backed off (slept) rather than busy-looping on the 429s.
        assert sleeps, "client did not back off on 429s"

        # With the breaker open, the next call raises BEFORE reaching the fake:
        # the served-429 counter must not move.
        served_before = state.count_429_served
        with pytest.raises(BullhornRateLimitError):
            svc.ping()
        assert state.count_429_served == served_before  # fail-fast, nothing served

    # The whole storm provoked only a handful of 429s — many orders of magnitude
    # under the 9,000-in-5-min disable threshold. This is the safety assertion.
    assert state.count_429_served < 900


def test_429_backoff_respects_the_bounded_retry_budget():
    """A call under 429 pressure retries a BOUNDED number of times then raises —
    it does not retry forever. With exactly ``BULLHORN_MAX_ATTEMPTS`` 429s queued,
    the client backs off ``MAX_ATTEMPTS - 1`` times (the last attempt raises), and
    each backoff wait tracks the ``Retry-After`` the fake served (1s)."""
    state = FakeBullhornState()
    org = state.make_org("org1")
    state.make_candidate(org)
    sleeps: list[float] = []

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=lambda **kw: None)
        auth.authorize_with_password()
        svc = BullhornService(
            auth,
            client_id=org.client_id,
            bucket=_fast_bucket(),
            breaker=rl.CircuitBreaker(max_429=1000, window_sec=300.0),
            time_sleep=lambda s: sleeps.append(s),
        )
        assert svc.ping()["sessionExpires"] > 0

        # Exactly enough 429s to exhaust the retry budget for one call, so it
        # raises but we capture the backoff waits it took.
        state.fail_next_requests_with_429(rl.BULLHORN_MAX_ATTEMPTS)
        with pytest.raises(BullhornRateLimitError):
            svc.ping()

    # Bounded budget: MAX_ATTEMPTS-1 backoffs (the final attempt raises), each
    # honoring the served Retry-After header (1s), never a runaway retry loop.
    assert len(sleeps) == rl.BULLHORN_MAX_ATTEMPTS - 1
    assert all(s == 1.0 for s in sleeps)


# ===========================================================================
# Class 8 — verb discipline
# ===========================================================================


def test_create_is_put_and_update_is_post_end_to_end():
    """Through the real client against the fake: a create round-trips as PUT and a
    subsequent update of that record round-trips as POST."""
    state = FakeBullhornState()
    org = state.make_org("org1")

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=lambda **kw: None)
        auth.authorize_with_password()
        svc = BullhornService(auth, client_id=org.client_id)

        created = svc._create("Candidate", {"name": "New Person"})  # noqa: SLF001
        new_id = created["changedEntityId"]
        assert created["changeType"] == "INSERT"

        updated = svc._update("Candidate", new_id, {"status": "Contacted"})  # noqa: SLF001
        assert updated["changeType"] == "UPDATE"
        assert updated["data"]["status"] == "Contacted"


def test_put_create_on_existing_id_fails_loudly():
    """Verb inversion: a create (PUT) aimed at an already-existing id is rejected
    by the fake and surfaces as a typed BullhornApiError, not a silent overwrite."""
    state = FakeBullhornState()
    org = state.make_org("org1")
    existing = state.make_candidate(org)

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=lambda **kw: None)
        auth.authorize_with_password()
        svc = BullhornService(auth, client_id=org.client_id)

        with pytest.raises(BullhornApiError) as exc:
            svc._create("Candidate", {"id": existing["id"], "name": "Dup"})  # noqa: SLF001
        assert exc.value.status_code == 400


def test_post_update_on_missing_id_fails_loudly():
    """Verb inversion: an update (POST) aimed at a non-existent id is rejected by
    the fake and surfaces as a typed BullhornApiError, not a stray create."""
    state = FakeBullhornState()
    org = state.make_org("org1")

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=lambda **kw: None)
        auth.authorize_with_password()
        svc = BullhornService(auth, client_id=org.client_id)

        with pytest.raises(BullhornApiError) as exc:
            svc._update("Candidate", 999999, {"status": "X"})  # noqa: SLF001
        assert exc.value.status_code == 400
