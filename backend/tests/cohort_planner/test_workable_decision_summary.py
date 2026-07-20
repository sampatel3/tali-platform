"""Decision-summary ATS note + advance side effects.

Covers the helper that runs inside ``approve_decision.run`` /
``override_decision.run`` after the underlying action committed —
checks the no-op paths, canonical movement copy, and move-to-advance behavior.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.actions import _workable_decision_summary as wds
from app.actions.types import Actor
from app.models.agent_decision import AgentDecision
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.share_link import ShareLink
from app.models.user import User
from app.platform import config as platform_config

from .conftest import make_world


def _make_user(db, org) -> User:
    u = User(
        email=f"u-{id(db)}@x.test",
        hashed_password="x",
        full_name="R",
        organization_id=org.id,
        is_active=True,
        is_verified=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_decision(db, org, role, app, decision_type: str = "send_assessment") -> AgentDecision:
    d = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        agent_run_id=None,
        decision_type=decision_type,
        recommendation=decision_type,
        status="pending",
        reasoning="Strong AWS Glue match, missing Kafka exposure.",
        confidence=0.85,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"test:{app.id}:{decision_type}",
    )
    db.add(d)
    db.flush()
    return d


def _enable_workable(db, org) -> None:
    org.workable_connected = True
    org.workable_access_token = "tok"
    org.workable_subdomain = "acme"
    org.workable_config = {
        "granted_scopes": ["r_jobs", "r_candidates", "w_candidates"],
        "workable_actor_member_id": "member-1",
    }
    db.flush()


# ---------------------------------------------------------------------------
# compose_decision_summary_note — pure formatting
# ---------------------------------------------------------------------------


def test_compose_recruiter_advance_uses_canonical_movement_copy(db):
    org, role, _, app = make_world(db, pre_screen=50.0)
    decision = _make_decision(db, org, role, app, "advance_to_interview")
    decision.evidence = {
        "decision_stage": "full_scoring",
        "role_fit_score": 85.0,
        "effective_threshold": 65.0,
    }
    app.taali_score_cache_100 = 85.0

    body = wds.compose_decision_summary_note(
        decision,
        app,
        actor=Actor(type="recruiter", user_id=123),
        verdict="advanced",
        actor_name="Sam Patel",
        role_name="Backend Engineer",
        moved_to="Final interview",
        reason="Strong referral",
    )

    assert body == (
        "TAALI · Candidate advanced\n"
        "Role: Backend Engineer\n"
        "Moved to: Final interview\n"
        "TAALI score used: 85/100\n"
        "Role threshold: 65/100\n"
        "Decision: Advanced by Sam Patel\n"
        "Reason: The candidate met the role threshold and was approved for progression."
    )
    assert "confidence" not in body.lower()
    assert "http" not in body.lower()


def test_compose_override_uses_override_headline_and_recruiter_fallback(db):
    org, role, _, app = make_world(db, pre_screen=72.0)
    decision = _make_decision(db, org, role, app, decision_type="reject")

    body = wds.compose_decision_summary_note(
        decision,
        app,
        actor=Actor(type="recruiter", user_id=123),
        verdict="advanced",
        override_action="advance",
    )
    assert body == (
        "TAALI · Candidate advanced — recommendation overridden\n"
        "Role: Backend\n"
        "TAALI recommendation: Reject\n"
        "Final decision: Advanced\n"
        "Decision source: Recruiter\n"
        "Reason: The recruiter overrode the recommendation and approved the candidate for progression."
    )
    assert "override →" not in body


def test_compose_skip_advance_is_a_movement_override_without_assessment_copy(db):
    org, role, _, app = make_world(db, pre_screen=72.0)
    decision = _make_decision(db, org, role, app, "send_assessment")
    body = wds.compose_decision_summary_note(
        decision,
        app,
        actor=Actor(type="recruiter", user_id=123),
        verdict="skip_advanced",
        override_action="skip_assessment_advance",
        reason="No need for an assessment after recruiter review",
    )
    assert body.startswith("TAALI · Candidate advanced — recommendation overridden\n")
    assert "assessment" not in body.lower()
    assert "TAALI recommendation: Continue in Taali" in body
    assert "Final decision: Advanced" in body


def test_compose_note_uses_plain_verdict_not_raw_decision_type(db):
    """The Workable note must not leak the Taali-internal decision_type
    string (e.g. skip_assessment_reject) — show a plain verdict instead."""
    org, role, _, app = make_world(db, pre_screen=20.0)
    decision = _make_decision(db, org, role, app, decision_type="skip_assessment_reject")
    decision.evidence = {
        "role_fit_score": 42.0,
        "effective_threshold": 65.0,
    }
    body = wds.compose_decision_summary_note(
        decision,
        app,
        actor=Actor(type="recruiter", user_id=123),
        actor_name="Sam Patel",
        verdict="rejected",
        reason="Missing a required platform skill",
    )
    assert "skip_assessment_reject" not in body
    assert body == (
        "TAALI · Candidate rejected\n"
        "Role: Backend\n"
        "TAALI score used: 42/100\n"
        "Role threshold: 65/100\n"
        "Decision: Rejected by Sam Patel\n"
        "Reason: The candidate did not meet the role threshold."
    )


def test_compose_automatic_advance_has_no_recruiter_attribution(db):
    org, role, _, app = make_world(db)
    decision = _make_decision(db, org, role, app, "advance_to_interview")
    decision.evidence = {
        "role_fit_score": 78.0,
        "effective_threshold": 65.0,
    }

    body = wds.compose_decision_summary_note(
        decision,
        app,
        actor=Actor.system(),
        verdict="advanced",
        role_name="Backend Engineer",
        reason="Auto-approved per role.auto_advance (decision #4812)",
    )

    assert body == (
        "TAALI · Candidate advanced automatically\n"
        "Role: Backend Engineer\n"
        "TAALI score used: 78/100\n"
        "Role threshold: 65/100\n"
        "Decision source: Taali automatic policy\n"
        "Reason: The candidate met the role threshold and was approved for progression."
    )
    assert "Recruiter" not in body
    assert "Auto-approved per" not in body


def test_compose_post_assessment_movement_keeps_result_detail_in_taali(db):
    org, role, _, app = make_world(db)
    decision = _make_decision(db, org, role, app, "reject")
    decision.evidence = {
        "decision_stage": "assessment",
        "role_fit_score": 95.0,
        "effective_threshold": 50.0,
        "taali_score": 90.0,
        "assessment_score": 20.0,
    }

    body = wds.compose_decision_summary_note(
        decision,
        app,
        actor=Actor(type="recruiter", user_id=123),
        actor_name="Sam Patel",
        verdict="rejected",
        reason="Assessment result was below the internal floor",
    )

    assert "score" not in body.lower()
    assert "threshold" not in body.lower()
    assert "assessment" not in body.lower()
    assert "Decision: Rejected by Sam Patel" in body
    assert "Reason: The candidate did not satisfy the role's progression policy." in body


def test_compose_related_role_uses_frozen_related_score_and_threshold(db):
    org, owner_role, _, app = make_world(db)
    related_role = Role(
        organization_id=org.id,
        name="AI Platform Engineer",
        source="manual",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner_role.id,
    )
    db.add(related_role)
    db.flush()
    decision = _make_decision(
        db, org, related_role, app, "advance_to_interview"
    )
    decision.reasoning = (
        "Related-role score 72 meets the 56 threshold; advance the shared application."
    )
    decision.evidence = {
        "shared_ats_application": True,
        "related_role_id": related_role.id,
        "role_fit_score": 68.0,
        "taali_score": 72.0,
        "effective_threshold": 56.0,
    }
    app.taali_score_cache_100 = 63.0

    body = wds.compose_decision_summary_note(
        decision,
        app,
        actor=Actor(type="recruiter", user_id=123),
        actor_name="Sam Patel",
        role_name=related_role.name,
        verdict="advanced",
    )

    assert body == (
        "TAALI · Candidate advanced for a related role\n"
        "Role: AI Platform Engineer\n"
        "Related-role score used: 72/100\n"
        "Role threshold: 56/100\n"
        "Original application score: 63/100\n"
        "Decision: Advanced by Sam Patel\n"
        "Reason: The candidate met the related-role threshold and was approved for progression."
    )


def test_compose_related_role_post_assessment_keeps_all_result_detail_in_taali(db):
    org, owner_role, _, app = make_world(db)
    related_role = Role(
        organization_id=org.id,
        name="AI Platform Engineer",
        source="manual",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner_role.id,
    )
    db.add(related_role)
    db.flush()
    decision = _make_decision(db, org, related_role, app, "reject")
    decision.evidence = {
        "decision_stage": "assessment",
        "shared_ats_application": True,
        "related_role_id": related_role.id,
        "role_fit_score": 68.0,
        "assessment_score": 42.0,
        "taali_score": 61.0,
        "effective_threshold": 70.0,
    }
    app.taali_score_cache_100 = 83.0

    body = wds.compose_decision_summary_note(
        decision,
        app,
        actor=Actor(type="recruiter", user_id=123),
        actor_name="Sam Patel",
        role_name=related_role.name,
        verdict="rejected",
        reason="Assessment score 42/100 was below the 70/100 threshold",
    )

    assert body == (
        "TAALI · Candidate rejected for a related role\n"
        "Role: AI Platform Engineer\n"
        "Decision: Rejected by Sam Patel\n"
        "Reason: The candidate did not satisfy the role's progression policy."
    )
    assert "score" not in body.lower()
    assert "threshold" not in body.lower()
    assert "assessment" not in body.lower()
    assert "original application" not in body.lower()


def test_compose_never_includes_arbitrary_recruiter_note(db):
    org, role, _, app = make_world(db)
    decision = _make_decision(db, org, role, app, "advance_to_interview")
    decision.evidence = {
        "decision_stage": "full_scoring",
        "role_fit_score": 81.0,
        "effective_threshold": 65.0,
    }

    body = wds.compose_decision_summary_note(
        decision,
        app,
        actor=Actor(type="recruiter", user_id=123),
        actor_name="Sam Patel",
        role_name=role.name,
        verdict="advanced",
        reason="Strong referral with deep platform experience.",
    )

    assert "Strong referral" not in body
    assert "Recruiter note" not in body
    assert "Decision: Advanced by Sam Patel" in body
    assert "Reason: The candidate met the role threshold" in body


@pytest.mark.parametrize(
    "assessment_provenance",
    [
        {"decision_stage": "assessment"},
        {"assessment_score": 92.0},
        {"assessment_id": 123},
        {"task_id": 456},
        {"assessment_result": {"status": "completed"}},
        {"assessment": {"result": "passed"}},
        {"score_provenance": {"source": "assessment_result"}},
    ],
)
def test_compose_legacy_assessment_provenance_suppresses_all_scores(
    db, assessment_provenance
):
    org, owner_role, _, app = make_world(db)
    related_role = Role(
        organization_id=org.id,
        name="AI Platform Engineer",
        source="manual",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner_role.id,
    )
    db.add(related_role)
    db.flush()
    decision = _make_decision(db, org, related_role, app, "advance_to_interview")
    decision.evidence = {
        "shared_ats_application": True,
        "related_role_id": related_role.id,
        "role_fit_score": 88.0,
        "taali_score": 88.0,
        "effective_threshold": 65.0,
        **assessment_provenance,
    }
    app.taali_score_cache_100 = 77.0

    body = wds.compose_decision_summary_note(
        decision,
        app,
        actor=Actor(type="recruiter", user_id=123),
        actor_name="Sam Patel",
        role_name=related_role.name,
        verdict="advanced",
    )

    assert "score" not in body.casefold()
    assert "threshold" not in body.casefold()
    assert "original application" not in body.casefold()
    assert "Reason: The candidate was approved for progression." in body


# ---------------------------------------------------------------------------
# post_decision_summary_to_workable — orchestrator
# ---------------------------------------------------------------------------


def test_post_summary_skips_when_workable_not_connected(db):
    """No Workable connection → no-op, no share link minted, no API call."""
    org, role, _, app = make_world(db)
    decision = _make_decision(db, org, role, app)
    user = _make_user(db, org)

    with patch(
        "app.actions._workable_decision_summary.build_workable_adapter"
    ) as mock_factory:
        ok = wds.post_decision_summary_to_workable(
            db,
            Actor.recruiter(user),
            app=app,
            org=org,
            decision=decision,
            verdict="advanced",
        )

    assert ok is False
    assert mock_factory.called is False
    assert db.query(ShareLink).filter(ShareLink.application_id == app.id).count() == 0


def test_post_summary_skips_when_mvp_flag_disables_workable(db, monkeypatch):
    org, role, _, app = make_world(db)
    decision = _make_decision(db, org, role, app)
    user = _make_user(db, org)
    _enable_workable(db, org)
    app.workable_candidate_id = "wc-123"
    db.flush()

    monkeypatch.setattr(platform_config.settings, "MVP_DISABLE_WORKABLE", True)

    with patch(
        "app.actions._workable_decision_summary.build_workable_adapter"
    ) as mock_factory:
        ok = wds.post_decision_summary_to_workable(
            db,
            Actor.recruiter(user),
            app=app,
            org=org,
            decision=decision,
            verdict="advanced",
        )

    assert ok is False
    assert mock_factory.called is False


def test_post_summary_posts_canonical_workable_movement_note_without_link(
    db, monkeypatch
):
    """Workable gets canonical copy without minting a report share link."""
    org, role, _, app = make_world(db, pre_screen=85.0)
    app.taali_score_cache_100 = 85.0  # the note surfaces the canonical Taali score
    decision = _make_decision(db, org, role, app, "advance_to_interview")
    decision.evidence = {
        "role_fit_score": 85.0,
        "effective_threshold": 65.0,
    }
    user = _make_user(db, org)
    _enable_workable(db, org)
    app.workable_candidate_id = "wc-123"
    db.flush()
    monkeypatch.setattr(platform_config.settings, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(platform_config.settings, "FRONTEND_URL", "https://taali.ai")

    with patch(
        "app.actions._workable_decision_summary.build_workable_adapter"
    ) as mock_factory:
        adapter = mock_factory.return_value
        adapter.post_candidate_comment.return_value = {"success": True}

        ok = wds.post_decision_summary_to_workable(
            db,
            Actor.recruiter(user),
            app=app,
            org=org,
            decision=decision,
            verdict="advanced",
            reason="Strong referral",
            moved_to="Final interview",
        )

    assert ok is True
    assert adapter.post_candidate_comment.called
    kwargs = adapter.post_candidate_comment.call_args.kwargs
    assert kwargs["member_id"] == "member-1"
    body = kwargs["body"]
    assert body == (
        "TAALI · Candidate advanced\n"
        "Role: Backend\n"
        "Moved to: Final interview\n"
        "TAALI score used: 85/100\n"
        "Role threshold: 65/100\n"
        "Decision: Advanced by R\n"
        "Reason: The candidate met the role threshold and was approved for progression."
    )

    assert db.query(ShareLink).filter(ShareLink.application_id == app.id).count() == 0


def test_post_summary_trusts_exact_app_role_fallback_when_decision_role_missing(
    db, monkeypatch
):
    org, role, _, app = make_world(db)
    role.name = "Assessment Engineer"
    decision = _make_decision(db, org, role, app, "advance_to_interview")
    user = _make_user(db, org)
    _enable_workable(db, org)
    app.workable_candidate_id = "wc-123"
    db.flush()
    decision.role = None
    decision.role_id = None
    monkeypatch.setattr(platform_config.settings, "MVP_DISABLE_WORKABLE", False)

    with patch(
        "app.actions._workable_decision_summary.build_workable_adapter"
    ) as mock_factory:
        adapter = mock_factory.return_value
        adapter.post_candidate_comment.return_value = {"success": True}

        ok = wds.post_decision_summary_to_workable(
            db,
            Actor.recruiter(user),
            app=app,
            org=org,
            decision=decision,
            verdict="advanced",
        )

    assert ok is True
    kwargs = adapter.post_candidate_comment.call_args.kwargs
    assert "Role: Assessment Engineer" in kwargs["body"]
    assert kwargs["trusted_role_values"] == ("Assessment Engineer",)


def test_post_summary_records_failure_event_on_api_error(db, monkeypatch):
    org, role, _, app = make_world(db)
    decision = _make_decision(db, org, role, app)
    user = _make_user(db, org)
    _enable_workable(db, org)
    app.workable_candidate_id = "wc-123"
    db.flush()
    monkeypatch.setattr(platform_config.settings, "MVP_DISABLE_WORKABLE", False)

    with patch(
        "app.actions._workable_decision_summary.build_workable_adapter"
    ) as mock_factory:
        adapter = mock_factory.return_value
        adapter.post_candidate_comment.return_value = {
            "success": False,
            "error": "workable 502",
        }

        ok = wds.post_decision_summary_to_workable(
            db,
            Actor.recruiter(user),
            app=app,
            org=org,
            decision=decision,
            verdict="advanced",
        )

    assert ok is False


def test_post_summary_routes_to_bullhorn_provider_with_same_movement_note(
    db, monkeypatch
):
    """Bullhorn receives the same canonical movement note without a report link."""
    org, role, candidate, app = make_world(db, pre_screen=82.0)
    app.taali_score_cache_100 = 82.0
    decision = _make_decision(db, org, role, app, decision_type="advance_to_interview")
    user = _make_user(db, org)
    org.bullhorn_connected = True
    org.bullhorn_username = "api-user"
    org.bullhorn_client_id = "client-id"
    org.bullhorn_client_secret = "encrypted-secret"
    org.bullhorn_refresh_token = "encrypted-refresh"
    role.source = "bullhorn"
    role.bullhorn_job_order_id = "job-1"
    candidate.bullhorn_candidate_id = "candidate-7"
    app.bullhorn_job_submission_id = "submission-9"
    db.flush()
    monkeypatch.setattr(platform_config.settings, "BULLHORN_ENABLED", True)
    monkeypatch.setattr(platform_config.settings, "FRONTEND_URL", "https://taali.ai")

    with patch(
        "app.components.integrations.bullhorn.provider.BullhornProvider.post_note",
        return_value={"success": True, "code": "ok", "config": {"ats": "bullhorn"}},
    ) as post_note:
        ok = wds.post_decision_summary_to_workable(
            db,
            Actor.recruiter(user),
            app=app,
            org=org,
            decision=decision,
            verdict="advanced",
            reason="Strong systems fit",
        )

    assert ok is True
    kwargs = post_note.call_args.kwargs
    assert kwargs["candidate_id"] == "candidate-7"
    assert kwargs["body"].startswith(
        "TAALI · Candidate advanced\nRole: Backend\nTAALI score used: 82/100"
    )
    assert "Decision: Advanced by R" in kwargs["body"]
    assert "http" not in kwargs["body"]
    assert (
        db.query(ShareLink).filter(ShareLink.application_id == app.id).count()
        == 0
    )


def test_bullhorn_advance_unknown_failure_retries_without_leaking_secret(
    db, monkeypatch, caplog
):
    from app.components.integrations.bullhorn.provider import BullhornProvider
    from app.services.workable_actions_service import WorkableWritebackError

    org, role, candidate, app = make_world(db)
    user = _make_user(db, org)
    org.bullhorn_connected = True
    org.bullhorn_username = "api-user"
    org.bullhorn_client_id = "client-id"
    org.bullhorn_client_secret = "encrypted-secret"
    org.bullhorn_refresh_token = "encrypted-refresh"
    role.source = "bullhorn"
    role.bullhorn_job_order_id = "job-1"
    candidate.bullhorn_candidate_id = "candidate-7"
    app.bullhorn_job_submission_id = "submission-9"
    db.flush()
    monkeypatch.setattr(platform_config.settings, "BULLHORN_ENABLED", True)

    secret = "redis://:ADVANCE_SECRET@host"
    with patch.object(
        BullhornProvider,
        "move_application",
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(WorkableWritebackError) as raised:
            wds._try_bullhorn_advance(
                db,
                Actor.recruiter(user),
                app=app,
                org=org,
                reason="Advance",
            )

    assert raised.value.code == "unexpected"
    assert raised.value.retriable is True
    assert secret not in str(raised.value)
    assert secret not in caplog.text


def test_bullhorn_advance_exact_target_is_not_reported_as_movement(
    db, monkeypatch
):
    from app.components.integrations.bullhorn.provider import BullhornProvider
    from app.models.candidate_application_event import CandidateApplicationEvent

    org, role, candidate, app = make_world(db)
    user = _make_user(db, org)
    org.bullhorn_connected = True
    org.bullhorn_username = "api-user"
    org.bullhorn_client_id = "client-id"
    org.bullhorn_client_secret = "encrypted-secret"
    org.bullhorn_refresh_token = "encrypted-refresh"
    role.source = "bullhorn"
    candidate.bullhorn_candidate_id = "candidate-7"
    app.bullhorn_job_submission_id = "submission-9"
    db.flush()
    monkeypatch.setattr(platform_config.settings, "BULLHORN_ENABLED", True)

    with patch.object(
        BullhornProvider,
        "move_application",
        return_value={
            "success": True,
            "skipped": True,
            "code": "already_at_target",
            "config": {"remote_status": "Interview Scheduled"},
        },
    ):
        moved = wds._try_bullhorn_advance(
            db,
            Actor.recruiter(user),
            app=app,
            org=org,
            reason="Advance",
        )

    assert moved is False
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "bullhorn_moved",
        )
        .count()
        == 0
    )


# ---------------------------------------------------------------------------
# try_workable_advance — best-effort move
# ---------------------------------------------------------------------------


def test_try_advance_skips_silently_when_target_stage_empty(db, monkeypatch):
    """Recruiter didn't pick a stage → no Workable call, returns False."""
    org, role, _, app = make_world(db)
    user = _make_user(db, org)
    _enable_workable(db, org)
    app.workable_candidate_id = "wc-123"
    db.flush()
    monkeypatch.setattr(platform_config.settings, "MVP_DISABLE_WORKABLE", False)

    with patch(
        "app.services.workable_actions_service.move_candidate_in_workable"
    ) as mock_move:
        ok = wds.try_workable_advance(
            db,
            Actor.recruiter(user),
            app=app,
            org=org,
            role=role,
            target_stage=None,
            reason="r",
        )

    assert ok is False
    assert not mock_move.called


