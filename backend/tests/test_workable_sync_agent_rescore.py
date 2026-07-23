"""Workable context changes invalidate owner scores without paid re-scoring.

Policy: a Workable sync stores fresh data (answers, comments, activities,
profile fields) and marks an existing score stale only when the exact rendered
scoring context changes. It NEVER dispatches paid re-scoring. Re-evaluation is
recruiter-triggered via the agent-chat flow that quotes the estimated cost.

The old auto-rescore-on-context-change trigger looped forever on
candidates with applications on multiple agent-on roles (each role's
sync overwrote the shared candidate row, so the context "changed" on
every alternate sync) and silently burned API credits. Per-application rendered
context digests prevent that oscillation while preserving honest owner-score
staleness. Independent related-role scores consume CV evidence, not this
owner-job context.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.components.integrations.workable.service import WorkableService
from app.components.integrations.workable.sync_service import WorkableSyncService
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import CvScoreJob
from app.models.organization import Organization
from app.models.role import JOB_STATUS_OPEN, ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation


_CANDIDATE_ID = "wk_cand_42"


class _StubClient(WorkableService):
    """Workable client stub that returns one comment-type activity per
    fetch. Different comment bodies across calls so the digest changes
    between syncs. The single ``get_candidate_activities`` call mirrors
    production: Workable doesn't expose a GET on /candidates/:id/comments,
    so comments arrive via the activities feed (``action="comment"``).
    """

    def __init__(
        self,
        comments_body: str,
        *,
        candidate_fields: dict | None = None,
        activities: list[dict] | None = None,
    ):
        super().__init__(access_token="x", subdomain="test")
        self._comments_body = comments_body
        self._candidate_fields = candidate_fields or {}
        self._activities = activities

    def get_candidate(self, candidate_id):
        return {"id": candidate_id, "name": "Alice", **self._candidate_fields}

    def get_candidate_activities(self, candidate_id):
        if self._activities is not None:
            return self._activities
        return [
            {
                "action": "comment",
                "body": self._comments_body,
                "member": {"name": "Recruiter"},
            }
        ]

    def download_candidate_resume(self, payload):
        return None

    def extract_workable_score(self, *, candidate_payload, ratings_payload=None):
        return None, None, None


def _build_org_role_candidate_app(
    db,
    *,
    org_slug: str,
    agentic: bool,
    starred: bool,
    pre_screen_score: float | None,
    cv_match_score: float | None,
):
    org = Organization(
        name=f"Org {org_slug}",
        slug=org_slug,
        workable_connected=True,
        workable_access_token="x",
        workable_subdomain="test",
    )
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Backend",
        source="workable",
        job_spec_text="Hiring a senior backend engineer.",
        workable_job_id="J1",
        starred_for_auto_sync=starred,
        agentic_mode_enabled=agentic,
        monthly_usd_budget_cents=5000,
    )
    db.add(role)
    db.flush()
    candidate = Candidate(
        organization_id=org.id,
        email="a@x.test",
        full_name="Alice",
        workable_candidate_id=_CANDIDATE_ID,
        workable_data={"id": _CANDIDATE_ID, "answers": []},
        # Pre-seed with a baseline comment so the next sync's comment is
        # a real "change" the digest catches.
        workable_comments=[{"body": "Initial comment", "member": {"name": "Recruiter"}}],
        workable_activities=[],
        cv_text="Senior backend engineer with 8 years of Python at scale.",
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="sync",
        cv_text="Senior backend engineer with 8 years of Python at scale.",
        pre_screen_score_100=pre_screen_score,
        cv_match_score=cv_match_score,
        workable_candidate_id=_CANDIDATE_ID,
        workable_stage="Phone Screen",
        workable_answers=[],
        workable_comments=[
            {"body": "Initial comment", "member": {"name": "Recruiter"}}
        ],
        workable_activities=[],
    )
    db.add(app)
    db.flush()
    return org, role, candidate, app


def _run_one_candidate_sync(
    db,
    *,
    org: Organization,
    role: Role,
    new_comment_body: str,
    client: WorkableService | None = None,
):
    """Drive ``_sync_candidate_for_role`` once in full mode with a stub
    that returns a different comment than what's stored.
    """
    service = WorkableSyncService(client or _StubClient(new_comment_body))
    service._sync_candidate_for_role(
        db=db,
        org=org,
        role=role,
        job={"id": role.workable_job_id, "shortcode": role.workable_job_id},
        candidate_ref={"id": _CANDIDATE_ID, "email": "a@x.test", "stage": "Phone Screen"},
        now=datetime.now(timezone.utc),
        run=None,
        mode="full",
    )


@pytest.mark.parametrize(
    ("agentic", "paused", "job_state", "expected_paid"),
    [
        pytest.param(True, False, "published", True, id="enabled"),
        pytest.param(True, True, "published", False, id="paused"),
        pytest.param(False, False, "published", False, id="off"),
        pytest.param(True, False, "closed", False, id="provider-closed"),
    ],
)
def test_new_workable_application_paid_dispatch_requires_running_agent(
    db,
    *,
    agentic,
    paused,
    job_state,
    expected_paid,
):
    """A sticky adoption star must never outlive the agent's spend grant."""

    slug = f"workable-paid-guard-{agentic}-{paused}-{job_state}"
    org = Organization(
        name=f"Org {slug}",
        slug=slug,
        workable_connected=True,
        workable_access_token="x",
        workable_subdomain="test",
    )
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Backend",
        source="workable",
        job_spec_text="Hiring a senior backend engineer.",
        workable_job_id="J1",
        workable_job_data={"state": job_state},
        job_status=JOB_STATUS_OPEN,
        starred_for_auto_sync=True,
        agentic_mode_enabled=agentic,
        agent_paused_at=(datetime.now(timezone.utc) if paused else None),
        monthly_usd_budget_cents=5000,
    )
    db.add(role)
    db.flush()

    with patch(
        "app.components.integrations.workable.sync_service.on_application_created"
    ) as on_created:
        _run_one_candidate_sync(
            db,
            org=org,
            role=role,
            new_comment_body="Metadata still synchronizes.",
        )

    on_created.assert_called_once()
    assert on_created.call_args.kwargs == {
        "score": expected_paid,
        "allow_paid_work": expected_paid,
        "parse_origin": "ats_ingest",
    }
    app = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.role_id == role.id)
        .one()
    )
    assert app.source == "workable"
    assert role.starred_for_auto_sync is True


