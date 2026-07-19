from __future__ import annotations

import json
import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, field_validator

from app.main import validation_exception_handler
from app.platform import middleware as middleware_module
from app.platform.logging import JsonFormatter
from app.platform.middleware import RateLimitMiddleware, RequestLoggingMiddleware
from app.platform.request_context import (
    get_request_id,
    normalize_request_id,
    set_request_id,
)
from app.services import rate_limit as rate_limit_module


_CREDENTIAL_REQUEST_IDS = {
    "taali-live-key": "tali_live_" + "a" * 32,
    "taali-test-key": "tali_test_" + "b" * 32,
    "github-personal-token": "ghp_" + "c" * 32,
    "github-oauth-token": "gho_" + "d" * 32,
    "github-user-token": "ghu_" + "e" * 32,
    "github-server-token": "ghs_" + "f" * 32,
    "github-refresh-token": "ghr_" + "g" * 32,
    "github-fine-grained-token": "github_pat_" + "h" * 32,
    "webhook-secret": "whsec_" + "i" * 32,
    "report-token": "rpt_" + "j" * 32,
    "share-token": "shr_" + "k" * 32,
    "submittal-token": "sub_" + "m" * 32,
    "eeo-token": "eeo_" + "n" * 32,
    "restricted-key": "rk_" + "p" * 32,
    "generic-api-key": "api_" + "q" * 32,
    "e2b-api-key": "e2b_" + "r" * 32,
    "resend-api-key": "re_" + "s" * 32,
    "voyage-api-key": "pa-" + "t" * 32,
    "aws-long-lived-key": "AKIA" + "R" * 16,
    "aws-temporary-key": "ASIA" + "S" * 16,
}
_CREDENTIAL_PARAMETERS = pytest.mark.parametrize(
    "credential",
    _CREDENTIAL_REQUEST_IDS.values(),
    ids=_CREDENTIAL_REQUEST_IDS,
)


class _PrivateValidationPayload(BaseModel):
    value: str

    @field_validator("value")
    @classmethod
    def _reject_private_value(cls, value: str) -> str:
        raise ValueError(f"private validation value={value}")


class _DynamicLocationPayload(BaseModel):
    values: dict[str, int]


def _middleware_records(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "tali.middleware"]


def _formatted(record: logging.LogRecord) -> dict:
    return json.loads(JsonFormatter().format(record))


def test_request_id_normalization_preserves_safe_ids_and_obscures_unsafe_ids():
    assert normalize_request_id("legacy-run-now-retry_1") == "legacy-run-now-retry_1"

    private = "candidate@example.invalid/Bearer-private-value"
    first = normalize_request_id(private)
    assert first == normalize_request_id(private)
    assert first is not None and first.startswith("opaque-")
    assert private not in first

    oversized = "x" * 10_000
    assert normalize_request_id(oversized).startswith("opaque-")
    assert normalize_request_id("sk-private-credential").startswith("opaque-")


@pytest.mark.parametrize(
    "request_id",
    (
        "legacy-run-now-retry_1",
        "request_20260718_abc",
        "github_patch_release_2026",
        "tali-live-deploy-2026",
        "rpt_daily_2026",
        "shr_batch_42",
        "sub_batch_42",
        "e2b-job-42",
        "re_batch_42",
        "pa-run-42",
    ),
)
def test_request_id_normalization_does_not_overmatch_legacy_ids(request_id):
    assert normalize_request_id(request_id) == request_id


@_CREDENTIAL_PARAMETERS
def test_request_id_normalization_obscures_safe_alphabet_credentials(credential):
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,128}", credential)

    first = normalize_request_id(credential)

    assert first == normalize_request_id(credential)
    assert first is not None and first.startswith("opaque-")
    assert first != credential
    assert credential not in first


@_CREDENTIAL_PARAMETERS
def test_request_id_context_never_retains_safe_alphabet_credentials(credential):
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,128}", credential)
    token = set_request_id(credential)
    try:
        context_request_id = get_request_id()
        assert context_request_id == normalize_request_id(credential)
        assert context_request_id is not None
        assert context_request_id.startswith("opaque-")
        assert credential not in context_request_id
    finally:
        token.var.reset(token)


@_CREDENTIAL_PARAMETERS
def test_json_formatter_never_retains_safe_alphabet_credentials(credential):
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,128}", credential)
    record = logging.LogRecord(
        name="taali.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request complete",
        args=(),
        exc_info=None,
    )
    record.request_id = credential

    payload = _formatted(record)

    assert payload["request_id"] == normalize_request_id(credential)
    assert payload["request_id"].startswith("opaque-")
    assert credential not in json.dumps(payload)


