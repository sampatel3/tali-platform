"""Tests for the GitHub-credential watchdog that guards assessment repo
provisioning (the 2026-06-25 zero-traction incident: an expired GITHUB_TOKEN
silently blocked every candidate from starting).
"""
import logging

import httpx

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
    monkeypatch.setattr(GET, lambda *a, **k: _Resp(401, '{"message":"Bad credentials"}'))
    r = verify_github_credentials(org="taali-ai", token="badtok", mock_mode=False)
    assert r["ok"] is False
    assert r["status_code"] == 401
    assert "Bad credentials" in r["detail"]


def test_verify_200_ok(monkeypatch):
    monkeypatch.setattr(GET, lambda *a, **k: _Resp(200, "{}"))
    r = verify_github_credentials(org="taali-ai", token="goodtok", mock_mode=False)
    assert r["ok"] is True
    assert r["status_code"] == 200


def test_verify_unreachable_does_not_raise(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("dns fail")

    monkeypatch.setattr(GET, boom)
    r = verify_github_credentials(org="taali-ai", token="tok", mock_mode=False)
    assert r["ok"] is False
    assert r["status_code"] is None


def test_healthcheck_task_ok(monkeypatch):
    monkeypatch.setattr(
        github_credentials, "verify_github_credentials",
        lambda *a, **k: {"ok": True, "mock": True, "org": "taali-ai"},
    )
    assert assessment_provisioning_healthcheck()["ok"] is True


def test_healthcheck_task_alerts_on_failure(monkeypatch, caplog):
    monkeypatch.setattr(
        github_credentials, "verify_github_credentials",
        lambda *a, **k: {"ok": False, "status_code": 401, "detail": "Bad credentials", "org": "taali-ai"},
    )
    with caplog.at_level(logging.ERROR):
        result = assessment_provisioning_healthcheck()
    assert result["ok"] is False
    assert "assessment_provisioning_unhealthy" in caplog.text
