"""Assessment completion stays inside Taali rather than posting ATS comments."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.components.assessments.submission_runtime import submit_assessment_impl
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.organization import Organization
from app.models.task import Task
from app.tasks import assessment_tasks


class _SubmissionSandbox:
    def run_code(self, _code):
        return {
            "stdout": (
                '{"files":{"src/main.py":"candidate work\\n"},'
                '"error":null}\n'
            )
        }


class _SubmissionRuntime:
    def __init__(self, _api_key):
        pass

    def create_sandbox(self):
        return _SubmissionSandbox()

    def connect_sandbox(self, _sandbox_id):
        return _SubmissionSandbox()

    def run_tests(self, _sandbox, _test_code):
        return {"passed": 3, "failed": 1, "total": 4}

    def close_sandbox(self, _sandbox):
        pass


def test_completed_assessment_does_not_enqueue_workable_human_comment(db):
    org = Organization(
        name="Completion Policy Org",
        slug=f"completion-policy-{id(db)}",
        workable_connected=True,
        workable_access_token="wk-token",
        workable_subdomain="completion-policy",
        workable_config={
            "workable_writeback": True,
            "granted_scopes": ["r_candidates", "w_candidates"],
            "workable_actor_member_id": "member-1",
        },
    )
    db.add(org)
    db.flush()
    candidate = Candidate(
        organization_id=org.id,
        email="completion-policy@example.test",
        full_name="Completion Policy Candidate",
    )
    task = Task(
        organization_id=org.id,
        name="Completion Policy Task",
        is_active=True,
        evaluation_rubric=None,
        extra_data={},
    )
    db.add_all([candidate, task])
    db.flush()
    assessment = Assessment(
        organization_id=org.id,
        candidate_id=candidate.id,
        task_id=task.id,
        token="completion-policy-token",
        status=AssessmentStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
        duration_minutes=60,
        e2b_session_id="candidate-session",
        is_demo=True,
        workable_candidate_id="wk-candidate-1",
    )
    db.add(assessment)
    db.commit()

    settings_obj = SimpleNamespace(
        MVP_DISABLE_PROCTORING=False,
        MVP_DISABLE_WORKABLE=False,
        E2B_API_KEY="e2b-test",
        ANTHROPIC_API_KEY="",
        FRONTEND_URL="https://app.taali.test",
    )
    with patch.object(assessment_tasks.post_results_to_workable, "delay") as ats_comment:
        result = submit_assessment_impl(
            assessment,
            "candidate final answer",
            0,
            db,
            settings_obj=settings_obj,
            e2b_service_cls=_SubmissionRuntime,
            workspace_repo_root_fn=lambda _task: "/workspace/repo",
            collect_git_evidence_fn=lambda _sandbox, _root: {},
            defer_scoring=True,
        )

    assert result["success"] is True
    ats_comment.assert_not_called()
    db.refresh(assessment)
    assert assessment.status == AssessmentStatus.COMPLETED


def test_legacy_workable_completion_task_is_a_policy_noop():
    with patch(
        "app.domains.integrations_notifications.adapters.build_workable_adapter",
        side_effect=AssertionError("legacy task must not build an ATS adapter"),
    ) as adapter_factory:
        result = assessment_tasks.post_results_to_workable.run(
            access_token="legacy-token",
            subdomain="legacy-subdomain",
            candidate_id="legacy-candidate",
            assessment_data={"score": 8.4},
            member_id="legacy-member",
        )

    assert result == {
        "success": False,
        "skipped": True,
        "reason": "assessment_lifecycle_taali_native",
    }
    adapter_factory.assert_not_called()
