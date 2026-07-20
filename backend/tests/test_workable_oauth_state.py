from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi_users.jwt import decode_jwt, generate_jwt

from app.domains.identity_access import workable_oauth_state
from app.platform.config import settings
from tests.conftest import auth_headers


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, key: str, value: str, *, ex: int | None = None):
        assert ex == workable_oauth_state.WORKABLE_OAUTH_STATE_LIFETIME_SECONDS
        self.values[key] = value
        return True

    def eval(self, script: str, numkeys: int, key: str, expected: str) -> int:
        assert script == workable_oauth_state._CONSUME_MATCHING_RECEIPT
        assert numkeys == 1
        if self.values.get(key) != expected:
            return 0
        del self.values[key]
        return 1


class _TokenResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "access_token": "workable-access-token",
            "refresh_token": "workable-refresh-token",
            "subdomain": "example",
            "scope": "r_jobs r_candidates",
        }


@pytest.fixture
def oauth_store(monkeypatch) -> _FakeRedis:
    store = _FakeRedis()
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(settings, "WORKABLE_CLIENT_ID", "test-workable-client")
    monkeypatch.setattr(settings, "WORKABLE_CLIENT_SECRET", "test-workable-secret")
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://app.example.test")
    monkeypatch.setattr(workable_oauth_state, "_redis_client", lambda: store)
    return store