def test_new_workable_cv_import_does_not_create_stale_marker_before_outbox(db):
    """The creation outbox owns first scoring; CV invalidation is for old apps."""

    class ResumeClient(_StubClient):
        def download_candidate_resume(self, payload):
            return "resume.pdf", b"new resume"

    org = Organization(
        name="New CV import",
        slug="new-cv-import",
        workable_connected=True,
        workable_access_token="x",
        workable_subdomain="test",
    )
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Backend",
        source="workable",
        job_spec_text="Hiring a senior backend engineer.",
        workable_job_id="NEW-CV",
        workable_job_data={"state": "published"},
        job_status=JOB_STATUS_OPEN,
        starred_for_auto_sync=True,
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
    )
    db.add(role)
    db.flush()

    def store_resume(*, app, candidate, filename, content):
        del filename, content
        now = datetime.now(timezone.utc)
        app.cv_text = "Newly imported candidate CV"
        app.cv_uploaded_at = now
        candidate.cv_text = app.cv_text
        candidate.cv_uploaded_at = now
        return True

    with (
        patch(
            "app.components.integrations.workable.sync_service._store_candidate_resume",
            side_effect=store_resume,
        ),
        patch(
            "app.services.cv_score_orchestrator.mark_application_scores_stale"
        ) as mark_stale,
        patch(
            "app.components.integrations.workable.sync_service.on_application_created"
        ) as on_created,
    ):
        _run_one_candidate_sync(
            db,
            org=org,
            role=role,
            new_comment_body="Initial import",
            client=ResumeClient("Initial import"),
        )

    application = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.role_id == role.id)
        .one()
    )
    mark_stale.assert_not_called()
    assert db.query(CvScoreJob).filter_by(application_id=application.id).count() == 0
    on_created.assert_called_once()