@_CREDENTIAL_PARAMETERS
def test_request_logging_never_reflects_safe_alphabet_credentials(
    credential,
    caplog,
):
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,128}", credential)
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/context")
    def context() -> dict[str, str | None]:
        return {"request_id": get_request_id()}

    caplog.set_level(logging.INFO, logger="tali.middleware")
    with TestClient(app) as client:
        response = client.get("/context", headers={"X-Request-ID": credential})

    normalized = normalize_request_id(credential)
    assert response.status_code == 200
    assert normalized is not None and normalized.startswith("opaque-")
    assert response.headers["X-Request-ID"] == normalized
    assert response.json()["request_id"] == normalized
    payload = _formatted(_middleware_records(caplog)[0])
    assert payload["request_id"] == normalized
    assert credential not in json.dumps(payload)


def test_request_logging_uses_route_templates_and_normalized_durable_id(caplog):
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/items/{item_id}")
    def item() -> dict[str, str | None]:
        return {"request_id": get_request_id()}

    caplog.set_level(logging.INFO, logger="tali.middleware")
    private_id = "candidate@example.invalid/Bearer-private-value"
    with TestClient(app) as client:
        first = client.get(
            "/items/path-private-marker?token=query-private-marker",
            headers={"X-Request-ID": private_id},
        )
        second = client.get(
            "/items/other-private-marker?token=other-query-marker",
            headers={"X-Request-ID": private_id},
        )

    assert first.status_code == second.status_code == 200
    normalized = first.headers["X-Request-ID"]
    assert normalized.startswith("opaque-")
    assert second.headers["X-Request-ID"] == normalized
    assert first.json()["request_id"] == second.json()["request_id"] == normalized

    records = _middleware_records(caplog)
    assert len(records) == 2
    payloads = [_formatted(record) for record in records]
    assert {payload["request_id"] for payload in payloads} == {normalized}
    assert all("route=/items/{item_id}" in payload["message"] for payload in payloads)
    encoded = json.dumps(payloads)
    for marker in (
        private_id,
        "path-private-marker",
        "query-private-marker",
        "other-private-marker",
        "other-query-marker",
    ):
        assert marker not in encoded


def test_request_logging_groups_unmatched_paths_without_echoing_them(caplog):
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)
    caplog.set_level(logging.INFO, logger="tali.middleware")

    with TestClient(app) as client:
        response = client.get(
            "/missing/path-private-marker?token=query-private-marker",
            headers={"X-Request-ID": "safe-request-id"},
        )

    assert response.status_code == 404
    payload = _formatted(_middleware_records(caplog)[0])
    assert "route=<unmatched-route>" in payload["message"]
    assert payload["request_id"] == "safe-request-id"
    assert "private-marker" not in json.dumps(payload)


def test_rate_limit_log_keeps_enforcement_key_but_emits_only_safe_category(
    caplog,
    monkeypatch,
):
    checked: list[str] = []

    def deny(key: str, **_kwargs) -> bool:
        checked.append(key)
        return False

    monkeypatch.setattr(middleware_module, "check_rate_limit", deny)
    monkeypatch.setattr(
        middleware_module,
        "resolve_client_ip",
        lambda _request: "203.0.113.42",
    )
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)
    caplog.set_level(logging.WARNING, logger="tali.middleware")

    with TestClient(app) as client:
        response = client.post(
            "/prefix/api/v1/auth/login/path-private-marker",
            headers={"X-Request-ID": "safe-request-id"},
        )

    assert response.status_code == 429
    assert checked == ["auth:203.0.113.42"]
    record = _middleware_records(caplog)[0]
    first_message = record.getMessage()
    assert first_message.startswith("rate_limit_exceeded category=auth bucket=bucket-")
    assert "203.0.113.42" not in first_message
    assert "private-marker" not in first_message


def test_legacy_fireflies_has_per_ip_bucket_but_scoped_route_does_not(
    monkeypatch,
):
    monkeypatch.setattr(
        middleware_module.settings,
        "FIREFLIES_LEGACY_RATE_LIMIT_PER_MINUTE",
        73,
    )
    assert middleware_module._legacy_fireflies_buckets(
        "203.0.113.42",
        "/api/v1/webhooks/fireflies",
    ) == [
        (
            "fireflies_legacy:ip:203.0.113.42",
            73,
        ),
    ]
    assert middleware_module._legacy_fireflies_buckets(
        "203.0.113.42",
        "/api/v1/webhooks/fireflies/42",
    ) == []


