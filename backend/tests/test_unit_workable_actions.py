"""Unit tests for Workable client payloads and outbound action helpers."""

import os
from types import SimpleNamespace

os.environ["DATABASE_URL"] = "sqlite:///./test.db"

from app.components.integrations.workable.service import WorkableService
from app.services.workable_actions_service import (
    disqualify_candidate_in_workable,
    move_candidate_in_workable,
    render_workable_note_template,
    revert_candidate_disqualification_in_workable,
)


def _org(**overrides):
    config = {
        "email_mode": "manual_taali",
        "granted_scopes": ["r_jobs", "r_candidates", "w_candidates"],
        "workable_actor_member_id": "member-1",
        "workable_disqualify_reason_id": "reason-1",
        "auto_reject_note_template": "Auto reject {{candidate_name}} at {pre_screen_score}.",
    }
    override_config = overrides.pop("workable_config", None) or {}
    config.update(override_config)
    payload = {
        "id": 1,
        "workable_connected": True,
        "workable_access_token": "token",
        "workable_subdomain": "acme",
        "workable_config": config,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _role(**overrides):
    payload = {
        "id": 11,
        "name": "Backend Engineer",
        "workable_actor_member_id": None,
        "workable_disqualify_reason_id": None,
        "auto_reject_note_template": None,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _app(**overrides):
    payload = {
        "id": 21,
        "workable_candidate_id": "candidate-1",
        "pre_screen_score_100": 47.2,
        "pre_screen_recommendation": "reject",
        "candidate": SimpleNamespace(full_name="Taylor Candidate", email="taylor@example.com"),
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_workable_service_move_candidate_posts_member_id_and_target_stage(monkeypatch):
    captured = {}
    service = WorkableService(access_token="token", subdomain="acme")

    def fake_request(method, path, *, json=None, params=None):
        captured["method"] = method
        captured["path"] = path
        captured["json"] = json
        captured["params"] = params
        return {"ok": True}

    monkeypatch.setattr(service, "_request", fake_request)

    result = service.move_candidate(candidate_id="candidate-1", member_id="member-1", target_stage="Review")

    assert result["success"] is True
    assert captured == {
        "method": "POST",
        "path": "/candidates/candidate-1/move",
        "json": {
            "member_id": "member-1",
            "target_stage": "Review",
        },
        "params": None,
    }


def test_workable_service_disqualify_candidate_truncates_note(monkeypatch):
    captured = {}
    service = WorkableService(access_token="token", subdomain="acme")
    long_note = "x" * 300

    def fake_request(method, path, *, json=None, params=None):
        captured["method"] = method
        captured["path"] = path
        captured["json"] = json
        captured["params"] = params
        return {"ok": True}

    monkeypatch.setattr(service, "_request", fake_request)

    result = service.disqualify_candidate(
        candidate_id="candidate-2",
        member_id="member-2",
        disqualify_reason_id="reason-2",
        disqualify_note=long_note,
        withdrew=True,
    )

    assert result["success"] is True
    assert captured == {
        "method": "POST",
        "path": "/candidates/candidate-2/disqualify",
        "json": {
            "member_id": "member-2",
            "withdrew": True,
            "disqualify_reason_id": "reason-2",
            "disqualify_note": long_note[:256],
        },
        "params": None,
    }


def test_workable_service_revert_candidate_posts_member_id(monkeypatch):
    captured = {}
    service = WorkableService(access_token="token", subdomain="acme")

    def fake_request(method, path, *, json=None, params=None):
        captured["method"] = method
        captured["path"] = path
        captured["json"] = json
        captured["params"] = params
        return {"ok": True}

    monkeypatch.setattr(service, "_request", fake_request)

    result = service.revert_candidate_disqualification(candidate_id="candidate-3", member_id="member-3")

    assert result["success"] is True
    assert captured == {
        "method": "POST",
        "path": "/candidates/candidate-3/revert",
        "json": {
            "member_id": "member-3",
        },
        "params": None,
    }


def test_render_workable_note_template_supports_single_and_double_braces_and_truncates():
    rendered = render_workable_note_template(
        "Auto reject {pre_screen_score} for {{candidate_name}} " + ("x" * 260),
        pre_screen_score="47.2",
        candidate_name="Taylor Candidate",
    )

    assert rendered.startswith("Auto reject 47.2 for Taylor Candidate")
    assert len(rendered) == 256


def test_disqualify_candidate_in_workable_requires_actor_member():
    result = disqualify_candidate_in_workable(
        org=_org(workable_config={"workable_actor_member_id": ""}),
        app=_app(),
        role=_role(),
    )

    assert result["success"] is False
    assert result["code"] == "missing_actor_member_id"


def test_disqualify_candidate_in_workable_requires_linked_candidate():
    result = disqualify_candidate_in_workable(
        org=_org(),
        app=_app(workable_candidate_id=""),
        role=_role(),
    )

    assert result["success"] is False
    assert result["code"] == "missing_candidate_id"


def test_disqualify_candidate_in_workable_surfaces_api_failure(monkeypatch):
    monkeypatch.setattr(
        WorkableService,
        "disqualify_candidate",
        lambda self, **_: {"success": False, "error": "boom", "response": {"error": "boom"}},
    )

    result = disqualify_candidate_in_workable(
        org=_org(),
        app=_app(),
        role=_role(),
        reason="Below threshold",
    )

    assert result["success"] is False
    assert result["code"] == "api_error"
    assert "boom" in result["message"]


def test_disqualify_candidate_in_workable_returns_success_and_rendered_note(monkeypatch):
    captured = {}

    def fake_disqualify(self, **kwargs):
        captured.update(kwargs)
        return {"success": True, "response": {"ok": True}}

    monkeypatch.setattr(WorkableService, "disqualify_candidate", fake_disqualify)

    result = disqualify_candidate_in_workable(
        org=_org(),
        app=_app(),
        role=_role(),
        reason="Below threshold",
        threshold_100=55,
    )

    assert result["success"] is True
    assert result["note"] == "Auto reject Taylor Candidate at 47.2."
    assert captured == {
        "candidate_id": "candidate-1",
        "member_id": "member-1",
        "disqualify_reason_id": "reason-1",
        "disqualify_note": "Auto reject Taylor Candidate at 47.2.",
        "withdrew": False,
    }


def test_revert_candidate_disqualification_in_workable_uses_member_id(monkeypatch):
    captured = {}

    def fake_revert(self, **kwargs):
        captured.update(kwargs)
        return {"success": True, "response": {"ok": True}}

    monkeypatch.setattr(WorkableService, "revert_candidate_disqualification", fake_revert)

    result = revert_candidate_disqualification_in_workable(
        org=_org(),
        app=_app(),
        role=_role(),
    )

    assert result["success"] is True
    assert captured == {
        "candidate_id": "candidate-1",
        "member_id": "member-1",
    }


def test_move_candidate_in_workable_uses_member_id_and_target_stage(monkeypatch):
    captured = {}

    def fake_move(self, **kwargs):
        captured.update(kwargs)
        return {"success": True, "response": {"ok": True}}

    monkeypatch.setattr(WorkableService, "move_candidate", fake_move)

    result = move_candidate_in_workable(
        org=_org(),
        candidate_id="candidate-4",
        target_stage="Review",
        role=_role(),
    )

    assert result["success"] is True
    assert captured == {
        "candidate_id": "candidate-4",
        "member_id": "member-1",
        "target_stage": "Review",
    }