def _sync_and_collect_rescore_calls(db, *, org, role):
    with patch(
        "app.services.cv_score_orchestrator.enqueue_score",
        return_value=None,
    ) as mock_enqueue, patch(
        "app.services.cv_score_orchestrator.mark_application_scores_stale",
        return_value=True,
    ) as mock_stale:
        _run_one_candidate_sync(db, org=org, role=role, new_comment_body="Asking for 65k")
        enqueued = {call.args[1].id for call in mock_enqueue.call_args_list}
        return enqueued, mock_stale.call_args_list


def test_agent_on_role_marks_material_context_change_stale_without_rescore(db):
    """A rendered comment change invalidates, but never spends automatically."""
    org, role, candidate, app = _build_org_role_candidate_app(
        db,
        org_slug="agent-on-no-auto-rescore",
        agentic=True,
        starred=True,  # agent-on always implies starred via auto-star
        pre_screen_score=72.0,
        cv_match_score=85.0,
    )
    with patch(
        "app.services.cv_score_orchestrator.enqueue_score",
        return_value=None,
    ) as mock_enqueue:
        _run_one_candidate_sync(
            db, org=org, role=role, new_comment_body="Asking for 65k"
        )
    assert not mock_enqueue.called, "sync must never enqueue a paid rescore"
    stale = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == app.id)
        .order_by(CvScoreJob.id.desc())
        .first()
    )
    assert stale is not None
    assert stale.status == "stale"
    assert stale.error_message == "workable_context_changed"
    # The hourly cohort drain only recovers explicitly authorized RoleIntent /
    # pause deferrals. A sync-owned marker stays visible until recruiter opt-in.
    from app.tasks.agent_tasks import _requeue_deferred_agent_scores

    with patch("app.services.cv_score_orchestrator.enqueue_score") as retry:
        assert _requeue_deferred_agent_scores(
            db, role=role, limit=50
        ) == (0, set())
    assert not retry.called
    # The new data still lands for display / the next approved evaluation —
    # on the application's own per-role copy as well as the shared
    # candidate-level fallback.
    assert candidate.workable_comments[0]["body"] == "Asking for 65k"
    assert app.workable_comments[0]["body"] == "Asking for 65k"
    assert app.workable_activities == []
    # Existing numeric scores remain visible beside the stale badge.
    assert app.pre_screen_score_100 is not None
    assert app.cv_match_score is not None


