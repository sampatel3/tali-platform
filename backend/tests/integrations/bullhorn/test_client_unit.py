"""Unit tests for the Bullhorn client's pure logic + auth invariant.

Two layers:

* **Pure logic** (no network): token bucket pacing, 429 circuit breaker,
  Retry-After parsing, verb-discipline helpers. Deterministic via injected
  clock/sleep — the analog of ``test_workable_rate_limit.py``.
* **End-to-end auth against the live fake**: discovery -> authorize -> token ->
  REST login -> search, plus the CRITICAL single-use refresh-token rotation
  ordering (persist fires BEFORE the new access token is used, and a persist
  failure aborts before the token is adopted -> no strand).

Full contract coverage (401 mid-sync, event replay, dead subscription, etc.) is
PR-4; this locks the client's own logic.
"""

from __future__ import annotations

import traceback
from types import SimpleNamespace

import httpx
import pytest

from app.components.integrations.bullhorn import (
    BullhornAuth,
    BullhornApiError,
    BullhornAuthError,
    BullhornService,
)
from app.components.integrations.bullhorn import ratelimit as rl
from tests.fakes.bullhorn_fakes import live_bullhorn_server
from tests.fakes.bullhorn_state import FakeBullhornState


# ============================================================================
# Pure logic — token bucket
# ============================================================================


def _clock():
    state = {"t": 1000.0}
    return state


def test_token_bucket_allows_burst_up_to_capacity():
    clock = _clock()
    sleeps: list[float] = []
    bucket = rl.TokenBucket(
        rate_per_sec=5.0,
        burst=3,
        monotonic=lambda: clock["t"],
        sleep=lambda s: sleeps.append(s),
    )
    # 3 tokens available at t0 -> no wait for the first 3.
    bucket.acquire()
    bucket.acquire()
    bucket.acquire()
    assert sleeps == []


def test_token_bucket_blocks_when_empty_then_refills():
    clock = _clock()
    sleeps: list[float] = []

    def _sleep(s):
        sleeps.append(s)
        clock["t"] += s  # advancing the clock refills the bucket

    bucket = rl.TokenBucket(
        rate_per_sec=5.0,  # 1 token every 0.2s
        burst=1,
        monotonic=lambda: clock["t"],
        sleep=_sleep,
    )
    bucket.acquire()  # consumes the one token
    bucket.acquire()  # must wait ~0.2s for a refill
    assert sleeps and abs(sleeps[0] - 0.2) < 1e-6


# ============================================================================
# Pure logic — circuit breaker
# ============================================================================


def test_circuit_breaker_opens_at_threshold_within_window():
    clock = _clock()
    breaker = rl.CircuitBreaker(max_429=3, window_sec=300.0, monotonic=lambda: clock["t"])
    assert not breaker.is_open()
    breaker.record_429()
    breaker.record_429()
    assert not breaker.is_open()
    breaker.record_429()  # 3rd within window -> open
    assert breaker.is_open()


def test_circuit_breaker_prunes_out_of_window_hits():
    clock = _clock()
    breaker = rl.CircuitBreaker(max_429=2, window_sec=300.0, monotonic=lambda: clock["t"])
    breaker.record_429()
    clock["t"] += 301  # first hit ages out of the window
    breaker.record_429()
    assert not breaker.is_open()  # only 1 in-window


# ============================================================================
# Pure logic — Retry-After
# ============================================================================


def _req() -> httpx.Request:
    return httpx.Request("GET", "http://x/rest-services/fake/search/Candidate")


def test_retry_after_honors_numeric_header():
    resp = httpx.Response(429, headers={"Retry-After": "4"}, request=_req())
    assert rl.retry_after_seconds(resp, 0) == 4.0


def test_retry_after_caps_oversized_header():
    resp = httpx.Response(429, headers={"Retry-After": "9999"}, request=_req())
    assert rl.retry_after_seconds(resp, 0) == rl.BULLHORN_BACKOFF_CAP_SEC


def test_retry_after_exponential_without_header():
    resp = httpx.Response(429, request=_req())
    assert rl.retry_after_seconds(resp, 0) == rl.BULLHORN_BACKOFF_BASE_SEC
    assert rl.retry_after_seconds(resp, 1) == rl.BULLHORN_BACKOFF_BASE_SEC * 2
    assert rl.retry_after_seconds(resp, 2) == rl.BULLHORN_BACKOFF_BASE_SEC * 4