def test_try_advance_calls_move_with_recruiter_pick(db, monkeypatch):
    """Recruiter passes a target_stage → move_candidate_in_workable gets it."""
    org, role, _, app = make_world(db)
    user = _make_user(db, org)
    _enable_workable(db, org)
    app.workable_candidate_id = "wc-123"
    db.flush()
    monkeypatch.setattr(platform_config.settings, "MVP_DISABLE_WORKABLE", False)

    with patch(
        "app.services.workable_actions_service.move_candidate_in_workable"
    ) as mock_move:
        mock_move.return_value = {
            "success": True,
            "action": "move",
            "code": "ok",
            "config": {"actor_member_id": "member-1"},
        }
        ok = wds.try_workable_advance(
            db,
            Actor.recruiter(user),
            app=app,
            org=org,
            role=role,
            target_stage="Phone screen",
            reason="Advanced via override",
        )

    assert ok is True
    assert mock_move.called
    kwargs = mock_move.call_args.kwargs
    assert kwargs["candidate_id"] == "wc-123"
    assert kwargs["target_stage"] == "Phone screen"
    assert app.workable_stage == "Phone screen"


def test_try_advance_exact_custom_stage_alias_is_silent_noop(db, monkeypatch):
    """An id/slug/name alias of the current custom stage is not a movement."""
    org, role, _, app = make_world(db)
    user = _make_user(db, org)
    _enable_workable(db, org)
    role.workable_stages = [
        {
            "id": "stage-custom-42",
            "slug": "leadership-chat",
            "name": "Leadership Chat",
            "kind": "custom",
        }
    ]
    app.workable_candidate_id = "wc-123"
    app.workable_stage = "stage-custom-42"
    db.flush()
    monkeypatch.setattr(platform_config.settings, "MVP_DISABLE_WORKABLE", False)

    with patch(
        "app.services.workable_actions_service.move_candidate_in_workable"
    ) as mock_move:
        moved = wds.try_workable_advance(
            db,
            Actor.recruiter(user),
            app=app,
            org=org,
            role=role,
            target_stage="Leadership Chat",
            reason="Advance",
        )

    assert moved is False
    mock_move.assert_not_called()