def test_owner_context_resync_preserves_independent_related_score_and_decision(db):
    """Owner questionnaires/comments/activity are not related-role inputs."""

    org, owner, _candidate, app = _build_org_role_candidate_app(
        db,
        org_slug="related-context-isolation",
        agentic=True,
        starred=True,
        pre_screen_score=72.0,
        cv_match_score=85.0,
    )
    related = Role(
        organization_id=org.id,
        name="Related backend role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
        job_spec_text="A related role requiring Python platform ownership.",
        agentic_mode_enabled=True,
    )
    db.add(related)
    db.flush()
    evaluation = SisterRoleEvaluation(
        organization_id=org.id,
        role_id=related.id,
        source_application_id=app.id,
        status="done",
        pipeline_stage="review",
        spec_fingerprint="old-spec",
        cv_fingerprint="old-cv",
        role_fit_score=84.0,
        summary="Strong fit before the new recruiter comment.",
        details={"role_fit_score": 84.0},
        model_version="old-model",
        prompt_version="old-prompt",
        trace_id="old-trace",
        scored_at=datetime.now(timezone.utc),
    )
    db.add(evaluation)
    run = AgentRun(
        id=910_001,
        organization_id=org.id,
        role_id=related.id,
        trigger="manual",
        status="succeeded",
        model_version="old-model",
        prompt_version="old-prompt",
    )
    db.add(run)
    db.flush()
    decision = AgentDecision(
        id=910_001,
        organization_id=org.id,
        role_id=related.id,
        application_id=app.id,
        agent_run_id=run.id,
        decision_type="send_assessment",
        recommendation="send_assessment",
        status="pending",
        reasoning="Independent related-role recommendation.",
        evidence={"score": 84},
        model_version="old-model",
        prompt_version="old-prompt",
        idempotency_key=f"owner-context-isolation:{related.id}:{app.id}",
    )
    db.add(decision)
    db.commit()

    with (
        patch(
            "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
        ) as score_dispatch,
        patch("app.cv_matching.holistic.run_holistic_match") as paid_provider,
        patch(
            "app.tasks.automation_tasks.parse_application_cv_sections.apply_async",
            return_value=None,
        ),
    ):
        _run_one_candidate_sync(
            db,
            org=org,
            role=owner,
            new_comment_body="Availability changed to a twelve-week notice period.",
        )
        db.commit()
    db.expire_all()
    saved = db.get(SisterRoleEvaluation, int(evaluation.id))
    score_dispatch.assert_not_called()
    paid_provider.assert_not_called()
    assert saved.status == "done"
    assert saved.role_fit_score == 84.0
    assert saved.summary == "Strong fit before the new recruiter comment."
    assert saved.details == {"role_fit_score": 84.0}
    assert saved.dispatch_attempted_at is None
    assert saved.started_at is None
    assert saved.model_version == "old-model"
    assert saved.prompt_version == "old-prompt"
    assert saved.trace_id == "old-trace"
    assert saved.history is None
    saved_decision = db.get(AgentDecision, int(decision.id))
    assert saved_decision.status == "pending"
    assert saved_decision.reasoning == "Independent related-role recommendation."
    owner_stale = (
        db.query(CvScoreJob)
        .filter(
            CvScoreJob.application_id == int(app.id),
            CvScoreJob.status == "stale",
        )
        .one()
    )
    assert owner_stale.error_message == "workable_context_changed"

    # The every-minute recovery sweep is transport recovery, not recruiter
    # authority. It must never turn this passive sync into a paid score.
    from app.tasks.sister_role_tasks import recover_sister_role_evaluations

    with patch(
        "app.tasks.sister_role_tasks.dispatch_sister_evaluation",
        return_value={"status": "queued"},
    ) as recovery_dispatch:
        recover_sister_role_evaluations.run(limit=200)
    assert all(
        call.kwargs.get("evaluation_id") != int(evaluation.id)
        for call in recovery_dispatch.call_args_list
    )


def test_starred_only_role_does_not_rescore_on_context_change(db):
    org, role, _, app = _build_org_role_candidate_app(
        db,
        org_slug="starred-only-norescore",
        agentic=False,
        starred=True,
        pre_screen_score=72.0,
        cv_match_score=85.0,
    )
    enqueued, stale_calls = _sync_and_collect_rescore_calls(db, org=org, role=role)
    assert app.id not in enqueued
    assert len(stale_calls) == 1
    assert stale_calls[0].args[1] == app.id
    assert stale_calls[0].kwargs == {"reason": "workable_context_changed"}


@pytest.mark.parametrize(
    "client",
    [
        pytest.param(
            _StubClient(
                "Initial comment",
                candidate_fields={
                    "answers": [
                        {
                            "question": {"body": "Salary expectation?"},
                            "answer": {"body": "AED 65k"},
                        }
                    ]
                },
            ),
            id="questionnaire-answer",
        ),
        pytest.param(
            _StubClient(
                "Initial comment",
                activities=[
                    {
                        "action": "comment",
                        "body": "Initial comment",
                        "member": {"name": "Recruiter"},
                    },
                    {
                        "action": "interview",
                        "stage_name": "Phone Screen",
                        "created_at": "2026-07-20T08:00:00Z",
                    },
                ],
            ),
            id="non-comment-activity",
        ),
    ],
)
def test_other_rendered_workable_surfaces_mark_scores_stale(db, client):
    org, role, _, app = _build_org_role_candidate_app(
        db,
        org_slug=f"rendered-surface-{id(client)}",
        agentic=False,
        starred=True,
        pre_screen_score=72.0,
        cv_match_score=85.0,
    )
    with patch(
        "app.services.cv_score_orchestrator.enqueue_score",
        return_value=None,
    ) as mock_enqueue, patch(
        "app.services.cv_score_orchestrator.mark_application_scores_stale",
        return_value=True,
    ) as mock_stale:
        _run_one_candidate_sync(
            db,
            org=org,
            role=role,
            new_comment_body="Initial comment",
            client=client,
        )
    mock_stale.assert_called_once_with(
        db, app.id, reason="workable_context_changed"
    )
    assert not mock_enqueue.called