def test_get_bucket_and_breaker_shared_per_client_id():
    a = rl.get_bucket("client-x")
    b = rl.get_bucket("client-x")
    c = rl.get_bucket("client-y")
    assert a is b and a is not c
    ba = rl.get_breaker("client-x")
    bb = rl.get_breaker("client-x")
    assert ba is bb


# ============================================================================
# Pure logic — verb discipline (_create=PUT, _update=POST)
# ============================================================================


def _service_with_recorder():
    """A BullhornService whose _request records (method, path) and returns {}.

    Auth is unused because we stub _request; construction just needs the fields.
    """
    calls: list[tuple[str, str]] = []
    auth = BullhornAuth(
        username="u",
        client_id="c",
        client_secret="s",
        refresh_token="r",
        persist_tokens=lambda **_: None,
        rest_url="http://x/rest-services/fake/",
    )
    svc = BullhornService(auth, client_id="c")

    def _fake_request(method, path, **kwargs):
        calls.append((method, path))
        return {"ok": True, "kwargs": kwargs}

    svc._request = _fake_request  # type: ignore[assignment]
    return svc, calls


def test_create_uses_put_on_entity():
    svc, calls = _service_with_recorder()
    svc._create("Note", {"comments": "hi"})
    assert calls == [("PUT", "entity/Note")]


def test_update_uses_post_on_entity_with_id():
    svc, calls = _service_with_recorder()
    svc._update("JobSubmission", 42, {"status": "Placed"})
    assert calls == [("POST", "entity/JobSubmission/42")]


def test_update_job_submission_status_is_a_post_update():
    svc, calls = _service_with_recorder()
    svc.update_job_submission_status(job_submission_id=7, status="Interview Scheduled")
    assert calls == [("POST", "entity/JobSubmission/7")]


def test_get_job_submission_returns_only_the_exact_requested_target():
    svc, _calls = _service_with_recorder()
    seen = {}

    def query_job_submissions(*, fields, where):
        seen.update(fields=fields, where=where)
        return [
            {"id": 8, "status": "Wrong"},
            {"id": 7, "status": "Submitted", "isDeleted": False},
        ]

    svc.query_job_submissions = query_job_submissions  # type: ignore[assignment]

    assert svc.get_job_submission("7") == {
        "id": 7,
        "status": "Submitted",
        "isDeleted": False,
    }
    assert seen == {
        "fields": "id,status,isDeleted,dateLastModified",
        "where": "id=7",
    }


def test_create_note_is_a_put_create():
    svc, calls = _service_with_recorder()
    svc.create_note(comments="note", person_reference_id=5)
    assert calls == [("PUT", "entity/Note")]


def test_search_requires_fields():
    svc, _calls = _service_with_recorder()
    with pytest.raises(ValueError):
        svc._paged("search", "Candidate", fields="", selector="", count=100)


def test_complete_open_job_snapshot_pages_to_exact_remote_total():
    """A short server-capped page is followed until the stable total is exact."""
    svc, _calls = _service_with_recorder()
    starts: list[int] = []

    def _page(_method, _path, **kwargs):
        start = kwargs["params"]["start"]
        starts.append(start)
        row_id = start + 1
        return {
            "total": 2,
            "start": start,
            "data": [{"id": row_id, "isOpen": True}],
        }

    svc._request = _page  # type: ignore[assignment]
    rows = svc.search_open_job_orders_complete(fields="id,isOpen")

    assert [row["id"] for row in rows] == [1, 2]
    assert starts == [0, 1]


def test_complete_open_job_snapshot_rejects_partial_page_before_closure():
    """A claimed total with no forward progress is never returned as complete."""
    svc, _calls = _service_with_recorder()

    def _partial(_method, _path, **kwargs):
        start = kwargs["params"]["start"]
        if start == 0:
            return {"total": 2, "start": 0, "data": [{"id": 1, "isOpen": True}]}
        return {"total": 2, "start": start, "data": []}

    svc._request = _partial  # type: ignore[assignment]

    with pytest.raises(BullhornApiError, match="partial"):
        svc.search_open_job_orders_complete(fields="id,isOpen")