def _issued_state(client, headers: dict[str, str]) -> str:
    response = client.get(
        "/api/v1/organizations/workable/authorize-url",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    query = parse_qs(urlparse(response.json()["url"]).query)
    assert query["redirect_uri"] == [
        "https://app.example.test/settings/workable/callback"
    ]
    return query["state"][0]


def test_oauth_state_success_is_single_use(
    client,
    oauth_store: _FakeRedis,
    monkeypatch,
) -> None:
    headers, _ = auth_headers(client, organization_name="OAuth success org")
    state = _issued_state(client, headers)
    outbound_codes: list[str] = []

    def _exchange(_url: str, *, data: dict):
        outbound_codes.append(data["code"])
        return _TokenResponse()

    monkeypatch.setattr(httpx, "post", _exchange)

    connected = client.post(
        "/api/v1/organizations/workable/connect",
        json={"code": "one-time-code", "state": state},
        headers=headers,
    )
    assert connected.status_code == 200, connected.text
    assert connected.json() == {"success": True, "subdomain": "example"}
    assert outbound_codes == ["one-time-code"]
    assert oauth_store.values == {}

    replay = client.post(
        "/api/v1/organizations/workable/connect",
        json={"code": "replayed-code", "state": state},
        headers=headers,
    )
    assert replay.status_code == 400, replay.text
    assert "invalid, expired, or already used" in replay.json()["detail"].lower()
    assert outbound_codes == ["one-time-code"]


def test_new_authorize_request_replaces_same_users_outstanding_receipt(
    oauth_store: _FakeRedis,
) -> None:
    first = workable_oauth_state.mint_workable_oauth_state(
        user_id=101,
        organization_id=7,
    )
    second = workable_oauth_state.mint_workable_oauth_state(
        user_id=101,
        organization_id=7,
    )

    assert first != second
    assert len(oauth_store.values) == 1
    with pytest.raises(workable_oauth_state.InvalidWorkableOAuthState):
        workable_oauth_state.consume_workable_oauth_state(
            first,
            user_id=101,
            organization_id=7,
        )

    # A stale callback cannot consume the newer receipt.
    assert len(oauth_store.values) == 1
    workable_oauth_state.consume_workable_oauth_state(
        second,
        user_id=101,
        organization_id=7,
    )
    assert oauth_store.values == {}


def test_oauth_receipts_are_independently_bounded_per_user_and_organization(
    oauth_store: _FakeRedis,
) -> None:
    states = [
        workable_oauth_state.mint_workable_oauth_state(
            user_id=user_id,
            organization_id=organization_id,
        )
        for user_id, organization_id in ((101, 7), (102, 7), (101, 8))
    ]

    assert len(oauth_store.values) == 3
    for state, (user_id, organization_id) in zip(
        states,
        ((101, 7), (102, 7), (101, 8)),
        strict=True,
    ):
        workable_oauth_state.consume_workable_oauth_state(
            state,
            user_id=user_id,
            organization_id=organization_id,
        )
    assert oauth_store.values == {}


def test_oauth_connect_rejects_missing_state_before_exchange(
    client,
    oauth_store: _FakeRedis,
    monkeypatch,
) -> None:
    headers, _ = auth_headers(client, organization_name="OAuth missing state org")
    _issued_state(client, headers)
    exchange = pytest.fail
    monkeypatch.setattr(httpx, "post", exchange)

    response = client.post(
        "/api/v1/organizations/workable/connect",
        json={"code": "code-without-state"},
        headers=headers,
    )

    assert response.status_code == 422, response.text
    assert len(oauth_store.values) == 1


def test_oauth_connect_rejects_tampered_state_without_consuming_valid_receipt(
    client,
    oauth_store: _FakeRedis,
    monkeypatch,
) -> None:
    headers, _ = auth_headers(client, organization_name="OAuth tamper org")
    state = _issued_state(client, headers)
    header, payload, signature = state.split(".")
    tampered_signature = f"{'A' if signature[0] != 'A' else 'B'}{signature[1:]}"
    tampered = ".".join((header, payload, tampered_signature))
    monkeypatch.setattr(httpx, "post", pytest.fail)

    response = client.post(
        "/api/v1/organizations/workable/connect",
        json={"code": "code", "state": tampered},
        headers=headers,
    )

    assert response.status_code == 400, response.text
    assert len(oauth_store.values) == 1


def test_oauth_state_is_bound_to_exact_user_and_organization(
    oauth_store: _FakeRedis,
) -> None:
    state = workable_oauth_state.mint_workable_oauth_state(
        user_id=101,
        organization_id=7,
    )

    with pytest.raises(workable_oauth_state.InvalidWorkableOAuthState):
        workable_oauth_state.consume_workable_oauth_state(
            state,
            user_id=102,
            organization_id=7,
        )
    with pytest.raises(workable_oauth_state.InvalidWorkableOAuthState):
        workable_oauth_state.consume_workable_oauth_state(
            state,
            user_id=101,
            organization_id=8,
        )

    # A mismatched caller cannot burn the rightful user's receipt.
    workable_oauth_state.consume_workable_oauth_state(
        state,
        user_id=101,
        organization_id=7,
    )
    assert oauth_store.values == {}


def test_expired_oauth_state_fails_before_receipt_consumption(
    oauth_store: _FakeRedis,
) -> None:
    valid = workable_oauth_state.mint_workable_oauth_state(
        user_id=201,
        organization_id=9,
    )
    claims = decode_jwt(
        valid,
        settings.SECRET_KEY,
        [workable_oauth_state.WORKABLE_OAUTH_STATE_AUDIENCE],
    )
    claims.pop("exp")
    expired = generate_jwt(claims, settings.SECRET_KEY, lifetime_seconds=-1)

    with pytest.raises(workable_oauth_state.InvalidWorkableOAuthState):
        workable_oauth_state.consume_workable_oauth_state(
            expired,
            user_id=201,
            organization_id=9,
        )
    assert len(oauth_store.values) == 1


def test_oauth_state_store_failure_is_fail_closed(client, oauth_store, monkeypatch) -> None:
    del oauth_store
    headers, _ = auth_headers(client, organization_name="OAuth unavailable org")
    monkeypatch.setattr(workable_oauth_state, "_redis_client", lambda: None)

    response = client.get(
        "/api/v1/organizations/workable/authorize-url",
        headers=headers,
    )

    assert response.status_code == 503, response.text
    assert "temporarily unavailable" in response.json()["detail"].lower()


def test_oauth_state_consume_fails_closed_when_store_is_unavailable(
    oauth_store,
    monkeypatch,
) -> None:
    state = workable_oauth_state.mint_workable_oauth_state(
        user_id=301,
        organization_id=10,
    )
    monkeypatch.setattr(workable_oauth_state, "_redis_client", lambda: None)

    with pytest.raises(workable_oauth_state.WorkableOAuthStateStoreUnavailable):
        workable_oauth_state.consume_workable_oauth_state(
            state,
            user_id=301,
            organization_id=10,
        )