def test_system_advance_uses_sole_cached_workable_interview_kind(db, monkeypatch):
    org, role, _, app = make_world(db)
    _enable_workable(db, org)
    org.workable_config = {
        **org.workable_config,
        "interview_stage_name": "",
    }
    role.workable_stages = [
        {"slug": "applied", "name": "Applied", "kind": "sourced"},
        {
            "slug": "final-interview",
            "name": "Final interview",
            "kind": "interview",
        },
    ]
    app.workable_candidate_id = "wc-123"
    db.flush()
    monkeypatch.setattr(platform_config.settings, "MVP_DISABLE_WORKABLE", False)

    with patch(
        "app.services.workable_actions_service.move_candidate_in_workable",
        return_value={
            "success": True,
            "action": "move",
            "code": "ok",
            "config": {"actor_member_id": "member-1"},
        },
    ) as move:
        ok = wds.try_workable_advance(
            db,
            Actor.system(),
            app=app,
            org=org,
            role=role,
            target_stage=None,
        )

    assert ok is True
    assert move.call_args.kwargs["target_stage"] == "final-interview"


def test_try_advance_skips_move_when_already_post_handover(db, monkeypatch):
    """Candidate already in a post-handover Workable stage (recruiter advanced
    them) → the move is a no-op; skip it (don't 422 / re-queue) and report no
    movement so the caller does not post a misleading summary."""
    org, role, _, app = make_world(db)
    user = _make_user(db, org)
    _enable_workable(db, org)
    app.workable_candidate_id = "wc-123"
    app.workable_stage = "Technical Interview"  # already past handover
    db.flush()
    monkeypatch.setattr(platform_config.settings, "MVP_DISABLE_WORKABLE", False)

    with patch(
        "app.services.workable_actions_service.move_candidate_in_workable"
    ) as mock_move:
        ok = wds.try_workable_advance(
            db,
            Actor.recruiter(user),
            app=app,
            org=org,
            role=role,
            target_stage="Phone screen",
            reason="Advanced via override",
        )

    assert ok is False
    assert not mock_move.called  # move skipped — no 422, no re-queue
    assert app.workable_stage == "Technical Interview"  # unchanged


