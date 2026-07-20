"""Execution-time authority checks for queued automatic role artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.models.organization import Organization
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import JOB_STATUS_CANCELLED, JOB_STATUS_OPEN, Role
from app.models.role_criterion import RoleCriterion
from app.platform.config import settings
from app.platform.database import SessionLocal
from app.cv_parsing.origins import (
    CV_PARSE_ORIGIN_ATS_INGEST,
    CV_PARSE_ORIGIN_NATIVE_APPLY,
    CV_PARSE_ORIGIN_RECRUITER_UPLOAD,
)
from app.tasks.assessment_tasks import sweep_assessment_task_provisioning
from app.tasks.automation_tasks import (
    generate_role_interview_focus,
    parse_application_cv_sections,
    regenerate_role_tech_questions,
)


def _role(db, *, suffix: str, enabled: bool, paused: bool) -> Role:
    org = Organization(name=f"Artifact guard {suffix}", slug=f"artifact-{suffix}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=int(org.id),
        name="Platform Engineer",
        job_spec_text=(
            "Own reliable distributed services, production quality, incident "
            "response, observability, security, and automated delivery."
        ),
        agentic_mode_enabled=enabled,
        agent_paused_at=(datetime.now(timezone.utc) if paused else None),
        agent_paused_reason=("paused by recruiter" if paused else None),
        interview_focus=None,
        tech_questions_signature=None,
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


def _application(db, role: Role, *, suffix: str, source: str) -> CandidateApplication:
    candidate = Candidate(
        organization_id=role.organization_id,
        full_name="Queued Candidate",
        email=f"queued-{suffix}@example.test",
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=role.organization_id,
        role_id=role.id,
        candidate_id=candidate.id,
        source=source,
        cv_text="Senior platform engineer with Python and distributed systems.",
    )
    db.add(app)
    db.commit()
    return app


@pytest.mark.parametrize(
    ("enabled", "paused", "suffix"),
    ((False, False, "focus-off"), (True, True, "focus-paused")),
)
def test_queued_interview_focus_does_not_start_after_role_stops(
    db, *, enabled: bool, paused: bool, suffix: str
):
    role = _role(db, suffix=suffix, enabled=enabled, paused=paused)

    with patch(
        "app.services.interview_focus_service.generate_interview_focus_sync"
    ) as provider:
        result = generate_role_interview_focus.run(
            role.id, requires_running_agent=True
        )

    assert result["status"] == "skipped"
    assert result["reason"] == "role_not_runnable"
    provider.assert_not_called()


@pytest.mark.parametrize(
    ("enabled", "paused", "suffix"),
    ((False, False, "tech-off"), (True, True, "tech-paused")),
)
def test_queued_tech_question_generation_does_not_start_after_role_stops(
    db, *, enabled: bool, paused: bool, suffix: str
):
    role = _role(db, suffix=suffix, enabled=enabled, paused=paused)

    with patch(
        "app.services.role_tech_questions_service.get_or_regenerate"
    ) as provider:
        result = regenerate_role_tech_questions.run(role.id)

    assert result["status"] == "skipped"
    assert result["reason"] == "role_not_runnable"
    provider.assert_not_called()


def test_artifact_recovery_sweep_omits_paused_roles(db):
    role = _role(db, suffix="sweep-paused", enabled=True, paused=True)

    with (
        patch(
            "app.tasks.assessment_tasks.settings.AUTO_GENERATE_ASSESSMENT_TASKS",
            True,
        ),
        patch(
            "app.tasks.automation_tasks.generate_role_interview_focus.delay"
        ) as focus_dispatch,
        patch(
            "app.tasks.automation_tasks.regenerate_role_tech_questions.delay"
        ) as tech_dispatch,
    ):
        summary = sweep_assessment_task_provisioning.run(limit=50)

    assert role.agent_paused_at is not None
    assert summary["interview_focus_due"] == 0
    assert summary["tech_questions_due"] == 0
    focus_dispatch.assert_not_called()
    tech_dispatch.assert_not_called()


def test_workspace_pause_blocks_queued_paid_focus_and_tech_generation(db):
    role = _role(db, suffix="workspace-artifacts", enabled=True, paused=False)
    org = db.query(Organization).filter(Organization.id == role.organization_id).one()
    org.agent_workspace_paused_at = datetime.now(timezone.utc)
    org.agent_workspace_paused_reason = "workspace paused by recruiter"
    db.commit()

    with (
        patch(
            "app.services.interview_focus_service.generate_interview_focus_sync"
        ) as focus_provider,
        patch(
            "app.services.role_tech_questions_service.get_or_regenerate"
        ) as tech_provider,
    ):
        focus = generate_role_interview_focus.run(
            role.id, requires_running_agent=False
        )
        tech = regenerate_role_tech_questions.run(role.id)

    assert focus["status"] == "skipped"
    assert focus["reason"] == "workspace_paused"
    assert tech["status"] == "skipped"
    assert tech["reason"] == "role_not_runnable"
    assert tech["detail"] == "workspace agent is paused"
    focus_provider.assert_not_called()
    tech_provider.assert_not_called()


@pytest.mark.parametrize("changed_input", ("job_spec", "requirements"))
@pytest.mark.parametrize("provider_mode", ("success", "exception"))
def test_interview_focus_discards_output_when_inputs_change_during_provider(
    db,
    monkeypatch: pytest.MonkeyPatch,
    changed_input: str,
    provider_mode: str,
):
    role = _role(
        db,
        suffix=f"focus-edit-{changed_input}-{provider_mode}",
        enabled=True,
        paused=False,
    )
    criterion = RoleCriterion(
        role_id=int(role.id),
        text="Python services",
        must_have=True,
        bucket="must",
        source="recruiter",
        ordering=0,
    )
    db.add(criterion)
    db.commit()
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)

    def edit_inputs(*_args, **_kwargs):
        with SessionLocal() as concurrent:
            current = concurrent.get(Role, int(role.id))
            if changed_input == "job_spec":
                current.job_spec_text = "Replacement specification requiring Rust and consensus."
            else:
                current_criterion = concurrent.get(RoleCriterion, int(criterion.id))
                current_criterion.text = "Rust and distributed consensus"
            concurrent.commit()
        if provider_mode == "exception":
            raise RuntimeError("provider failed after the role edit")
        return {"questions": [{"question": "Stale provider question"}]}

    with patch(
        "app.services.interview_focus_service.generate_interview_focus_sync",
        side_effect=edit_inputs,
    ):
        result = generate_role_interview_focus.run(
            role.id, requires_running_agent=True
        )

    db.expire_all()
    saved = db.get(Role, int(role.id))
    assert result["status"] == "superseded"
    assert result["reason"] == "role_inputs_changed"
    assert saved.interview_focus is None
    assert saved.screening_pack_template is None
    assert saved.tech_interview_pack_template is None
    assert "interview_focus_provisioning" not in (
        saved.assessment_task_provisioning or {}
    )


@pytest.mark.parametrize("pause_scope", ("role", "workspace"))
def test_interview_focus_discards_output_when_paused_during_provider(
    db, monkeypatch: pytest.MonkeyPatch, pause_scope: str
):
    role = _role(db, suffix=f"focus-inflight-{pause_scope}", enabled=True, paused=False)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)

    def pause_authority(*_args, **_kwargs):
        with SessionLocal() as concurrent:
            current = concurrent.get(Role, int(role.id))
            if pause_scope == "role":
                current.agent_paused_at = datetime.now(timezone.utc)
                current.agent_paused_reason = "paused during provider call"
            else:
                organization = concurrent.get(Organization, int(role.organization_id))
                organization.agent_workspace_paused_at = datetime.now(timezone.utc)
                organization.agent_workspace_paused_reason = "paused during provider call"
            concurrent.commit()
        return {"questions": [{"question": "Stale provider question"}]}

    with patch(
        "app.services.interview_focus_service.generate_interview_focus_sync",
        side_effect=pause_authority,
    ):
        result = generate_role_interview_focus.run(
            role.id, requires_running_agent=True
        )

    db.expire_all()
    saved = db.get(Role, int(role.id))
    assert result["status"] == "superseded"
    assert result["reason"] in {"role_not_runnable", "workspace_paused"}
    assert saved.interview_focus is None
    assert "interview_focus_provisioning" not in (
        saved.assessment_task_provisioning or {}
    )


@pytest.mark.parametrize("changed_input", ("job_spec", "requirements"))
@pytest.mark.parametrize("provider_mode", ("success", "exception"))
def test_tech_questions_discard_output_when_inputs_change_during_provider(
    db,
    monkeypatch: pytest.MonkeyPatch,
    changed_input: str,
    provider_mode: str,
):
    role = _role(
        db,
        suffix=f"tech-edit-{changed_input}-{provider_mode}",
        enabled=True,
        paused=False,
    )
    criterion = RoleCriterion(
        role_id=int(role.id),
        text="Python services",
        must_have=True,
        bucket="must",
        source="recruiter",
        ordering=0,
    )
    role.tech_questions_cached = [{"question": "Previous current question"}]
    role.tech_questions_signature = "stale"
    db.add(criterion)
    db.commit()
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)

    def edit_inputs(*_args, **_kwargs):
        with SessionLocal() as concurrent:
            current = concurrent.get(Role, int(role.id))
            current.tech_questions_signature = None
            if changed_input == "job_spec":
                current.job_spec_text = "Replacement specification requiring Rust and consensus."
            else:
                current_criterion = concurrent.get(RoleCriterion, int(criterion.id))
                current_criterion.text = "Rust and distributed consensus"
            concurrent.commit()
        if provider_mode == "exception":
            raise RuntimeError("provider failed after the role edit")
        return [{"question": "Stale generated question"}]

    with patch(
        "app.services.role_tech_questions_service.generate_tech_questions",
        side_effect=edit_inputs,
    ):
        result = regenerate_role_tech_questions.run(role.id)

    db.expire_all()
    saved = db.get(Role, int(role.id))
    assert result["status"] == "superseded"
    assert result["reason"] == "role_inputs_changed"
    assert saved.tech_questions_cached == [{"question": "Previous current question"}]
    assert saved.tech_questions_signature is None
    assert "tech_questions_provisioning" not in (
        saved.assessment_task_provisioning or {}
    )


@pytest.mark.parametrize("pause_scope", ("role", "workspace"))
def test_tech_questions_discard_output_when_paused_during_provider(
    db, monkeypatch: pytest.MonkeyPatch, pause_scope: str
):
    role = _role(db, suffix=f"tech-inflight-{pause_scope}", enabled=True, paused=False)
    role.tech_questions_cached = [{"question": "Previous current question"}]
    role.tech_questions_signature = "stale"
    db.commit()
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)

    def pause_authority(*_args, **_kwargs):
        with SessionLocal() as concurrent:
            current = concurrent.get(Role, int(role.id))
            if pause_scope == "role":
                current.agent_paused_at = datetime.now(timezone.utc)
                current.agent_paused_reason = "paused during provider call"
            else:
                organization = concurrent.get(Organization, int(role.organization_id))
                organization.agent_workspace_paused_at = datetime.now(timezone.utc)
                organization.agent_workspace_paused_reason = "paused during provider call"
            concurrent.commit()
        return [{"question": "Stale generated question"}]

    with patch(
        "app.services.role_tech_questions_service.generate_tech_questions",
        side_effect=pause_authority,
    ):
        result = regenerate_role_tech_questions.run(role.id)

    db.expire_all()
    saved = db.get(Role, int(role.id))
    assert result["status"] == "superseded"
    assert result["reason"] in {"role_not_runnable", "workspace_paused"}
    assert saved.tech_questions_cached == [{"question": "Previous current question"}]
    assert saved.tech_questions_signature == "stale"
    assert "tech_questions_provisioning" not in (
        saved.assessment_task_provisioning or {}
    )


@pytest.mark.parametrize(
    ("origin", "source", "enabled", "paused", "suffix"),
    (
        (CV_PARSE_ORIGIN_ATS_INGEST, "workable", False, False, "ats-off"),
        (CV_PARSE_ORIGIN_ATS_INGEST, "workable", True, True, "ats-paused"),
        (CV_PARSE_ORIGIN_NATIVE_APPLY, "careers", False, False, "native-off"),
        (CV_PARSE_ORIGIN_NATIVE_APPLY, "careers", True, True, "native-paused"),
    ),
)
def test_queued_autonomous_cv_parse_does_not_start_after_role_stops(
    db,
    *,
    origin: str,
    source: str,
    enabled: bool,
    paused: bool,
    suffix: str,
):
    role = _role(db, suffix=suffix, enabled=enabled, paused=paused)
    app = _application(db, role, suffix=suffix, source=source)

    with patch("app.cv_parsing.apply.parse_and_store_cv_sections") as provider:
        result = parse_application_cv_sections.run(app.id, origin=origin)

    assert result["status"] == "skipped"
    assert result["reason"] == "role_not_runnable"
    provider.assert_not_called()


@pytest.mark.parametrize("terminal_kind", ("local", "workable", "bullhorn"))
def test_queued_autonomous_cv_parse_obeys_provider_neutral_job_lifecycle(
    db, terminal_kind: str
):
    suffix = f"terminal-{terminal_kind}"
    role = _role(db, suffix=suffix, enabled=True, paused=False)
    role.job_status = JOB_STATUS_OPEN
    if terminal_kind == "local":
        role.job_status = JOB_STATUS_CANCELLED
    elif terminal_kind == "workable":
        role.source = "workable"
        role.workable_job_id = f"WORK-{role.id}"
        role.workable_job_data = {"state": "closed"}
    else:
        role.source = "bullhorn"
        role.bullhorn_job_order_id = str(90_000 + int(role.id))
        role.bullhorn_job_data = {"status": "Closed", "isOpen": False}
    db.commit()
    app = _application(db, role, suffix=suffix, source=role.source)

    with patch("app.cv_parsing.apply.parse_and_store_cv_sections") as provider:
        result = parse_application_cv_sections.run(
            app.id, origin=CV_PARSE_ORIGIN_ATS_INGEST
        )

    assert result["status"] == "skipped"
    assert result["reason"] == "role_not_runnable"
    assert "not open" in result["detail"] or "not live" in result["detail"]
    provider.assert_not_called()


def test_explicit_recruiter_cv_parse_runs_while_agent_is_off(db):
    role = _role(db, suffix="upload-off", enabled=False, paused=False)
    app = _application(db, role, suffix="upload-off", source="workable")

    with patch(
        "app.cv_parsing.apply.parse_and_store_cv_sections", return_value=True
    ) as provider:
        result = parse_application_cv_sections.run(
            app.id, origin=CV_PARSE_ORIGIN_RECRUITER_UPLOAD
        )

    assert result["status"] == "ok"
    provider.assert_called_once()


def test_legacy_cv_parse_without_origin_fails_closed(db):
    role = _role(db, suffix="legacy", enabled=True, paused=False)
    app = _application(db, role, suffix="legacy", source="careers")

    with patch("app.cv_parsing.apply.parse_and_store_cv_sections") as provider:
        result = parse_application_cv_sections.run(app.id)

    assert result["status"] == "skipped"
    assert result["reason"] == "unknown_origin"
    provider.assert_not_called()