def test_agent_on_role_never_scored_app_left_for_normal_pipeline(db):
    """An app the agent hasn't scored yet is left for the agent's normal
    new-candidate pipeline; the sync itself still dispatches nothing."""
    org, role, _, app = _build_org_role_candidate_app(
        db,
        org_slug="agent-on-norescore-no-history",
        agentic=True,
        starred=True,
        pre_screen_score=None,
        cv_match_score=None,
    )
    enqueued, stale_calls = _sync_and_collect_rescore_calls(db, org=org, role=role)
    assert app.id not in enqueued
    assert not stale_calls


def test_empty_comments_response_overwrites_stale_stored_comments(db):
    """When Workable returns an empty comments list (recruiter cleared
    them), the candidate's stored comments must be overwritten to ``[]``
    so the digest reflects the change and pre-screen sees current state.

    Previously ``if comments:`` skipped the assignment on empty lists,
    leaving stale data and silently breaking the rescore trigger."""

    class EmptyActivitiesClient(WorkableService):
        def __init__(self):
            super().__init__(access_token="x", subdomain="test")

        def get_candidate(self, cid):
            return {"id": cid, "name": "Alice"}

        def get_candidate_activities(self, cid):
            return []  # Recruiter cleared the candidate → empty feed (not None)

        def download_candidate_resume(self, p):
            return None

        def extract_workable_score(self, *, candidate_payload, ratings_payload=None):
            return None, None, None

    org, role, candidate, _ = _build_org_role_candidate_app(
        db,
        org_slug="empty-overwrites-stale",
        agentic=True,
        starred=True,
        pre_screen_score=72.0,
        cv_match_score=None,
    )
    assert candidate.workable_comments == [
        {"body": "Initial comment", "member": {"name": "Recruiter"}}
    ]

    service = WorkableSyncService(EmptyActivitiesClient())
    service._sync_candidate_for_role(
        db=db, org=org, role=role,
        job={"id": role.workable_job_id, "shortcode": role.workable_job_id},
        candidate_ref={"id": _CANDIDATE_ID, "email": "a@x.test", "stage": "Phone Screen"},
        now=datetime.now(timezone.utc), run=None, mode="full",
    )
    db.refresh(candidate)
    assert candidate.workable_comments == [], (
        "empty fetch response must clear stale comments"
    )


def test_none_comments_response_preserves_stale_stored_comments(db):
    """When the comments endpoint fails (returns ``None``), we must
    keep the previously stored comments rather than clobber them with
    an empty list — that would lose data on every transient failure."""

    class FailingActivitiesClient(WorkableService):
        def __init__(self):
            super().__init__(access_token="x", subdomain="test")

        def get_candidate(self, cid):
            return {"id": cid, "name": "Alice"}

        def get_candidate_activities(self, cid):
            return None  # Fetch failure (rate-limit / 404 / transport)

        def download_candidate_resume(self, p):
            return None

        def extract_workable_score(self, *, candidate_payload, ratings_payload=None):
            return None, None, None

    org, role, candidate, _ = _build_org_role_candidate_app(
        db,
        org_slug="none-preserves-stale",
        agentic=True,
        starred=True,
        pre_screen_score=72.0,
        cv_match_score=None,
    )
    original_comments = candidate.workable_comments

    service = WorkableSyncService(FailingActivitiesClient())
    service._sync_candidate_for_role(
        db=db, org=org, role=role,
        job={"id": role.workable_job_id, "shortcode": role.workable_job_id},
        candidate_ref={"id": _CANDIDATE_ID, "email": "a@x.test", "stage": "Phone Screen"},
        now=datetime.now(timezone.utc), run=None, mode="full",
    )
    db.refresh(candidate)
    assert candidate.workable_comments == original_comments, (
        "fetch failure must not clobber stored comments"
    )