def test_legacy_fireflies_request_executes_only_its_per_ip_abuse_budget(
    monkeypatch,
):
    checked: list[tuple[str, int, int]] = []

    def allow(key: str, *, limit: int, window_seconds: int) -> bool:
        checked.append((key, limit, window_seconds))
        return True

    monkeypatch.setattr(middleware_module, "check_rate_limit", allow)
    monkeypatch.setattr(
        middleware_module,
        "resolve_client_ip",
        lambda _request: "203.0.113.42",
    )
    monkeypatch.setattr(
        middleware_module.settings,
        "FIREFLIES_LEGACY_RATE_LIMIT_PER_MINUTE",
        73,
    )
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.post("/api/v1/webhooks/fireflies")
    def legacy_fireflies() -> dict[str, bool]:
        return {"accepted": True}

    @app.post("/api/v1/webhooks/fireflies/{organization_id}")
    def scoped_fireflies(organization_id: int) -> dict[str, int]:
        return {"organization_id": organization_id}

    with TestClient(app) as client:
        legacy_response = client.post("/api/v1/webhooks/fireflies")
        scoped_response = client.post("/api/v1/webhooks/fireflies/42")

    assert legacy_response.status_code == scoped_response.status_code == 200
    assert checked == [
        (
            "fireflies_legacy:ip:203.0.113.42",
            73,
            middleware_module._RATE_WINDOW_SEC,
        ),
    ]


def test_legacy_fireflies_limit_returns_retry_after_and_never_limits_scoped(
    monkeypatch,
):
    rate_limit_module.reset_memory_buckets()
    monkeypatch.setattr(
        middleware_module.settings,
        "FIREFLIES_LEGACY_RATE_LIMIT_PER_MINUTE",
        2,
    )
    monkeypatch.setattr(
        middleware_module,
        "resolve_client_ip",
        lambda _request: "203.0.113.42",
    )
    monkeypatch.setattr(
        middleware_module,
        "check_rate_limit",
        lambda key, *, limit, window_seconds: rate_limit_module._check_memory(
            key,
            limit,
            window_seconds,
        ),
    )
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.post("/api/v1/webhooks/fireflies")
    def legacy_fireflies() -> dict[str, bool]:
        return {"accepted": True}

    @app.post("/api/v1/webhooks/fireflies/{organization_id}")
    def scoped_fireflies(organization_id: int) -> dict[str, int]:
        return {"organization_id": organization_id}

    try:
        with TestClient(app) as client:
            assert client.post("/api/v1/webhooks/fireflies").status_code == 200
            assert client.post("/api/v1/webhooks/fireflies").status_code == 200
            limited = client.post("/api/v1/webhooks/fireflies")
            scoped = [
                client.post("/api/v1/webhooks/fireflies/42")
                for _ in range(3)
            ]
    finally:
        rate_limit_module.reset_memory_buckets()

    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == str(middleware_module._RATE_WINDOW_SEC)
    assert all(response.status_code == 200 for response in scoped)


def test_validation_logging_omits_private_message_value_and_dynamic_location(caplog):
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_middleware(RequestLoggingMiddleware)

    @app.post("/validate/{item_id}")
    def validate_private(item_id: str, payload: _PrivateValidationPayload):
        assert item_id
        return payload

    @app.post("/locations/{item_id}")
    def validate_location(item_id: str, payload: _DynamicLocationPayload):
        assert item_id
        return payload

    caplog.set_level(logging.WARNING, logger="taali.validation")
    private_request_id = "candidate@example.invalid/private-request"
    with TestClient(app) as client:
        private_response = client.post(
            "/validate/path-private-marker",
            json={"value": "body-private-marker"},
            headers={"X-Request-ID": private_request_id},
        )
        location_response = client.post(
            "/locations/path-private-marker",
            json={"values": {"location-private-marker": "not-an-integer"}},
            headers={"X-Request-ID": private_request_id},
        )

    assert private_response.status_code == location_response.status_code == 422
    records = [record for record in caplog.records if record.name == "taali.validation"]
    assert len(records) == 2
    payloads = [_formatted(record) for record in records]
    assert all(payload["request_id"].startswith("opaque-") for payload in payloads)
    assert "route=/validate/{item_id}" in payloads[0]["message"]
    assert "route=/locations/{item_id}" in payloads[1]["message"]
    assert "msg" not in payloads[0]["message"]
    assert "field-" in payloads[1]["message"]
    encoded = json.dumps(payloads)
    for marker in (
        private_request_id,
        "path-private-marker",
        "body-private-marker",
        "location-private-marker",
        "not-an-integer",
    ):
        assert marker not in encoded
