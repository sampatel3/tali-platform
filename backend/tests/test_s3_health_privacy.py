"""Stable, secret-safe object-storage health diagnostics."""

from __future__ import annotations

import logging

import boto3
import pytest
from botocore.exceptions import ClientError

from app.platform.config import settings
from app.services import s3_health_diagnostics as diagnostics, s3_service


@pytest.fixture(autouse=True)
def _reset_s3_health_cache():
    s3_service.reset_s3_health_cache()
    yield
    s3_service.reset_s3_health_cache()


@pytest.fixture
def configured_s3(monkeypatch):
    monkeypatch.setattr(settings, "S3_DISABLED", False)
    monkeypatch.setattr(settings, "AWS_ACCESS_KEY_ID", "configured-access-key")
    monkeypatch.setattr(settings, "AWS_SECRET_ACCESS_KEY", "configured-secret-key")
    monkeypatch.setattr(settings, "AWS_S3_BUCKET", "operator-visible-bucket")
    monkeypatch.setattr(settings, "AWS_REGION", "eu-west-2")


def _client_error(*, code: str, message: str, status_code: int) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": status_code},
        },
        "HeadBucket",
    )


def _raise(error: BaseException):
    raise error


def test_healthy_probe_preserves_operational_context(configured_s3, monkeypatch):
    class Client:
        def head_bucket(self, **_kwargs):
            return None

    monkeypatch.setattr(boto3, "client", lambda *_args, **_kwargs: Client())

    assert s3_service.s3_status() == {
        "available": True,
        "ok": True,
        "configured": True,
        "bucket": "operator-visible-bucket",
        "region": "eu-west-2",
        "status": "ok",
        "reason": "ok",
    }


def test_probe_caches_bounded_provider_diagnostics_not_message(
    configured_s3,
    monkeypatch,
    caplog,
):
    marker = "secret://provider-message?token=must-not-escape"
    error = _client_error(code="InvalidAccessKeyId", message=marker, status_code=403)
    calls = 0

    class Client:
        def head_bucket(self, **_kwargs):
            nonlocal calls
            calls += 1
            _raise(error)

    monkeypatch.setattr(boto3, "client", lambda *_args, **_kwargs: Client())
    caplog.set_level(logging.DEBUG, logger="taali.s3")

    expected = {
        "available": False,
        "ok": False,
        "configured": True,
        "bucket": "operator-visible-bucket",
        "region": "eu-west-2",
        "status": "credentials_rejected",
        "reason": "credentials_rejected",
        "provider_code": "InvalidAccessKeyId",
        "provider_status_code": 403,
    }
    assert s3_service.s3_status() == expected
    assert s3_service.s3_status() == expected
    assert calls == 1
    assert marker not in repr(expected)
    assert marker not in s3_service._health_reason
    assert marker not in caplog.text


def test_unknown_provider_code_and_body_are_not_reflected(
    configured_s3,
    monkeypatch,
    caplog,
):
    marker = "private-provider-code-and-body"
    error = _client_error(code=marker, message=marker, status_code=418)

    class Client:
        def head_bucket(self, **_kwargs):
            _raise(error)

    monkeypatch.setattr(boto3, "client", lambda *_args, **_kwargs: Client())
    caplog.set_level(logging.DEBUG, logger="taali.s3")

    status = s3_service.s3_status()

    assert status["status"] == "provider_rejected"
    assert status["provider_status_code"] == 418
    assert "provider_code" not in status
    assert marker not in repr(status)
    assert marker not in s3_service._health_reason
    assert marker not in caplog.text


def test_probe_cache_rejects_uncontrolled_diagnostic_values(
    configured_s3,
    monkeypatch,
    caplog,
):
    marker = "arbitrary-provider-diagnostic-must-not-be-cached"
    monkeypatch.setattr(
        s3_service,
        "_probe_health",
        lambda: (False, marker, marker, marker),
    )
    caplog.set_level(logging.DEBUG, logger="taali.s3")

    status = s3_service.s3_status()

    assert status["status"] == "probe_error"
    assert "provider_code" not in status
    assert "provider_status_code" not in status
    assert s3_service._health_reason == "probe_error"
    assert marker not in repr(status)
    assert marker not in caplog.text