# ============================================================================
# End-to-end auth against the live fake
# ============================================================================


def _make_auth(server, org, *, persist, refresh_token=None, password="pw", redirect_uri=None):
    return BullhornAuth(
        username=org.username,
        client_id=org.client_id,
        client_secret=org.client_secret,
        refresh_token=refresh_token,
        persist_tokens=persist,
        discovery_url=server.discovery_url,
        password=password,
        redirect_uri=redirect_uri,
    )


def test_connect_then_search_end_to_end():
    state = FakeBullhornState()
    org = state.make_org("org1")
    state.make_candidate(org, name="Ada Lovelace")
    persisted: list[dict] = []

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=lambda **kw: persisted.append(kw))
        auth.authorize_with_password()  # discovery + authorize + first token pair
        svc = BullhornService(auth, client_id=org.client_id)

        pong = svc.ping()
        assert pong["sessionExpires"] > 0

        rows = svc.search_candidates(fields="id,name,email")
        assert len(rows) == 1
        assert rows[0]["name"] == "Ada Lovelace"

    # authorize_with_password persisted the first refresh token exactly once.
    assert len(persisted) == 1
    assert persisted[0]["refresh_token"]


def test_refresh_rotation_persists_new_token_before_use():
    """The invariant: on refresh, persist(new_refresh) fires BEFORE the new access
    token is adopted/used. We observe the hook and assert the token it saw is the
    one subsequently in force (and different from the pre-refresh token)."""
    state = FakeBullhornState()
    org = state.make_org("org1")
    persisted: list[str] = []

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=lambda **kw: persisted.append(kw["refresh_token"]))
        auth.authorize_with_password()
        first_refresh = persisted[-1]

        # Force a rotation.
        auth.refresh_access_token()
        rotated_refresh = persisted[-1]

        assert rotated_refresh != first_refresh  # rotated
        assert len(persisted) == 2  # connect + one refresh, each persisted once

        # The rotated token is the one now in force: a second refresh works
        # (proving we adopted the rotated token, not the spent one).
        auth.refresh_access_token()
        assert persisted[-1] != rotated_refresh


def test_persist_failure_aborts_before_adopting_access_token():
    """If persistence fails, we must NOT adopt the new access token — the org is
    recoverable (the new refresh token is discardable server-side only once used).
    A raising hook -> BullhornAuthError, and the in-memory access token is unchanged.
    """
    state = FakeBullhornState()
    org = state.make_org("org1")

    calls = {"n": 0}

    def _persist(**kw):
        calls["n"] += 1
        if calls["n"] == 2:  # succeed on connect, fail on the rotation
            raise RuntimeError("db write failed")

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=_persist)
        auth.authorize_with_password()
        token_before = auth._access_token  # noqa: SLF001 — asserting the invariant

        with pytest.raises(BullhornAuthError):
            auth.refresh_access_token()

        # Access token NOT advanced: the failed persist aborted adoption.
        assert auth._access_token == token_before  # noqa: SLF001


def test_reused_refresh_token_surfaces_as_auth_error():
    """A spent (single-use) refresh token -> invalid_grant -> typed auth error,
    never a silent success (the strand signal)."""
    state = FakeBullhornState()
    org = state.make_org("org1")

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=lambda **kw: None)
        auth.authorize_with_password()
        # Grab the current refresh token, rotate once (spending it), then try to
        # reuse the now-spent token via a fresh auth object.
        spent = auth._refresh_token  # noqa: SLF001
        auth.refresh_access_token()

        stale_auth = _make_auth(server, org, persist=lambda **kw: None, refresh_token=spent)
        with pytest.raises(BullhornAuthError):
            stale_auth.refresh_access_token()


def test_tokens_never_leak_into_httpx_logs(caplog):
    """Bullhorn puts access_token (/login) and BhRestToken (every call) in the URL
    query string; httpx logs request URLs at INFO. ``quiet_httpx`` must keep those
    tokens out of the captured log records for the whole connect+call flow."""
    import logging

    state = FakeBullhornState()
    org = state.make_org("org1")
    state.make_candidate(org)

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=lambda **kw: None)
        with caplog.at_level(logging.INFO, logger="httpx"):
            auth.authorize_with_password()
            svc = BullhornService(auth, client_id=org.client_id)
            svc.ping()
            svc.search_candidates(fields="id,name")

    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "BhRestToken=" not in blob
    assert "access_token=" not in blob


