"""Tests for the GitHub-credential watchdog that guards assessment repo
provisioning (the 2026-06-25 zero-traction incident: an expired GITHUB_TOKEN
silently blocked every candidate from starting).
"""
import logging

import httpx

from app.platform.sentry_privacy import OperationalAlert
from app.services import github_credentials
from app.services.github_credentials import verify_github_credentials
from app.tasks.assessment_tasks import assessment_provisioning_healthcheck

GET = "app.services.github_credentials.httpx.get"


class _Resp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def test_verify_mock_mode_ok():
    r = verify_github_credentials(org="taali-ai", token="tok", mock_mode=True)
    assert r["ok"] is True and r.get("mock") is True


def test_verify_no_token():
    r = verify_github_credentials(org="taali-ai", token="", mock_mode=False)
    assert r["ok"] is False
    assert "not set" in r["detail"].lower()


def test_verify_401(monkeypatch):
    provider_secret = "Bearer ghp-provider-secret from upstream body"
    monkeypatch.setattr(GET, lambda *a, **k: _Resp(401, provider_secret))
    r = verify_github_credentials(org="taali-ai", token="badtok", mock_mode=False)
    assert r["ok"] is False
    assert r["status_code"] == 401
    assert r["detail"] == "github_auth_failed"
    assert provider_secret not in str(r)


def test_verify_200_ok(monkeypatch):
    monkeypatch.setattr(GET, lambda *a, **k: _Resp(200, "{}"))
    r = verify_github_credentials(org="taali-ai", token="goodtok", mock_mode=False)
    assert r["ok"] is True
    assert r["status_code"] == 200


def test_verify_http_failure_never_returns_provider_body(monkeypatch):
    provider_secret = "ghp-provider-secret from upstream 503 body"
    monkeypatch.setattr(GET, lambda *a, **k: _Resp(503, provider_secret))

    r = verify_github_credentials(org="taali-ai", token="tok", mock_mode=False)

    assert r == {
        "ok": False,
        "status_code": 503,
        "detail": "github_http_error",
        "org": "taali-ai",
    }
    assert provider_secret not in str(r)


def test_verify_unreachable_does_not_raise(monkeypatch):
    provider_secret = "https://ghp-provider-secret@github.invalid/private"

    def boom(*a, **k):
        raise httpx.ConnectError(provider_secret)

    monkeypatch.setattr(GET, boom)
    r = verify_github_credentials(org="taali-ai", token="tok", mock_mode=False)
    assert r["ok"] is False
    assert r["status_code"] is None
    assert r["detail"] == "github_unreachable"
    assert provider_secret not in str(r)


def test_verify_unexpected_exception_never_returns_exception_text(monkeypatch):
    provider_secret = "Bearer ghp-provider-secret from transport wrapper"

    def boom(*a, **k):
        raise RuntimeError(provider_secret)

    monkeypatch.setattr(GET, boom)
    r = verify_github_credentials(org="taali-ai", token="tok", mock_mode=False)

    assert r == {
        "ok": False,
        "status_code": None,
        "detail": "github_request_failed",
        "org": "taali-ai",
    }
    assert provider_secret not in str(r)


def test_healthcheck_task_ok(monkeypatch):
    monkeypatch.setattr(
        github_credentials, "verify_github_credentials",
        lambda *a, **k: {"ok": True, "mock": True, "org": "taali-ai"},
    )
    assert assessment_provisioning_healthcheck()["ok"] is True


def test_healthcheck_task_alerts_on_failure(monkeypatch, caplog):
    alerts = []
    provider_secret = "Bearer ghp-provider-secret from upstream body"
    monkeypatch.setattr(
        "app.tasks.assessment_tasks.settings.GITHUB_ORG", "taali-ai"
    )
    monkeypatch.setattr(
        github_credentials, "verify_github_credentials",
        lambda *a, **k: {
            "ok": False,
            "status_code": 401,
            "detail": provider_secret,
            "org": "taali-ai",
        },
    )
    monkeypatch.setattr(
        "app.tasks.assessment_tasks.capture_operational_alert",
        lambda operation, **kwargs: alerts.append((operation, kwargs)),
    )
    with caplog.at_level(logging.ERROR):
        result = assessment_provisioning_healthcheck()
    assert result["ok"] is False
    assert "assessment_provisioning_unhealthy" in caplog.text
    assert provider_secret not in caplog.text
    assert all(
        provider_secret not in repr(record.__dict__) for record in caplog.records
    )
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "assessment_provisioning_unhealthy"
    )
    assert record.status_code == 401
    assert record.org == "taali-ai"
    assert not hasattr(record, "check")
    assert alerts == [
        (
            OperationalAlert.ASSESSMENT_PROVISIONING_UNHEALTHY,
            {"metrics": {"status_code": 401}},
        )
    ]
    assert provider_secret not in repr(alerts)