# ---------------------------------------------------------------------------
# End-to-end wiring: approve_decision + override_decision invoke the helper
# ---------------------------------------------------------------------------


def test_approve_advance_to_interview_invokes_advance_and_summary(db, monkeypatch):
    """Approving an advance_to_interview decision should both move the
    candidate in Workable AND post the decision-summary note."""
    from app.actions import advance_stage as advance_stage_action
    from app.actions import approve_decision

    org, role, _, app = make_world(db, pre_screen=85.0)
    decision = _make_decision(db, org, role, app, decision_type="advance_to_interview")
    user = _make_user(db, org)
    _enable_workable(db, org)
    app.workable_candidate_id = "wc-123"
    db.flush()
    monkeypatch.setattr(platform_config.settings, "MVP_DISABLE_WORKABLE", False)

    # Stub the underlying pipeline transition so we don't need real
    # pipeline events in this unit-scope test.
    original_advance = advance_stage_action.run
    advance_stage_action.run = lambda *a, **kw: None

    try:
        with patch(
            "app.actions._decision_side_effects.try_workable_advance"
        ) as mock_advance, patch(
            "app.actions._decision_side_effects.post_decision_summary_to_workable"
        ) as mock_summary:
            mock_advance.return_value = True
            mock_summary.return_value = True
            approve_decision.run(
                db,
                Actor.recruiter(user),
                organization_id=int(org.id),
                decision_id=int(decision.id),
                note="Strong fit",
                workable_target_stage="Phone screen",
            )
    finally:
        advance_stage_action.run = original_advance

    assert mock_advance.called, "advance_to_interview must trigger Workable move"
    assert mock_summary.called, "advance_to_interview must post Workable comment"
    advance_kwargs = mock_advance.call_args.kwargs
    assert advance_kwargs["target_stage"] == "Phone screen"
    summary_kwargs = mock_summary.call_args.kwargs
    assert summary_kwargs["verdict"] == "advanced"
    assert summary_kwargs["reason"] == "Strong fit"


