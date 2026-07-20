import pytest
from fastapi import HTTPException

from app.platform import admin_auth
from app.platform.config import settings


_ADMIN_SECRET = "dedicated-admin-secret-for-focused-tests"
_JWT_SECRET = "jwt-signing-secret-for-focused-tests"


@pytest.mark.parametrize(
    "provided",
    [None, "", "wrong-secret", "wrong-🔐", _JWT_SECRET],
)
def test_admin_secret_rejects_missing_wrong_and_jwt_key(monkeypatch, provided):
    monkeypatch.setattr(settings, "ADMIN_SECRET", _ADMIN_SECRET)
    monkeypatch.setattr(settings, "SECRET_KEY", _JWT_SECRET)

    with pytest.raises(HTTPException) as exc_info:
        admin_auth.require_admin_secret(provided)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Forbidden"


def test_admin_secret_accepts_dedicated_operator_key(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_SECRET", _ADMIN_SECRET)
    monkeypatch.setattr(settings, "SECRET_KEY", _JWT_SECRET)

    assert admin_auth.require_admin_secret(_ADMIN_SECRET) is None


def test_admin_secret_preserves_whitespace_tolerant_operator_calls(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_SECRET", f"  {_ADMIN_SECRET}  ")

    assert admin_auth.require_admin_secret(f" {_ADMIN_SECRET} ") is None


def test_admin_secret_fails_closed_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_SECRET", "")

    with pytest.raises(HTTPException) as exc_info:
        admin_auth.require_admin_secret("")

    assert exc_info.value.status_code == 403


def test_admin_secret_comparison_is_constant_time(monkeypatch):
    calls: list[tuple[bytes, bytes]] = []

    def _compare_digest(provided: bytes, expected: bytes) -> bool:
        calls.append((provided, expected))
        return False

    monkeypatch.setattr(settings, "ADMIN_SECRET", _ADMIN_SECRET)
    monkeypatch.setattr(admin_auth.hmac, "compare_digest", _compare_digest)

    with pytest.raises(HTTPException):
        admin_auth.require_admin_secret("wrong-secret")

    assert calls == [
        (b"wrong-secret", _ADMIN_SECRET.encode("utf-8")),
    ]


def test_main_admin_route_rejects_jwt_key_and_accepts_dedicated_key(
    client, monkeypatch
):
    from app.candidate_graph import client as graph_client

    monkeypatch.setattr(settings, "ADMIN_SECRET", _ADMIN_SECRET)
    monkeypatch.setattr(settings, "SECRET_KEY", _JWT_SECRET)
    monkeypatch.setattr(graph_client, "is_configured", lambda: False)

    missing = client.get("/admin/graphiti/search-debug")
    jwt_key = client.get(
        "/admin/graphiti/search-debug",
        headers={"X-Admin-Secret": _JWT_SECRET},
    )
    dedicated = client.get(
        "/admin/graphiti/search-debug",
        headers={"X-Admin-Secret": _ADMIN_SECRET},
    )

    assert missing.status_code == 403
    assert jwt_key.status_code == 403
    assert dedicated.status_code == 200
    assert dedicated.json() == {"status": "unconfigured"}
