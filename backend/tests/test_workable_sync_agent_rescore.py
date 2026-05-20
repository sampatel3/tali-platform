"""Sync's agent-driven rescore trigger.

When an existing application on an agent-on role picks up new
questionnaire answers, recruiter comments, or activity entries — AND
the application already has scoring history — sync must enqueue a
rescore so pre-screen / cv_match see the new context.

Starred-only roles (no agent) must NOT trigger a rescore. Starring is
for keeping data fresh; the agent is what acts on changes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.components.integrations.workable.service import WorkableService
from app.components.integrations.workable.sync_service import WorkableSyncService
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role


_CANDIDATE_ID = "wk_cand_42"


class _StubClient(WorkableService):
    """Workable client stub that returns one comment-type activity per
    fetch. Different comment bodies across calls so the digest changes
    between syncs. The single ``get_candidate_activities`` call mirrors
    production: Workable doesn't expose a GET on /candidates/:id/comments,
    so comments arrive via the activities feed (``action="comment"``).
    """

    def __init__(self, comments_body: str):
        super().__init__(access_token="x", subdomain="test")
        self._comments_body = comments_body

    def get_candidate(self, candidate_id):
        return {"id": candidate_id, "name": "Alice"}

    def get_candidate_activities(self, candidate_id):
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
):
    """Drive ``_sync_candidate_for_role`` once in full mode with a stub
    that returns a different comment than what's stored.
    """
    service = WorkableSyncService(_StubClient(new_comment_body))
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


def test_agent_on_role_with_existing_score_enqueues_rescore_on_comment_change(db):
    org, role, _, app = _build_org_role_candidate_app(
        db,
        org_slug="agent-on-rescore",
        agentic=True,
        starred=True,  # agent-on always implies starred via auto-star
        pre_screen_score=72.0,
        cv_match_score=None,
    )
    with patch(
        "app.services.cv_score_orchestrator.enqueue_score",
        return_value=None,
    ) as mock_enqueue:
        _run_one_candidate_sync(db, org=org, role=role, new_comment_body="Asking for 65k")
        called_app_ids = {call.args[1].id for call in mock_enqueue.call_args_list}
        assert app.id in called_app_ids, "expected rescore enqueue for agent-on role"


def test_starred_only_role_does_not_enqueue_rescore(db):
    """Starred without agent = passive data fetch, no acting on changes."""
    org, role, _, app = _build_org_role_candidate_app(
        db,
        org_slug="starred-only-norescore",
        agentic=False,
        starred=True,
        pre_screen_score=72.0,
        cv_match_score=85.0,
    )
    with patch(
        "app.services.cv_score_orchestrator.enqueue_score",
        return_value=None,
    ) as mock_enqueue:
        _run_one_candidate_sync(db, org=org, role=role, new_comment_body="Asking for 65k")
        called_app_ids = {call.args[1].id for call in mock_enqueue.call_args_list}
        assert app.id not in called_app_ids, (
            "starred-only role must not trigger agent-style rescore"
        )


def test_agent_on_role_skips_rescore_when_never_scored(db):
    """An app the agent hasn't started on yet should be left for the
    agent's normal scoring pipeline, not auto-rescored on every sync."""
    org, role, _, app = _build_org_role_candidate_app(
        db,
        org_slug="agent-on-norescore-no-history",
        agentic=True,
        starred=True,
        pre_screen_score=None,
        cv_match_score=None,
    )
    with patch(
        "app.services.cv_score_orchestrator.enqueue_score",
        return_value=None,
    ) as mock_enqueue:
        _run_one_candidate_sync(db, org=org, role=role, new_comment_body="Asking for 65k")
        called_app_ids = {call.args[1].id for call in mock_enqueue.call_args_list}
        assert app.id not in called_app_ids, (
            "should not rescore an app that's never been scored"
        )


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
    # Pre-seed candidate with the same comment-shaped activity the stub
    # will return (post-split shape, including the ``action`` field) so
    # the before/after digests match exactly and no rescore fires.
    candidate.workable_comments = [
        {
            "action": "comment",
            "body": "Same comment",
            "member": {"name": "Recruiter"},
        }
    ]
    db.flush()
    with patch(
        "app.services.cv_score_orchestrator.enqueue_score",
        return_value=None,
    ) as mock_enqueue:
        _run_one_candidate_sync(db, org=org, role=role, new_comment_body="Same comment")
        called_app_ids = {call.args[1].id for call in mock_enqueue.call_args_list}
        assert app.id not in called_app_ids, (
            "context unchanged → no rescore"
        )