def test_approve_reject_decision_invokes_summary_without_advance(
    db, monkeypatch
):
    """Approving a reject decision should NOT move the candidate
    (disqualify path handles Workable on its own) but SHOULD post the
    Taali summary note for the audit trail."""
    from app.actions import approve_decision
    from app.actions import reject_application as reject_action

    org, role, _, app = make_world(db, pre_screen=42.0)
    decision = _make_decision(db, org, role, app, decision_type="reject")
    user = _make_user(db, org)
    _enable_workable(db, org)
    app.workable_candidate_id = "wc-123"
    db.flush()
    monkeypatch.setattr(platform_config.settings, "MVP_DISABLE_WORKABLE", False)

    original_reject = reject_action.run

    def _reject(*_args, **_kwargs):
        app.application_outcome = "rejected"
        return app

    reject_action.run = _reject

    try:
        with patch(
            "app.actions._decision_side_effects.try_workable_advance"
        ) as mock_advance, patch(
            "app.actions._decision_side_effects.post_decision_summary_to_workable"
        ) as mock_summary, patch(
            "app.actions.reject_application.notify_rejection",
            return_value=True,
        ) as notify_rejection:
            approve_decision.run(
                db,
                Actor.recruiter(user),
                organization_id=int(org.id),
                decision_id=int(decision.id),
                note="Missing must-have",
            )
    finally:
        reject_action.run = original_reject

    assert not mock_advance.called, "reject decision must NOT move in Workable"
    assert notify_rejection.call_args.kwargs["reason"] is None
    assert mock_summary.called
    assert mock_summary.call_args.kwargs["verdict"] == "rejected"
    assert mock_summary.call_args.kwargs["reason"] == "Missing must-have"