def test_non_auth_operation_failure_logs_only_structured_diagnostics(
    configured_s3,
    monkeypatch,
    caplog,
):
    marker = "s3://private-provider-body/candidate-name"
    error = _client_error(code="AccessDenied", message=marker, status_code=403)

    class Client:
        def upload_file(self, *_args, **_kwargs):
            _raise(error)

    monkeypatch.setattr(s3_service, "_get_client", lambda: (Client(), "operator-visible-bucket"))
    caplog.set_level(logging.DEBUG, logger="taali.s3")

    assert s3_service.upload_to_s3("/private/candidate.pdf", "private/key") is None
    assert "operation=upload_file" in caplog.text
    assert "status=access_denied" in caplog.text
    assert "provider_code=AccessDenied" in caplog.text
    assert marker not in caplog.text
    assert "/private/candidate.pdf" not in caplog.text
    assert "private/key" not in caplog.text


def test_provider_code_is_never_inferred_from_exception_message():
    marker = "InvalidAccessKeyId appears only in arbitrary exception text"

    status, code, status_code = diagnostics.provider_failure(
        RuntimeError(marker),
        default_status="transport_error",
    )

    assert (status, code, status_code) == ("transport_error", None, None)


def test_runtime_auth_failure_disables_uploads_without_caching_or_logging_body(
    configured_s3,
    monkeypatch,
    caplog,
):
    marker = "expired-token-response-body-must-not-escape"
    error = _client_error(code="ExpiredToken", message=marker, status_code=401)

    class Client:
        def upload_file(self, *_args, **_kwargs):
            _raise(error)

    monkeypatch.setattr(s3_service, "_get_client", lambda: (Client(), "operator-visible-bucket"))
    caplog.set_level(logging.DEBUG, logger="taali.s3")

    assert s3_service.upload_to_s3("/private/candidate.pdf", "private/key") is None
    status = s3_service.s3_status()

    assert status["status"] == "runtime_credentials_rejected"
    assert status["provider_code"] == "ExpiredToken"
    assert status["provider_status_code"] == 401
    assert marker not in repr(status)
    assert marker not in caplog.text


def test_admin_health_drops_arbitrary_s3_fields(client, configured_s3, monkeypatch):
    marker = "provider-response-body-must-not-reach-admin-health"
    monkeypatch.setattr(
        s3_service,
        "s3_status",
        lambda: {
            "available": False,
            "ok": False,
            "configured": True,
            "bucket": "operator-visible-bucket",
            "region": "eu-west-2",
            "status": "provider_rejected",
            "reason": marker,
            "provider_code": marker,
            "provider_status_code": 418,
            "provider_message": marker,
            "response_body": marker,
        },
    )

    response = client.get(
        "/admin/health",
        headers={"X-Admin-Secret": "test-admin-secret"},
    )

    assert response.status_code == 200
    assert response.json()["s3"] == {
        "available": False,
        "ok": False,
        "configured": True,
        "bucket": "operator-visible-bucket",
        "region": "eu-west-2",
        "status": "provider_rejected",
        "reason": "provider_rejected",
        "provider_status_code": 418,
    }
    assert marker not in response.text


def test_admin_health_uses_stable_fallback_when_probe_raises(
    client,
    configured_s3,
    monkeypatch,
):
    marker = "probe-crash-body-must-not-reach-admin-health"
    monkeypatch.setattr(s3_service, "s3_status", lambda: _raise(RuntimeError(marker)))

    response = client.get(
        "/admin/health",
        headers={"X-Admin-Secret": "test-admin-secret"},
    )

    assert response.status_code == 200
    assert response.json()["s3"] == {
        "available": False,
        "ok": False,
        "configured": True,
        "bucket": "operator-visible-bucket",
        "region": "eu-west-2",
        "status": "probe_error",
        "reason": "probe_error",
    }
    assert marker not in response.text