def test_activities_pagination_walks_all_pages(db, monkeypatch):
    """Workable paginates activities at 50/page by default. A candidate
    with a long history (multiple comments + assessment + interview +
    messages) would lose older entries if sync stopped after page 1.

    This test pages through three responses (50 + 50 + 30 = 130 total)
    and asserts every comment-typed entry made it into ``workable_comments``.
    """
    pages = [
        {
            "activities": [
                {"action": "comment", "body": f"page1 #{i}", "member": {"name": "R"}}
                for i in range(50)
            ],
            "paging": {"next": "https://x.workable.com/spi/v3/page2"},
        },
        {
            "activities": [
                {"action": "comment", "body": f"page2 #{i}", "member": {"name": "R"}}
                for i in range(50)
            ],
            "paging": {"next": "https://x.workable.com/spi/v3/page3"},
        },
        {
            "activities": [
                {"action": "comment", "body": f"page3 #{i}", "member": {"name": "R"}}
                for i in range(30)
            ],
            # No paging.next → walk stops.
        },
    ]

    class PaginatingClient(WorkableService):
        def __init__(self):
            super().__init__(access_token="x", subdomain="test")
            self._call_count = 0

        def get_candidate(self, cid):
            return {"id": cid, "name": "Alice"}

        def download_candidate_resume(self, p):
            return None

        def extract_workable_score(self, *, candidate_payload, ratings_payload=None):
            return None, None, None

        def _request_optional(self, method, path, **kwargs):
            self._call_count += 1
            return pages[0]

        def _get_next_page(self, next_url):
            # Cursor walks pages 2 → 3 → exhausted.
            if "page2" in next_url:
                return pages[1]
            if "page3" in next_url:
                return pages[2]
            return {}

    org, role, candidate, _ = _build_org_role_candidate_app(
        db,
        org_slug="activities-paginate",
        agentic=False,
        starred=True,
        pre_screen_score=None,
        cv_match_score=None,
    )
    service = WorkableSyncService(PaginatingClient())
    service._sync_candidate_for_role(
        db=db, org=org, role=role,
        job={"id": role.workable_job_id, "shortcode": role.workable_job_id},
        candidate_ref={"id": _CANDIDATE_ID, "email": "a@x.test", "stage": "Phone Screen"},
        now=datetime.now(timezone.utc), run=None, mode="full",
    )
    db.refresh(candidate)
    # 130 total comment entries across 3 pages — none lost.
    assert candidate.workable_comments is not None
    assert len(candidate.workable_comments) == 130
    # Older entries (later pages) must be present, not just page 1.
    bodies = {c.get("body") for c in candidate.workable_comments}
    assert "page1 #0" in bodies
    assert "page2 #49" in bodies
    assert "page3 #29" in bodies