def test_advance_summary_requires_confirmed_ats_movement(db):
    from app.actions._decision_side_effects import apply_decision_side_effects

    org, role, _, app = make_world(db)
    decision = _make_decision(db, org, role, app, "advance_to_interview")

    with patch(
        "app.actions._decision_side_effects.try_workable_advance",
        return_value=False,
    ), patch(
        "app.actions._decision_side_effects.post_decision_summary_to_workable"
    ) as post_summary:
        apply_decision_side_effects(
            db,
            Actor.system(),
            decision=decision,
            app=app,
            org=org,
            role=role,
            disposition="approved",
        )

    post_summary.assert_not_called()


def test_confirmed_movement_checkpoint_precedes_optional_summary_and_graph(db):
    from app.actions._decision_side_effects import apply_decision_side_effects
    from app.candidate_graph import episode_outbox

    org, role, _, app = make_world(db)
    decision = _make_decision(db, org, role, app, "advance_to_interview")
    decision.status = "approved"
    db.flush()
    steps: list[str] = []
    real_commit = db.commit

    def checkpoint_commit():
        steps.append("commit")
        real_commit()

    with patch(
        "app.actions._decision_side_effects.try_workable_advance",
        side_effect=lambda *_args, **_kwargs: steps.append("move") or True,
    ), patch.object(db, "commit", side_effect=checkpoint_commit), patch(
        "app.actions._decision_side_effects.post_decision_summary_to_workable",
        side_effect=lambda *_args, **_kwargs: steps.append("summary") or True,
    ), patch.object(
        episode_outbox,
        "enqueue_recruiter_action",
        side_effect=lambda *_args, **_kwargs: steps.append("graph"),
    ):
        apply_decision_side_effects(
            db,
            Actor.system(),
            decision=decision,
            app=app,
            org=org,
            role=role,
            disposition="approved",
            workable_target_stage="Phone screen",
            commit_after_confirmed_movement=True,
        )

    assert steps == ["move", "commit", "summary", "graph"]