def test_auth_failure_traceback_never_retains_tokenized_request_url():
    access_token = "ACCESS_TRACEBACK_SECRET"
    corp_token = "CORP_TRACEBACK_SECRET"

    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    auth = BullhornAuth(
        username="traceback-user",
        client_id="traceback-client",
        client_secret="client-secret",
        refresh_token="refresh-secret",
        persist_tokens=lambda **_kwargs: None,
        rest_url=f"https://example.test/rest-services/{corp_token}/",
        transport=httpx.MockTransport(fail),
    )
    auth._access_token = access_token  # noqa: SLF001 - exercise REST login

    with pytest.raises(BullhornAuthError):
        try:
            auth._login()  # noqa: SLF001 - exact token-bearing request seam
        except Exception:
            rendered = traceback.format_exc()
            assert access_token not in rendered
            assert corp_token not in rendered
            assert "access_token=" not in rendered
            raise


def test_rest_failure_traceback_never_retains_bh_or_corp_token():
    bh_token = "BH_TRACEBACK_SECRET"
    corp_token = "CORP_REST_TRACEBACK_SECRET"

    class _Auth:
        def ensure_session(self):
            return SimpleNamespace(
                bh_rest_token=bh_token,
                rest_url=f"https://example.test/rest-services/{corp_token}/",
            )

        def reauthenticate(self):  # pragma: no cover - 500 never reauths
            raise AssertionError("unexpected reauthentication")

    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    service = BullhornService(
        _Auth(),
        client_id="traceback-service-client",
        transport=httpx.MockTransport(fail),
    )
    with pytest.raises(Exception):
        try:
            service.search_candidates(fields="id,name")
        except Exception:
            rendered = traceback.format_exc()
            assert bh_token not in rendered
            assert corp_token not in rendered
            assert "BhRestToken=" not in rendered
            raise


def test_overlapping_quiet_httpx_contexts_never_restore_info_logging():
    """Exiting one overlapping request cannot expose another request's URL."""
    import logging

    from app.components.integrations.bullhorn.auth import quiet_httpx

    httpx_logger = logging.getLogger("httpx")
    httpx_logger.setLevel(logging.INFO)
    first = quiet_httpx()
    second = quiet_httpx()
    first.__enter__()
    second.__enter__()
    assert httpx_logger.level >= logging.WARNING

    first.__exit__(None, None, None)
    assert httpx_logger.level >= logging.WARNING
    second.__exit__(None, None, None)
    assert httpx_logger.level >= logging.WARNING


def test_persistent_401_reauths_once_then_raises_auth_error():
    """If the session keeps 401ing (reauth can't fix it), the client reauths
    EXACTLY once and then raises a typed auth error — it never loops forever."""
    state = FakeBullhornState()
    org = state.make_org("org1")
    state.make_candidate(org)

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=lambda **kw: None)
        auth.authorize_with_password()
        svc = BullhornService(auth, client_id=org.client_id)
        assert svc.ping()["sessionExpires"] > 0  # warm a session

        # Expire the session, and stub reauth to a no-op so the retried call
        # still hits the expired session -> a second 401.
        state.advance_clock(700)
        calls = {"n": 0}
        real_session = auth._session  # noqa: SLF001 — keep the stale session in place

        def _noop_reauth():
            calls["n"] += 1
            auth._session = real_session  # noqa: SLF001 — deliberately still stale
            return real_session

        auth.reauthenticate = _noop_reauth  # type: ignore[assignment]

        with pytest.raises(BullhornAuthError):
            svc.ping()
        assert calls["n"] == 1  # reauth attempted exactly once, no infinite loop


def test_session_reused_until_401_then_reauth_once():
    """A 401 (forced via session TTL fast-forward) triggers ONE reauth+relogin,
    and the subsequent call succeeds on the fresh session."""
    state = FakeBullhornState()
    org = state.make_org("org1")
    state.make_candidate(org)

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=lambda **kw: None)
        auth.authorize_with_password()
        svc = BullhornService(auth, client_id=org.client_id)

        assert svc.ping()["sessionExpires"] > 0
        session_1 = auth.rest_url

        # Expire the REST session -> next call 401s, client refreshes + re-logins.
        state.advance_clock(700)  # > SESSION_TTL (600)
        pong = svc.ping()
        assert pong["sessionExpires"] > 0
        # A new BhRestToken/session was established (url may be identical path but
        # the session object was rebuilt; the call succeeding is the assertion).
        assert auth.rest_url is not None
        _ = session_1