def test_activities_split_into_comments_and_non_comments(db):
    """The activities feed is the only source for both comments and
    the rest of the timeline (Workable's API doesn't expose a GET on
    /candidates/:id/comments). Sync must split ``action=='comment'``
    entries into ``workable_comments`` and the rest into
    ``workable_activities`` so the formatter renders them as distinct
    <WORKABLE_*> blocks."""

    class MixedActivitiesClient(WorkableService):
        def __init__(self):
            super().__init__(access_token="x", subdomain="test")

        def get_candidate(self, cid):
            return {"id": cid, "name": "Alice"}

        def get_candidate_activities(self, cid):
            return [
                {
                    "action": "comment",
                    "body": "Phone screen: candidate asking for 65k.",
                    "member": {"name": "Jade"},
                    "created_at": "2026-02-13T08:57:45Z",
                },
                {
                    "action": "applied",
                    "stage_name": "Applied",
                    "created_at": "2026-02-09T20:21:17Z",
                },
                {
                    "action": "rating",
                    "stage_name": "Applied",
                    "body": "Auto-scored 92%",
                    "created_at": "2026-02-10T10:00:00Z",
                },
                {
                    "action": "comment",
                    "body": "Confirmed UAE residency requirement.",
                    "member": {"name": "Jade"},
                    "created_at": "2026-02-14T09:00:00Z",
                },
            ]

        def download_candidate_resume(self, p):
            return None

        def extract_workable_score(self, *, candidate_payload, ratings_payload=None):
            return None, None, None

    org, role, candidate, _ = _build_org_role_candidate_app(
        db,
        org_slug="split-comments-activities",
        agentic=False,
        starred=True,
        pre_screen_score=None,
        cv_match_score=None,
    )
    service = WorkableSyncService(MixedActivitiesClient())
    service._sync_candidate_for_role(
        db=db, org=org, role=role,
        job={"id": role.workable_job_id, "shortcode": role.workable_job_id},
        candidate_ref={"id": _CANDIDATE_ID, "email": "a@x.test", "stage": "Phone Screen"},
        now=datetime.now(timezone.utc), run=None, mode="full",
    )
    db.refresh(candidate)
    assert candidate.workable_comments is not None
    assert len(candidate.workable_comments) == 2
    assert all(c.get("action") == "comment" for c in candidate.workable_comments)
    assert "65k" in candidate.workable_comments[0]["body"]
    # Non-comment activities land in the activities column.
    assert candidate.workable_activities is not None
    assert len(candidate.workable_activities) == 2
    assert {a.get("action") for a in candidate.workable_activities} == {"applied", "rating"}


def test_agent_on_role_skips_rescore_when_context_unchanged(db):
    """Idempotent sync: re-syncing with identical Workable data must not
    enqueue a fresh rescore on every Beat tick."""
    org, role, candidate, app = _build_org_role_candidate_app(
        db,
        org_slug="agent-on-norescore-noop",
        agentic=True,
        starred=True,
        pre_screen_score=72.0,
        cv_match_score=85.0,
    )
    # Raw storage shape changes (adding ``action``) but the normalized renderer
    # produces the same prompt text, so this is deliberately a no-op.
    candidate.workable_comments = [
        {
            "action": "comment",
            "body": "Same comment",
            "member": {"name": "Recruiter"},
        }
    ]
    app.workable_comments = [
        {
            "body": "Same comment",
            "member": {"name": "Recruiter"},
        }
    ]
    # The list/detail payload may omit ``sourced``; omission is not an
    # authoritative change and must preserve the last known prompt value.
    app.workable_sourced = True
    db.flush()
    with patch(
        "app.services.cv_score_orchestrator.enqueue_score",
        return_value=None,
    ) as mock_enqueue, patch(
        "app.services.cv_score_orchestrator.mark_application_scores_stale",
        return_value=True,
    ) as mock_stale:
        _run_one_candidate_sync(db, org=org, role=role, new_comment_body="Same comment")
        called_app_ids = {call.args[1].id for call in mock_enqueue.call_args_list}
        assert app.id not in called_app_ids, (
            "context unchanged → no rescore"
        )
        assert not mock_stale.called, "normalized context unchanged → not stale"
        assert app.workable_sourced is True