def test_direct_side_effect_path_keeps_caller_owned_transaction(db):
    from app.actions._decision_side_effects import apply_decision_side_effects
    from app.candidate_graph import episode_outbox

    org, role, _, app = make_world(db)
    decision = _make_decision(db, org, role, app, "advance_to_interview")

    with patch(
        "app.actions._decision_side_effects.try_workable_advance",
        return_value=True,
    ), patch(
        "app.actions._decision_side_effects.post_decision_summary_to_workable",
        return_value=True,
    ), patch.object(
        episode_outbox, "enqueue_recruiter_action", return_value=None
    ), patch.object(db, "commit") as commit:
        apply_decision_side_effects(
            db,
            Actor.system(),
            decision=decision,
            app=app,
            org=org,
            role=role,
            disposition="approved",
            workable_target_stage="Phone screen",
        )

    commit.assert_not_called()


def test_confirmed_destination_uses_workable_label_and_bullhorn_status(db):
    from app.actions._decision_side_effects import (
        _confirmed_movement_destination,
        _workable_stage_display_name,
    )
    from app.components.integrations.bullhorn.provider import BullhornProvider

    org, role, _, app = make_world(db)
    role.workable_stages = [
        {
            "id": "stage-42",
            "slug": "phone-screen",
            "name": "Phone screen",
            "kind": "interview",
        }
    ]
    assert _workable_stage_display_name(role, "phone-screen") == "Phone screen"
    assert _workable_stage_display_name(role, "stage-42") == "Phone screen"

    app.bullhorn_status = "Client Interview"
    provider = BullhornProvider(org, db)
    with patch(
        "app.components.integrations.resolver.resolve_application_ats_provider",
        return_value=provider,
    ):
        destination = _confirmed_movement_destination(
            db,
            app=app,
            org=org,
            role=role,
            requested_workable_stage="phone-screen",
        )

    assert destination == "Client Interview"


def test_reject_summary_requires_confirmed_ats_rejection(db):
    from app.actions._decision_side_effects import apply_decision_side_effects

    org, role, _, app = make_world(db)
    decision = _make_decision(db, org, role, app, "reject")

    with patch(
        "app.actions.reject_application.notify_rejection",
        return_value=False,
    ), patch(
        "app.actions._decision_side_effects.post_decision_summary_to_workable"
    ) as post_summary:
        apply_decision_side_effects(
            db,
            Actor.system(),
            decision=decision,
            app=app,
            org=org,
            role=role,
            disposition="approved",
            reject_notify=True,
        )

    post_summary.assert_not_called()


def test_override_skip_assessment_advance_invokes_advance_and_summary(
    db, monkeypatch
):
    """Skip & advance is the headline case — must move the candidate to
    the configured Workable stage AND post the summary note."""
    from app.actions import advance_stage as advance_stage_action
    from app.actions import override_decision

    org, role, _, app = make_world(db, pre_screen=78.0)
    decision = _make_decision(db, org, role, app, decision_type="send_assessment")
    user = _make_user(db, org)
    _enable_workable(db, org)
    app.workable_candidate_id = "wc-123"
    db.flush()
    monkeypatch.setattr(platform_config.settings, "MVP_DISABLE_WORKABLE", False)

    original_advance = advance_stage_action.run
    advance_stage_action.run = lambda *a, **kw: None

    try:
        with patch(
            "app.actions._decision_side_effects.try_workable_advance"
        ) as mock_advance, patch(
            "app.actions._decision_side_effects.post_decision_summary_to_workable"
        ) as mock_summary:
            override_decision.run(
                db,
                Actor.recruiter(user),
                organization_id=int(org.id),
                decision_id=int(decision.id),
                override_action="skip_assessment_advance",
                note="Internal referral",
                workable_target_stage="Phone screen",
            )
    finally:
        advance_stage_action.run = original_advance

    assert mock_advance.called, "skip & advance must call try_workable_advance"
    advance_kwargs = mock_advance.call_args.kwargs
    assert advance_kwargs["target_stage"] == "Phone screen"
    assert mock_summary.called
    kwargs = mock_summary.call_args.kwargs
    assert kwargs["verdict"] == "skip_advanced"
    assert kwargs["override_action"] == "skip_assessment_advance"
    assert kwargs["reason"] == "Internal referral"


