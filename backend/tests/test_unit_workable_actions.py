"""Unit tests for Workable client payloads and outbound action helpers."""

import os
from types import SimpleNamespace

import pytest

os.environ["DATABASE_URL"] = "sqlite:///./test.db"

from app.components.integrations.workable.service import WorkableService
from app.services.workable_actions_service import (
    build_workable_reject_note,
    disqualify_candidate_in_workable,
    move_candidate_in_workable,
    render_workable_note_template,
    revert_candidate_disqualification_in_workable,
)


def _org(**overrides):
    config = {
        "workable_writeback": True,
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
    # Per-role overrides for ``workable_disqualify_reason_id`` and
    # ``auto_reject_note_template`` were dropped in alembic 076 — they
    # now live only on ``org.workable_config``. ``workable_actor_member_id``
    # keeps its per-role override.
    payload = {
        "id": 11,
        "name": "Backend Engineer",
        "workable_actor_member_id": None,
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


def test_workable_service_blocks_assessment_lifecycle_comment(monkeypatch):
    service = WorkableService(access_token="token", subdomain="acme")
    requests = []
    monkeypatch.setattr(
        service,
        "_request",
        lambda *args, **kwargs: requests.append((args, kwargs)),
    )

    result = service.post_candidate_comment(
        candidate_id="candidate-3",
        member_id="member-3",
        body="Assessment completed with a score of 42/100.",
    )

    assert result["success"] is False
    assert result["error"] == "assessment_lifecycle_content_blocked"
    assert requests == []


@pytest.mark.parametrize(
    "body",
    [
        "Technical test passed.",
        "Candidate evaluation was positive.",
        "Coding exercise result: strong.",
        "Grade: 8/10.",
        "See report at https://taali.ai/reports/123.",
        "Details: https://taali.ai/assessment/123.",
        "Details: https://taali.ai/share/abc.",
    ],
)
def test_workable_service_blocks_assessment_bypass_vocabulary_and_urls(
    monkeypatch, body
):
    service = WorkableService(access_token="token", subdomain="acme")
    requests = []
    monkeypatch.setattr(
        service,
        "_request",
        lambda *args, **kwargs: requests.append((args, kwargs)),
    )

    result = service.post_candidate_comment(
        candidate_id="candidate-3",
        member_id="member-3",
        body=body,
    )

    assert result["success"] is False
    assert result["error"] == "assessment_lifecycle_content_blocked"
    assert requests == []


def test_workable_service_allows_assessment_word_inside_structured_role_name(
    monkeypatch,
):
    service = WorkableService(access_token="token", subdomain="acme")
    requests = []
    monkeypatch.setattr(
        service,
        "_request",
        lambda *args, **kwargs: requests.append((args, kwargs)) or {"ok": True},
    )

    result = service.post_candidate_comment(
        candidate_id="candidate-3",
        member_id="member-3",
        body=(
            "TAALI · Candidate advanced\n"
            "Role: Assessment Engineer\n"
            "Reason: The candidate was approved for progression."
        ),
        trusted_role_values=("Assessment Engineer",),
    )

    assert result["success"] is True
    assert len(requests) == 1


def test_workable_service_allows_owned_canonical_movement_score_labels(monkeypatch):
    service = WorkableService(access_token="token", subdomain="acme")
    requests = []
    monkeypatch.setattr(
        service,
        "_request",
        lambda *args, **kwargs: requests.append((args, kwargs)) or {"ok": True},
    )

    result = service.post_candidate_comment(
        candidate_id="candidate-3",
        member_id="member-3",
        body=(
            "TAALI · Candidate advanced for a related role\n"
            "Role: Assessment Engineer\n"
            "TAALI score: 72/100\n"
            "Related-role score used: 72/100\n"
            "Pre-screen score used: 70/100\n"
            "Role threshold: 56/100\n"
            "Original application score: 63/100\n"
            "Decision source: Recruiter\n"
            "Reason: The candidate met the related-role threshold and was approved "
            "for progression."
        ),
        trusted_role_values=("Assessment Engineer",),
    )

    assert result["success"] is True
    assert len(requests) == 1


def test_workable_service_does_not_trust_arbitrary_assessment_copy_on_role_line(
    monkeypatch,
):
    service = WorkableService(access_token="token", subdomain="acme")
    requests = []
    monkeypatch.setattr(
        service,
        "_request",
        lambda *args, **kwargs: requests.append((args, kwargs)),
    )

    result = service.post_candidate_comment(
        candidate_id="candidate-3",
        member_id="member-3",
        body="Role: Assessment complete — score 91/100",
        trusted_role_values=("Assessment Engineer",),
    )

    assert result["success"] is False
    assert result["error"] == "assessment_lifecycle_content_blocked"
    assert requests == []


def test_render_workable_note_template_supports_single_and_double_braces_and_truncates():
    rendered = render_workable_note_template(
        "Auto reject {pre_screen_score} for {{candidate_name}} " + ("x" * 260),
        pre_screen_score="47.2",
        candidate_name="Taylor Candidate",
    )

    assert rendered.startswith("Auto reject 47.2 for Taylor Candidate")
    assert len(rendered) == 256
    assert rendered.endswith("…")


def test_build_workable_reject_note_supports_both_threshold_placeholders():
    rendered = build_workable_reject_note(
        app=_app(),
        role=_role(),
        template="Threshold {threshold}; legacy {{threshold_100}}.",
        threshold_100=55,
    )

    assert rendered == "Threshold 55.0; legacy 55.0."


def test_build_workable_reject_note_uses_canonical_automatic_copy_without_template():
    rendered = build_workable_reject_note(
        app=_app(),
        role=_role(),
        template=None,
        reason="Internal pre-screen reason",
        threshold_100=55,
    )

    assert rendered == (
        "TAALI · Candidate rejected automatically\n\n"
        "Pre-screen score: 47.2/100\n"
        "Role threshold: 55.0/100\n"
        "Reason: The candidate did not meet the configured threshold."
    )


def test_disqualify_candidate_in_workable_requires_actor_member():
    result = disqualify_candidate_in_workable(
        org=_org(workable_config={"workable_actor_member_id": ""}),
        app=_app(),
        role=_role(),
    )

    assert result["success"] is False
    assert result["code"] == "missing_actor_member_id"


def test_disqualify_candidate_in_workable_skips_already_disqualified_app(
    monkeypatch,
):
    requests = []
    monkeypatch.setattr(
        WorkableService,
        "disqualify_candidate",
        lambda self, **kwargs: requests.append(kwargs),
    )

    result = disqualify_candidate_in_workable(
        org=_org(),
        app=_app(workable_disqualified=True),
        role=_role(),
        reason="Rejected in Taali following recruiter review.",
    )

    assert result["success"] is True
    assert result["skipped"] is True
    assert result["code"] == "already_disqualified"
    assert result["config"]["movement_performed"] is False
    assert requests == []


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


def test_disqualify_candidate_in_workable_does_not_apply_org_template_implicitly(monkeypatch):
    captured = {}

    def fake_disqualify(self, **kwargs):
        captured.update(kwargs)
        return {"success": True, "response": {"ok": True}}

    monkeypatch.setattr(WorkableService, "disqualify_candidate", fake_disqualify)

    result = disqualify_candidate_in_workable(
        org=_org(),
        app=_app(),
        role=_role(),
        reason="Rejected in Taali following recruiter review.",
    )

    assert result["success"] is True
    assert result["note"] == (
        "TAALI · Candidate rejected\n"
        "Role: Backend Engineer\n"
        "Reason: The candidate was rejected in Taali."
    )
    assert captured == {
        "candidate_id": "candidate-1",
        "member_id": "member-1",
        "disqualify_reason_id": "reason-1",
        "disqualify_note": (
            "TAALI · Candidate rejected\n"
            "Role: Backend Engineer\n"
            "Reason: The candidate was rejected in Taali."
        ),
        "withdrew": False,
    }


def test_disqualify_candidate_in_workable_omits_note_when_reason_is_none(monkeypatch):
    captured = {}

    def fake_disqualify(self, **kwargs):
        captured.update(kwargs)
        return {"success": True, "response": {"ok": True}}

    monkeypatch.setattr(WorkableService, "disqualify_candidate", fake_disqualify)

    result = disqualify_candidate_in_workable(
        org=_org(),
        app=_app(),
        role=_role(),
        reason=None,
    )

    assert result["success"] is True
    assert "note" not in result
    assert captured["disqualify_note"] is None


def test_disqualify_candidate_in_workable_applies_explicit_auto_reject_template(monkeypatch):
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
        note_template="Auto reject {{candidate_name}} below {threshold}.",
        threshold_100=55,
    )

    assert result["success"] is True
    assert result["note"] == "Auto reject Taylor Candidate below 55.0."
    assert captured["disqualify_note"] == "Auto reject Taylor Candidate below 55.0."


def test_disqualify_blocks_assessment_copy_hidden_in_template_role_line(monkeypatch):
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
        note_template="Role: Assessment complete — score 91/100",
        threshold_100=55,
    )

    assert result["success"] is True
    assert "note" not in result
    assert captured["disqualify_note"] is None


def test_disqualify_replaces_assessment_reason_with_fixed_movement_copy(monkeypatch):
    captured = {}

    def fake_disqualify(self, **kwargs):
        captured.update(kwargs)
        return {"success": True, "response": {"ok": True}}

    monkeypatch.setattr(WorkableService, "disqualify_candidate", fake_disqualify)

    result = disqualify_candidate_in_workable(
        org=_org(),
        app=_app(),
        role=_role(),
        reason="Assessment completed with a score of 42/100.",
    )

    assert result["success"] is True
    assert "Assessment" not in result["note"]
    assert "42/100" not in result["note"]
    assert result["note"] == (
        "TAALI · Candidate rejected\n"
        "Role: Backend Engineer\n"
        "Reason: The candidate was rejected in Taali."
    )
    assert captured["disqualify_note"] == result["note"]


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