def test_per_application_digest_prevents_multi_role_context_oscillation(db):
    """Shared Candidate profile churn must not repeatedly stale role A."""

    class ProfileClient(_StubClient):
        def __init__(self, *, headline: str, comment: str):
            super().__init__(comment)
            self._headline = headline

        def get_candidate(self, candidate_id):
            return {
                "id": candidate_id,
                "name": "Alice",
                "headline": self._headline,
            }

    org, role_a, candidate, app_a = _build_org_role_candidate_app(
        db,
        org_slug="multi-role-context-digest",
        agentic=True,
        starred=True,
        pre_screen_score=72.0,
        cv_match_score=85.0,
    )
    candidate.headline = "Role A profile"
    app_a.workable_comments = [
        {"body": "Role A note", "member": {"name": "Recruiter"}}
    ]
    candidate.workable_comments = app_a.workable_comments

    role_b = Role(
        organization_id=org.id,
        name="Platform",
        source="workable",
        job_spec_text="Hiring a platform engineer.",
        workable_job_id="J2",
        starred_for_auto_sync=True,
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
    )
    db.add(role_b)
    db.flush()
    app_b = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role_b.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="sync",
        cv_text=app_a.cv_text,
        pre_screen_score_100=71.0,
        cv_match_score=84.0,
        workable_candidate_id="wk_cand_role_b",
        workable_stage="Phone Screen",
        workable_answers=[],
        workable_comments=[
            {"body": "Role B note", "member": {"name": "Recruiter"}}
        ],
        workable_activities=[],
    )
    db.add(app_b)
    db.flush()

    def sync(*, role, candidate_id, headline, comment):
        WorkableSyncService(
            ProfileClient(headline=headline, comment=comment)
        )._sync_candidate_for_role(
            db=db,
            org=org,
            role=role,
            job={"id": role.workable_job_id, "shortcode": role.workable_job_id},
            candidate_ref={
                "id": candidate_id,
                "email": "a@x.test",
                "stage": "Phone Screen",
            },
            now=datetime.now(timezone.utc),
            run=None,
            mode="full",
        )

    with patch(
        "app.services.cv_score_orchestrator.enqueue_score",
        return_value=None,
    ) as mock_enqueue, patch(
        "app.services.cv_score_orchestrator.mark_application_scores_stale",
        return_value=True,
    ) as mock_stale:
        # First A sync is a normalized no-op and records A's own digest.
        sync(
            role=role_a,
            candidate_id=_CANDIDATE_ID,
            headline="Role A profile",
            comment="Role A note",
        )
        # B legitimately changes the shared Candidate snapshot.
        sync(
            role=role_b,
            candidate_id="wk_cand_role_b",
            headline="Role B profile",
            comment="Role B note",
        )
        calls_before_a_returns = len(mock_stale.call_args_list)
        # Returning to A restores shared fields, but its application-owned
        # digest proves that A's rendered context itself did not change.
        sync(
            role=role_a,
            candidate_id=_CANDIDATE_ID,
            headline="Role A profile",
            comment="Role A note",
        )

    assert len(mock_stale.call_args_list) == calls_before_a_returns
    assert not mock_enqueue.called
    assert "workable_scoring_context_digest" in app_a.integration_sync_state


def test_phone_identity_fallback_invalidates_changed_existing_app_context(db):
    """A new Workable id/email must not launder an old score via phone dedupe."""
    from app.components.integrations.workable.scoring_context_freshness import (
        SCORING_CONTEXT_DIGEST_KEY,
        rendered_workable_scoring_context_digest,
    )

    org, role, candidate, app = _build_org_role_candidate_app(
        db,
        org_slug="phone-fallback-context-change",
        agentic=True,
        starred=True,
        pre_screen_score=72.0,
        cv_match_score=85.0,
    )
    candidate.phone = "+971 50 202 2165"
    candidate.phone_normalized = "502022165"
    candidate.headline = "Original profile"
    app.integration_sync_state = {
        SCORING_CONTEXT_DIGEST_KEY: rendered_workable_scoring_context_digest(
            candidate, app
        )
    }
    db.flush()

    client = _StubClient(
        "Initial comment",
        candidate_fields={
            "email": "new-address@example.test",
            "phone": "0502022165",
            "headline": "Materially changed profile",
        },
    )
    service = WorkableSyncService(client)
    with patch(
        "app.services.cv_score_orchestrator.mark_application_scores_stale",
        return_value=True,
    ) as mark_stale, patch(
        "app.services.cv_score_orchestrator.enqueue_score",
        return_value=None,
    ) as enqueue_score:
        service._sync_candidate_for_role(
            db=db,
            org=org,
            role=role,
            job={"id": role.workable_job_id, "shortcode": role.workable_job_id},
            candidate_ref={
                "id": "wk_cand_reidentified",
                "email": "new-address@example.test",
                "phone": "0502022165",
                "stage": "Phone Screen",
            },
            now=datetime.now(timezone.utc),
            run=None,
            mode="full",
        )

    mark_stale.assert_called_once_with(
        db,
        int(app.id),
        reason="workable_context_changed",
    )
    enqueue_score.assert_not_called()
    assert app.workable_candidate_id == "wk_cand_reidentified"
