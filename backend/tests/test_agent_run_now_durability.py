"""Durable, scoped HTTP contracts for recruiter-triggered agent runs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models.agent_run import AGENT_RUN_DISPATCHING, AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from tests.conftest import auth_headers


def _user_and_org(db, email: str) -> tuple[User, int]:
    user = db.query(User).filter(User.email == email).one()
    return user, int(user.organization_id)


def _role(db, organization_id: int, name: str) -> Role:
    role = Role(
        organization_id=organization_id,
        name=name,
        source="manual",
        agentic_mode_enabled=True,
        job_spec_text="Hire a reliable platform engineer with production ownership.",
    )
    db.add(role)
    db.flush()
    return role


def _application(
    db,
    *,
    organization_id: int,
    role: Role,
    suffix: str,
) -> CandidateApplication:
    candidate = Candidate(
        organization_id=organization_id,
        full_name=f"Candidate {suffix}",
        email=f"run-now-{suffix}@example.test",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=organization_id,
        role_id=int(role.id),
        candidate_id=int(candidate.id),
        source="manual",
        application_outcome="open",
        pipeline_stage="review",
    )
    db.add(application)
    db.flush()
    return application


def _related_role_application(
    db,
    *,
    organization_id: int,
    suffix: str = "related",
) -> tuple[Role, CandidateApplication, SisterRoleEvaluation]:
    source = _role(db, organization_id, f"Related source {suffix}")
    application = _application(
        db,
        organization_id=organization_id,
        role=source,
        suffix=suffix,
    )
    related = Role(
        organization_id=organization_id,
        name=f"Related durable run {suffix}",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(source.id),
        agentic_mode_enabled=True,
        job_spec_text="A related platform role with independent scoring and workflow ownership.",
    )
    db.add(related)
    db.flush()
    evaluation = SisterRoleEvaluation(
        organization_id=organization_id,
        role_id=int(related.id),
        source_application_id=int(application.id),
        status="pending",
        spec_fingerprint=f"related-manual-run-{suffix}",
    )
    db.add(evaluation)
    db.flush()
    return related, application, evaluation


def test_run_now_broker_failure_persists_one_recoverable_intent(client, db):
    headers, email = auth_headers(client)
    _user, organization_id = _user_and_org(db, email)
    role = _role(db, organization_id, "Broker recovery")
    db.commit()
    request_headers = {**headers, "Idempotency-Key": "run-now-broker-recovery"}

    with patch(
        "app.tasks.agent_tasks.agent_manual_run.delay",
        side_effect=RuntimeError("broker unavailable"),
    ):
        response = client.post(
            f"/api/v1/roles/{role.id}/agent/run-now",
            json={},
            headers=request_headers,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "dispatch_pending"
    assert body["queued"] is False
    assert body["broker_accepted"] is False
    assert body["dispatch_pending"] is True
    assert body["intent_persisted"] is True
    assert body["replayed"] is False
    assert body["task_id"] is None
    assert body["idempotency_key"] == "run-now-broker-recovery"

    db.expire_all()
    intent = db.get(AgentRun, int(body["agent_run_id"]))
    assert intent is not None
    assert intent.status == AGENT_RUN_DISPATCHING
    assert int(intent.organization_id) == organization_id
    assert int(intent.role_id) == int(role.id)

    # An immediate HTTP retry replays the intent but cannot know whether the
    # earlier ambiguous publish reached the broker, so it must not say queued.
    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        replay = client.post(
            f"/api/v1/roles/{role.id}/agent/run-now",
            json={},
            headers=request_headers,
        )
    assert replay.status_code == 200, replay.text
    replay_body = replay.json()
    assert replay_body["agent_run_id"] == body["agent_run_id"]
    assert replay_body["queued"] is False
    assert replay_body["broker_accepted"] is None
    assert replay_body["dispatch_pending"] is True
    assert replay_body["replayed"] is True
    delay.assert_not_called()

    snapshot = dict(intent.agent_state_snapshot or {})
    snapshot["dispatch_next_attempt_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    intent.agent_state_snapshot = snapshot
    db.commit()

    from app.tasks.agent_tasks import recover_dispatching_manual_agent_runs

    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        recovered = recover_dispatching_manual_agent_runs.run(limit=10)
    assert recovered == {"scanned": 1, "kicked": 1, "publish_failed": 0}
    delay.assert_called_once_with(
        role_id=int(role.id),
        application_id=None,
        dispatch_key=str(intent.dispatch_key),
        organization_id=organization_id,
    )

    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        bounded = recover_dispatching_manual_agent_runs.run(limit=10)
    assert bounded["kicked"] == 0
    delay.assert_not_called()


def test_related_role_run_now_recovers_through_the_scoring_worker(client, db):
    headers, email = auth_headers(client)
    _user, organization_id = _user_and_org(db, email)
    role, application, evaluation = _related_role_application(
        db,
        organization_id=organization_id,
    )
    owner_role = db.get(Role, int(role.ats_owner_role_id))
    other_application = _application(
        db,
        organization_id=organization_id,
        role=owner_role,
        suffix="related-other",
    )
    db.add(
        SisterRoleEvaluation(
            organization_id=organization_id,
            role_id=int(role.id),
            source_application_id=int(other_application.id),
            status="pending",
            spec_fingerprint="related-manual-run-other",
        )
    )
    unrelated_role = _role(db, organization_id, "Related unrelated role")
    unrelated_application = _application(
        db,
        organization_id=organization_id,
        role=unrelated_role,
        suffix="related-unrelated",
    )
    db.commit()

    with patch("app.tasks.sister_role_tasks.score_sister_role.apply_async") as dispatch:
        concealed = client.post(
            f"/api/v1/roles/{role.id}/agent/run-now",
            json={"application_id": unrelated_application.id},
            headers={**headers, "Idempotency-Key": "related-run-concealed"},
        )
    assert concealed.status_code == 404, concealed.text
    assert concealed.json() == {"detail": "application not found for this role"}
    dispatch.assert_not_called()

    request_headers = {**headers, "Idempotency-Key": "related-run-recovery"}

    with patch(
        "app.tasks.sister_role_tasks.score_sister_role.apply_async",
        side_effect=RuntimeError("broker unavailable"),
    ):
        response = client.post(
            f"/api/v1/roles/{role.id}/agent/run-now",
            json={"application_id": application.id},
            headers=request_headers,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["application_id"] == int(application.id)
    assert body["status"] == "dispatch_pending"
    assert body["intent_persisted"] is True
    intent = db.get(AgentRun, int(body["agent_run_id"]))
    assert intent is not None

    snapshot = dict(intent.agent_state_snapshot or {})
    snapshot["dispatch_next_attempt_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    intent.agent_state_snapshot = snapshot
    db.commit()

    from app.tasks.agent_tasks import recover_dispatching_manual_agent_runs

    with patch(
        "app.tasks.sister_role_tasks.score_sister_role.apply_async",
        return_value=SimpleNamespace(id="related-recovered"),
    ) as dispatch:
        recovered = recover_dispatching_manual_agent_runs.run(limit=10)
    assert recovered == {"scanned": 1, "kicked": 1, "publish_failed": 0}
    dispatch.assert_called_once_with(
        args=[int(role.id)],
        kwargs={
            "dispatch_key": str(intent.dispatch_key),
            "organization_id": organization_id,
            "application_id": int(application.id),
        },
        queue="scoring",
    )

    from app.tasks.sister_role_tasks import score_sister_role

    with patch(
        "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
    ) as score_evaluation:
        result = score_sister_role.run(
            int(role.id),
            dispatch_key=str(intent.dispatch_key),
            application_id=int(application.id),
        )
    assert result["queued"] == 1
    score_evaluation.assert_called_once_with(
        args=[int(evaluation.id)],
        queue="scoring",
    )
    db.expire_all()
    assert db.get(AgentRun, int(intent.id)).status == "succeeded"


def test_orchestrator_workspace_pause_race_terminalizes_dispatch_intent(client, db):
    headers, email = auth_headers(client)
    _user, organization_id = _user_and_org(db, email)
    role = _role(db, organization_id, "Workspace pause race")

    from app.services.manual_agent_run_dispatch import ensure_manual_run_intent

    intent = ensure_manual_run_intent(
        db,
        role=role,
        application_id=None,
        dispatch_key="workspace-pause-race",
    ).run
    db.commit()

    organization = db.get(Organization, organization_id)
    assert organization is not None
    organization.agent_workspace_paused_at = datetime.now(timezone.utc)
    organization.agent_workspace_paused_reason = "paused after broker admission"
    db.commit()

    from app.agent_runtime.orchestrator import run_cycle

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org",
        side_effect=AssertionError("paused cycle must not resolve a paid client"),
    ):
        run = run_cycle(
            db,
            role=role,
            trigger="manual",
            dispatch_key=str(intent.dispatch_key),
        )
    db.commit()

    db.expire_all()
    persisted = db.get(AgentRun, int(intent.id))
    assert persisted is not None
    assert int(run.id) == int(intent.id)
    assert persisted.status == "aborted"
    assert persisted.error == "workspace_paused_before_cycle"
    assert persisted.finished_at is not None
    assert db.query(AgentRun).filter_by(dispatch_key="workspace-pause-race").count() == 1
    assert db.query(AgentRun).filter_by(status=AGENT_RUN_DISPATCHING).count() == 0


@pytest.mark.parametrize("revocation", ("closed", "corrupt_candidate_org"))
def test_native_manual_worker_revalidates_focused_application_before_cycle(
    db,
    revocation,
):
    organization = Organization(
        name=f"Native manual focus {revocation}",
        slug=f"native-manual-focus-{revocation}-{id(db)}",
    )
    db.add(organization)
    db.flush()
    role = _role(db, int(organization.id), f"Native focus {revocation}")
    application = _application(
        db,
        organization_id=int(organization.id),
        role=role,
        suffix=f"native-focus-{revocation}",
    )

    from app.services.manual_agent_run_dispatch import ensure_manual_run_intent

    intent = ensure_manual_run_intent(
        db,
        role=role,
        application_id=int(application.id),
        dispatch_key=f"native-focus-{revocation}",
    ).run
    if revocation == "closed":
        application.application_outcome = "withdrawn"
    else:
        foreign = Organization(
            name="Native focus foreign",
            slug=f"native-focus-foreign-{id(db)}",
        )
        db.add(foreign)
        db.flush()
        candidate = db.get(Candidate, int(application.candidate_id))
        assert candidate is not None
        candidate.organization_id = int(foreign.id)
    db.commit()

    from app.tasks.agent_tasks import agent_manual_run

    with patch("app.agent_runtime.orchestrator.run_cycle") as run_cycle:
        result = agent_manual_run.run(
            role_id=int(role.id),
            application_id=int(application.id),
            dispatch_key=str(intent.dispatch_key),
            organization_id=int(organization.id),
        )

    assert result == {
        "status": "skipped",
        "reason": "application_unavailable",
        "role_id": int(role.id),
        "application_id": int(application.id),
    }
    run_cycle.assert_not_called()
    db.expire_all()
    persisted = db.get(AgentRun, int(intent.id))
    assert persisted is not None
    assert persisted.status == "aborted"
    assert persisted.error == "application_unavailable"


@pytest.mark.parametrize("delivered_application", ("other", "missing"))
def test_related_manual_worker_rejects_application_swapped_dispatch_payload(
    db,
    delivered_application,
):
    organization = Organization(
        name=f"Related dispatch scope {delivered_application}",
        slug=f"related-dispatch-scope-{delivered_application}-{id(db)}",
    )
    db.add(organization)
    db.flush()
    role, application_a, _evaluation_a = _related_role_application(
        db,
        organization_id=int(organization.id),
        suffix=f"scope-a-{delivered_application}",
    )
    owner_role = db.get(Role, int(role.ats_owner_role_id))
    assert owner_role is not None
    application_b = _application(
        db,
        organization_id=int(organization.id),
        role=owner_role,
        suffix=f"scope-b-{delivered_application}",
    )
    db.add(
        SisterRoleEvaluation(
            organization_id=int(organization.id),
            role_id=int(role.id),
            source_application_id=int(application_b.id),
            status="pending",
            spec_fingerprint=f"scope-b-{delivered_application}",
        )
    )

    from app.services.manual_agent_run_dispatch import ensure_manual_run_intent

    intent = ensure_manual_run_intent(
        db,
        role=role,
        application_id=int(application_a.id),
        dispatch_key=f"related-scope-{delivered_application}",
    ).run
    db.commit()

    from app.tasks.sister_role_tasks import score_sister_role

    delivered_id = (
        int(application_b.id) if delivered_application == "other" else None
    )
    with patch(
        "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
    ) as score_evaluation:
        result = score_sister_role.run(
            int(role.id),
            dispatch_key=str(intent.dispatch_key),
            application_id=delivered_id,
            organization_id=int(organization.id),
        )

    assert result == {
        "status": "skipped",
        "reason": "dispatch_scope_mismatch",
        "role_id": int(role.id),
    }
    score_evaluation.assert_not_called()
    db.expire_all()
    persisted = db.get(AgentRun, int(intent.id))
    assert persisted is not None
    assert persisted.status == AGENT_RUN_DISPATCHING
    assert persisted.finished_at is None


def test_related_manual_worker_aborts_when_focused_application_is_revoked(db):
    organization = Organization(
        name="Related focused application revoked",
        slug=f"related-focused-revoked-{id(db)}",
    )
    db.add(organization)
    db.flush()
    role, application, _evaluation = _related_role_application(
        db,
        organization_id=int(organization.id),
        suffix="focused-revoked",
    )

    from app.services.manual_agent_run_dispatch import ensure_manual_run_intent

    intent = ensure_manual_run_intent(
        db,
        role=role,
        application_id=int(application.id),
        dispatch_key="related-focused-revoked",
    ).run
    application.workable_disqualified = True
    db.commit()

    from app.tasks.sister_role_tasks import score_sister_role

    with patch(
        "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
    ) as score_evaluation:
        result = score_sister_role.run(
            int(role.id),
            dispatch_key=str(intent.dispatch_key),
            application_id=int(application.id),
            organization_id=int(organization.id),
        )

    assert result["status"] == "skipped"
    assert result["reason"] == "application_unavailable"
    score_evaluation.assert_not_called()
    db.expire_all()
    persisted = db.get(AgentRun, int(intent.id))
    assert persisted is not None
    assert persisted.status == "aborted"
    assert persisted.error == "application_unavailable"


def test_manual_run_terminal_update_requires_organization_and_application(db):
    organization = Organization(
        name="Manual terminal scope",
        slug=f"manual-terminal-scope-{id(db)}",
    )
    foreign = Organization(
        name="Manual terminal scope foreign",
        slug=f"manual-terminal-scope-foreign-{id(db)}",
    )
    db.add_all((organization, foreign))
    db.flush()
    role = _role(db, int(organization.id), "Manual terminal scope")

    from app.services.manual_agent_run_dispatch import (
        ensure_manual_run_intent,
        finish_manual_run_intent,
    )

    intent = ensure_manual_run_intent(
        db,
        role=role,
        application_id=401,
        dispatch_key="manual-terminal-scope",
    ).run
    assert finish_manual_run_intent(
        db,
        dispatch_key=str(intent.dispatch_key),
        organization_id=int(foreign.id),
        role_id=int(role.id),
        application_id=401,
        status="aborted",
    ) is False
    assert finish_manual_run_intent(
        db,
        dispatch_key=str(intent.dispatch_key),
        organization_id=int(organization.id),
        role_id=int(role.id),
        application_id=402,
        status="aborted",
    ) is False
    assert intent.status == AGENT_RUN_DISPATCHING
    assert intent.finished_at is None


@pytest.mark.parametrize("altered_field", ("role", "organization", "trigger", "application"))
def test_orchestrator_validates_terminal_replay_scope_before_returning(
    db,
    altered_field,
):
    organization = Organization(
        name=f"Terminal replay {altered_field}",
        slug=f"terminal-replay-{altered_field}-{id(db)}",
    )
    foreign = Organization(
        name=f"Terminal replay foreign {altered_field}",
        slug=f"terminal-replay-foreign-{altered_field}-{id(db)}",
    )
    db.add_all((organization, foreign))
    db.flush()
    role = _role(db, int(organization.id), f"Terminal replay {altered_field}")
    replay_role = role
    if altered_field == "role":
        replay_role = _role(db, int(organization.id), "Terminal replay other role")

    from app.services.manual_agent_run_dispatch import ensure_manual_run_intent

    intent = ensure_manual_run_intent(
        db,
        role=role,
        application_id=101,
        dispatch_key=f"terminal-replay-{altered_field}",
    ).run
    intent.status = "succeeded"
    intent.finished_at = datetime.now(timezone.utc)
    if altered_field == "organization":
        intent.organization_id = int(foreign.id)
    db.commit()

    from app.agent_runtime.orchestrator import run_cycle

    with pytest.raises(ValueError, match="dispatch scope mismatch"):
        run_cycle(
            db,
            role=replay_role,
            trigger="cron" if altered_field == "trigger" else "manual",
            application_id=102 if altered_field == "application" else 101,
            dispatch_key=str(intent.dispatch_key),
        )


@pytest.mark.parametrize(
    ("blocked_state", "expected_reason"),
    (
        ("disabled", "agent_disabled"),
        ("paused", "agent_paused"),
        ("workspace_paused", "workspace_paused"),
        ("deleted", "role_not_found"),
    ),
)
def test_related_manual_worker_and_recovery_abort_revoked_intents(
    db,
    blocked_state,
    expected_reason,
):
    organization = Organization(
        name=f"Related manual guard {blocked_state}",
        slug=f"related-manual-guard-{blocked_state}-{id(db)}",
    )
    db.add(organization)
    db.flush()
    worker_role, worker_application, _worker_evaluation = _related_role_application(
        db,
        organization_id=int(organization.id),
        suffix=f"worker-{blocked_state}",
    )
    recovery_role, recovery_application, _recovery_evaluation = (
        _related_role_application(
            db,
            organization_id=int(organization.id),
            suffix=f"recovery-{blocked_state}",
        )
    )

    from app.services.manual_agent_run_dispatch import ensure_manual_run_intent

    worker_intent = ensure_manual_run_intent(
        db,
        role=worker_role,
        application_id=int(worker_application.id),
        dispatch_key=f"related-worker-{blocked_state}",
    ).run
    recovery_intent = ensure_manual_run_intent(
        db,
        role=recovery_role,
        application_id=int(recovery_application.id),
        dispatch_key=f"related-recovery-{blocked_state}",
    ).run

    if blocked_state == "disabled":
        worker_role.agentic_mode_enabled = False
        recovery_role.agentic_mode_enabled = False
    elif blocked_state == "paused":
        worker_role.agent_paused_at = datetime.now(timezone.utc)
        recovery_role.agent_paused_at = datetime.now(timezone.utc)
    elif blocked_state == "workspace_paused":
        organization.agent_workspace_paused_at = datetime.now(timezone.utc)
    elif blocked_state == "deleted":
        worker_role.deleted_at = datetime.now(timezone.utc)
        recovery_role.deleted_at = datetime.now(timezone.utc)
    db.commit()

    from app.tasks.agent_tasks import recover_dispatching_manual_agent_runs
    from app.tasks.sister_role_tasks import score_sister_role

    with patch(
        "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
    ) as score_evaluation:
        worker_result = score_sister_role.run(
            int(worker_role.id),
            dispatch_key=str(worker_intent.dispatch_key),
            application_id=int(worker_application.id),
        )
    assert worker_result == {
        "status": "skipped",
        "reason": expected_reason,
        "role_id": int(worker_role.id),
    }
    score_evaluation.assert_not_called()

    with patch("app.tasks.sister_role_tasks.score_sister_role.apply_async") as dispatch:
        recovery_result = recover_dispatching_manual_agent_runs.run(limit=10)
    assert recovery_result == {"scanned": 1, "kicked": 0, "publish_failed": 0}
    dispatch.assert_not_called()

    db.expire_all()
    for intent in (worker_intent, recovery_intent):
        persisted = db.get(AgentRun, int(intent.id))
        assert persisted is not None
        assert persisted.status == "aborted"
        assert persisted.error == expected_reason
        assert persisted.finished_at is not None


def test_related_manual_worker_fails_closed_for_missing_role(db):
    from app.tasks.sister_role_tasks import score_sister_role

    with patch(
        "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
    ) as score_evaluation:
        result = score_sister_role.run(
            999_999_999,
            dispatch_key="missing-related-role",
            application_id=123,
        )

    assert result == {
        "status": "skipped",
        "reason": "role_not_found",
        "role_id": 999_999_999,
    }
    score_evaluation.assert_not_called()


def test_run_now_idempotency_key_is_bound_to_role_and_application_scope(client, db):
    headers, email = auth_headers(client)
    _user, organization_id = _user_and_org(db, email)
    first_role = _role(db, organization_id, "First scope")
    second_role = _role(db, organization_id, "Second scope")
    first_app = _application(
        db,
        organization_id=organization_id,
        role=first_role,
        suffix="first",
    )
    second_app = _application(
        db,
        organization_id=organization_id,
        role=second_role,
        suffix="second",
    )
    db.commit()
    request_headers = {**headers, "Idempotency-Key": "fixed-run-scope"}

    with patch(
        "app.tasks.agent_tasks.agent_manual_run.delay",
        return_value=SimpleNamespace(id="run-now-task"),
    ) as delay:
        accepted = client.post(
            f"/api/v1/roles/{first_role.id}/agent/run-now",
            json={"application_id": first_app.id},
            headers=request_headers,
        )
    assert accepted.status_code == 200, accepted.text
    accepted_body = accepted.json()
    assert accepted_body["queued"] is True
    assert accepted_body["broker_accepted"] is True
    assert accepted_body["dispatch_pending"] is False
    assert accepted_body["replayed"] is False
    assert accepted_body["application_id"] == int(first_app.id)
    delay.assert_called_once()

    # Reusing the key for another role is a scope conflict, not a second run.
    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        conflict = client.post(
            f"/api/v1/roles/{second_role.id}/agent/run-now",
            json={"application_id": second_app.id},
            headers=request_headers,
        )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["code"] == "MANUAL_RUN_IDEMPOTENCY_CONFLICT"
    delay.assert_not_called()
    assert db.query(AgentRun).count() == 1

    # An application from another role is rejected before any intent/publish.
    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        wrong_application = client.post(
            f"/api/v1/roles/{first_role.id}/agent/run-now",
            json={
                "application_id": second_app.id,
                "idempotency_key": "wrong-application-scope",
            },
            headers=headers,
        )
    assert wrong_application.status_code == 404, wrong_application.text
    delay.assert_not_called()
    assert db.query(AgentRun).count() == 1


def test_run_now_conceals_closed_and_disqualified_applications(client, db):
    headers, email = auth_headers(client)
    _user, organization_id = _user_and_org(db, email)
    role = _role(db, organization_id, "Closed application scope")
    closed = _application(
        db,
        organization_id=organization_id,
        role=role,
        suffix="closed",
    )
    closed.application_outcome = "hired"
    disqualified = _application(
        db,
        organization_id=organization_id,
        role=role,
        suffix="disqualified",
    )
    disqualified.workable_disqualified = True
    db.commit()

    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        for application, key in (
            (closed, "closed-manual-run"),
            (disqualified, "disqualified-manual-run"),
        ):
            response = client.post(
                f"/api/v1/roles/{role.id}/agent/run-now",
                json={"application_id": int(application.id)},
                headers={**headers, "Idempotency-Key": key},
            )
            assert response.status_code == 404, response.text
            assert response.json() == {
                "detail": "application not found for this role"
            }

    delay.assert_not_called()
    assert db.query(AgentRun).count() == 0


def test_run_now_legacy_request_id_is_a_stable_fallback(client, db):
    headers, email = auth_headers(client)
    _user, organization_id = _user_and_org(db, email)
    role = _role(db, organization_id, "Legacy request id")
    db.commit()
    request_headers = {**headers, "X-Request-ID": "legacy-run-now-retry"}

    with patch(
        "app.tasks.agent_tasks.agent_manual_run.delay",
        return_value=SimpleNamespace(id="legacy-task"),
    ) as delay:
        first = client.post(
            f"/api/v1/roles/{role.id}/agent/run-now",
            json={},
            headers=request_headers,
        )
        replay = client.post(
            f"/api/v1/roles/{role.id}/agent/run-now",
            json={},
            headers=request_headers,
        )

    assert first.status_code == 200, first.text
    assert first.json()["queued"] is True
    assert replay.status_code == 200, replay.text
    assert replay.json()["queued"] is False
    assert replay.json()["replayed"] is True
    assert replay.json()["agent_run_id"] == first.json()["agent_run_id"]
    assert delay.call_count == 1
    assert db.query(AgentRun).count() == 1


def test_run_now_authorization_fails_before_intent_or_publish(client, db):
    headers, email = auth_headers(client)
    user, organization_id = _user_and_org(db, email)
    role = _role(db, organization_id, "Unauthorized run")
    user.role = "member"
    db.commit()

    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        response = client.post(
            f"/api/v1/roles/{role.id}/agent/run-now",
            json={"idempotency_key": "unauthorized-run"},
            headers=headers,
        )

    assert response.status_code == 403, response.text
    delay.assert_not_called()
    assert db.query(AgentRun).count() == 0


def test_run_now_rejects_conflicting_body_and_header_keys(client, db):
    headers, email = auth_headers(client)
    _user, organization_id = _user_and_org(db, email)
    role = _role(db, organization_id, "Conflicting keys")
    db.commit()

    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        response = client.post(
            f"/api/v1/roles/{role.id}/agent/run-now",
            json={"idempotency_key": "body-key"},
            headers={**headers, "Idempotency-Key": "header-key"},
        )

    assert response.status_code == 422, response.text
    delay.assert_not_called()
    assert db.query(AgentRun).count() == 0