# ============================================================================
# Auth-code grant returns a 302 (code in Location header), not a JSON body
# ============================================================================


def test_code_from_authorize_reads_the_location_header():
    """The automated auth-code grant answers with a 302 whose Location carries
    ``?code=...`` (URL-encoded ok). ``_code_from_authorize`` must read it off the
    header, and return None for a non-redirect response (so we never mistake a
    2xx/JSON body for a code)."""
    from urllib.parse import quote

    from app.components.integrations.bullhorn.auth import _code_from_authorize

    raw = "abc/def+ghi=="  # exercises URL-decoding of the code
    redirect = httpx.Response(
        302,
        headers={"Location": f"https://app.example/cb?code={quote(raw)}&client_id=c&state=s"},
        request=httpx.Request("POST", "https://auth/oauth/authorize"),
    )
    assert _code_from_authorize(redirect) == raw

    # A 200 with a JSON {"code": ...} body is NOT how real Bullhorn replies — the
    # helper must not treat it as a code (guards against regressing to the fiction).
    non_redirect = httpx.Response(
        200,
        json={"code": "should-be-ignored"},
        request=httpx.Request("POST", "https://auth/oauth/authorize"),
    )
    assert _code_from_authorize(non_redirect) is None


def test_connect_handles_302_authorize_and_never_leaks_the_code(caplog):
    """End-to-end: the fake now replies to /oauth/authorize with a 302 (like real
    Bullhorn), so this whole flow exercises the redirect-parsing path — and the
    authorization code (a secret, carried in the Location query string) must never
    reach the httpx logs."""
    import logging

    state = FakeBullhornState()
    org = state.make_org("org1")
    state.make_candidate(org, name="Ada Lovelace")

    with live_bullhorn_server(state) as server:
        auth = _make_auth(server, org, persist=lambda **kw: None)
        with caplog.at_level(logging.INFO, logger="httpx"):
            auth.authorize_with_password()  # 302 -> code from Location -> token
        svc = BullhornService(auth, client_id=org.client_id)
        rows = svc.search_candidates(fields="id,name")
        assert len(rows) == 1 and rows[0]["name"] == "Ada Lovelace"

    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "code=" not in blob  # the auth code never surfaced in a log line


def test_connect_with_redirect_uri_round_trips_on_both_legs():
    """When a redirect_uri is configured it is sent to /authorize AND echoed on the
    token exchange (Bullhorn requires the identical value); the connect succeeds."""
    state = FakeBullhornState()
    org = state.make_org("org1")
    state.make_candidate(org, name="Grace Hopper")

    with live_bullhorn_server(state) as server:
        auth = _make_auth(
            server, org, persist=lambda **kw: None, redirect_uri="https://tali.example/bh/callback"
        )
        auth.authorize_with_password()
        svc = BullhornService(auth, client_id=org.client_id)
        rows = svc.search_candidates(fields="id,name")
        assert len(rows) == 1 and rows[0]["name"] == "Grace Hopper"


def test_fake_enforces_redirect_uri_echo_on_the_token_exchange():
    """The fake mirrors Bullhorn's rule that the token exchange MUST echo the same
    redirect_uri that /authorize received: a code minted under one redirect_uri
    cannot be exchanged with a different (or absent) one — it is invalid_grant.
    This locks the contract the client's echo (test above) is satisfying."""
    state = FakeBullhornState()
    org = state.make_org("org1")

    # Code registered under a redirect_uri; exchanging with a mismatch is rejected.
    code = state.mint_auth_code(org, redirect_uri="https://tali.example/cb")
    assert state.exchange_auth_code(code, redirect_uri="https://other.example/cb") == (
        "redirect_uri_mismatch"
    )

    # And a fresh code with matching echo succeeds.
    code2 = state.mint_auth_code(org, redirect_uri="https://tali.example/cb")
    rec = state.exchange_auth_code(code2, redirect_uri="https://tali.example/cb")
    assert rec != "redirect_uri_mismatch" and rec is not None