def test_override_legacy_no_op_skips_summary(db, monkeypatch):
    """Legacy hold / manual_review overrides don't change candidate state,
    so they shouldn't generate a Workable activity entry."""
    from app.actions import override_decision

    org, role, _, app = make_world(db)
    decision = _make_decision(db, org, role, app)
    user = _make_user(db, org)
    _enable_workable(db, org)
    app.workable_candidate_id = "wc-123"
    db.flush()
    monkeypatch.setattr(platform_config.settings, "MVP_DISABLE_WORKABLE", False)

    with patch(
        "app.actions._decision_side_effects.post_decision_summary_to_workable"
    ) as mock_summary:
        override_decision.run(
            db,
            Actor.recruiter(user),
            organization_id=int(org.id),
            decision_id=int(decision.id),
            override_action="manual_review",
            note="Legacy",
        )

    assert not mock_summary.called


# ---------------------------------------------------------------------------
# Deferral contract: routes pass collect_side_effects so the slow Workable +
# graph work runs in a background task instead of on the request thread.
# ---------------------------------------------------------------------------


def test_approve_with_collect_side_effects_defers_inline_work(db, monkeypatch):
    """When the caller passes ``collect_side_effects``, run() must NOT run
    the side effects inline — it hands the route what it needs to enqueue
    the deferred task — while still committing the state change."""
    from app.actions import advance_stage as advance_stage_action
    from app.actions import approve_decision

    org, role, _, app = make_world(db, pre_screen=85.0)
    decision = _make_decision(db, org, role, app, decision_type="advance_to_interview")
    user = _make_user(db, org)
    _enable_workable(db, org)
    app.workable_candidate_id = "wc-123"
    db.flush()
    monkeypatch.setattr(platform_config.settings, "MVP_DISABLE_WORKABLE", False)

    original_advance = advance_stage_action.run
    advance_stage_action.run = lambda *a, **kw: None

    sink: dict = {}
    try:
        with patch(
            "app.actions.approve_decision.apply_decision_side_effects"
        ) as mock_apply:
            result = approve_decision.run(
                db,
                Actor.recruiter(user),
                organization_id=int(org.id),
                decision_id=int(decision.id),
                note="Strong fit",
                workable_target_stage="Phone screen",
                collect_side_effects=sink,
            )
    finally:
        advance_stage_action.run = original_advance

    assert not mock_apply.called, "deferred path must not run side effects inline"
    assert result.status == "approved", "state change still applies synchronously"
    assert sink == {"reject_notify": False}


def test_approve_reject_reports_reject_notify_for_deferral(db):
    """A reject approval reports reject_notify=True so the deferred task
    knows this resolution freshly rejected the candidate (gates the
    Workable disqualify / rejection email)."""
    from app.actions import approve_decision

    org, role, _, app = make_world(db, pre_screen=30.0)
    decision = _make_decision(db, org, role, app, decision_type="reject")
    user = _make_user(db, org)

    sink: dict = {}
    with patch(
        "app.actions.approve_decision.apply_decision_side_effects"
    ) as mock_apply:
        approve_decision.run(
            db,
            Actor.recruiter(user),
            organization_id=int(org.id),
            decision_id=int(decision.id),
            note="Missing must-have",
            collect_side_effects=sink,
        )

    assert not mock_apply.called
    assert sink.get("reject_notify") is True
    assert app.application_outcome == "rejected", "state change applies inline"


def test_apply_decision_side_effects_task_runs_on_committed_decision(db):
    """The deferred Celery task re-loads a committed, resolved decision and
    runs the shared side-effect applier without error (Workable disabled in
    tests, so the writeback is a no-op — this guards the task wiring:
    re-load, Actor reconstruction, apply call)."""
    from datetime import datetime, timezone

    from app.tasks.decision_tasks import apply_decision_side_effects

    org, role, _, app = make_world(db, pre_screen=85.0)
    decision = _make_decision(db, org, role, app, decision_type="advance_to_interview")
    user = _make_user(db, org)
    decision.status = "approved"
    decision.human_disposition = "approved"
    decision.resolved_by_user_id = user.id
    decision.resolved_at = datetime.now(timezone.utc)
    db.commit()

    result = apply_decision_side_effects.apply(
        args=[int(decision.id)],
        kwargs={"workable_target_stage": None, "reject_notify": False},
    ).result

    assert result["status"] == "ok"
    assert result["decision_id"] == int(decision.id)
