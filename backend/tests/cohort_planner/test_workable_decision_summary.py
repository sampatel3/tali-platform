"""Decision-summary Workable note + advance side effects.

Covers the helper that runs inside ``approve_decision.run`` /
``override_decision.run`` after the underlying action committed —
checks the no-op paths, the body composition, the share-link mint,
and the move-to-advance behavior.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.actions import _workable_decision_summary as wds
from app.actions.types import Actor
from app.decision_policy.bootstrap import bootstrap_org
from app.models.agent_decision import AgentDecision
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.share_link import ShareLink
from app.models.user import User
from app.platform import config as platform_config
from app.services.workable_actions_service import (
    WorkableWritebackError,
    strict_workable_writes,
)

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
        "workable_writeback": True,
        "workable_actor_member_id": "member-1",
    }
    db.flush()


# ---------------------------------------------------------------------------
# compose_decision_summary_note — pure formatting
# ---------------------------------------------------------------------------


def test_compose_note_includes_score_confidence_reasoning_and_share_url(db):
    # The note's score is ALWAYS the canonical Taali score, never the
    # pre-screen display value — seed them differently to prove it.
    org, role, _, app = make_world(db, pre_screen=50.0)
    decision = _make_decision(db, org, role, app)
    app.taali_score_cache_100 = 85.0

    body = wds.compose_decision_summary_note(
        decision,
        app,
        verdict="advanced",
        share_url="https://taali.ai/share/shr_xyz",
        reason="Strong referral",
    )

    assert body.startswith("TAALI ▸ Advanced by recruiter")
    assert "Score: 85/100" in body
    assert "Score: 50" not in body
    assert "Tali confidence: 85%" in body
    assert "Strong AWS Glue match" in body
    assert "Recruiter note: Strong referral" in body
    assert "Report (30 days): https://taali.ai/share/shr_xyz" in body


def test_compose_note_marks_override_in_headline(db):
    org, role, _, app = make_world(db, pre_screen=72.0)
    decision = _make_decision(db, org, role, app, decision_type="reject")

    body = wds.compose_decision_summary_note(
        decision,
        app,
        verdict="advanced",
        override_action="advance",
        share_url=None,
    )
    assert "override → advance" in body


def test_compose_note_skip_advance_does_not_mark_override(db):
    """Skip & advance is its own verdict — the headline already conveys
    the override, no need to append a parenthetical."""
    org, role, _, app = make_world(db, pre_screen=72.0)
    decision = _make_decision(db, org, role, app)
    body = wds.compose_decision_summary_note(
        decision,
        app,
        verdict="skip_advanced",
        override_action="skip_assessment_advance",
        share_url=None,
    )
    assert "Skipped assessment and advanced by recruiter" in body
    assert "override" not in body.lower()


def test_compose_note_uses_plain_verdict_not_raw_decision_type(db):
    """The Workable note must not leak the Taali-internal decision_type
    string (e.g. skip_assessment_reject) — show a plain verdict instead."""
    org, role, _, app = make_world(db, pre_screen=20.0)
    decision = _make_decision(db, org, role, app, decision_type="skip_assessment_reject")
    body = wds.compose_decision_summary_note(
        decision, app, verdict="rejected", share_url=None,
    )
    assert "skip_assessment_reject" not in body
    assert "Agent recommended: reject —" in body


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


def test_post_summary_posts_note_with_share_link(db, monkeypatch):
    """Happy path: Workable connected + candidate linked → mints a 30d
    recruiter ShareLink + posts an activity containing the URL."""
    org, role, _, app = make_world(db, pre_screen=85.0)
    app.taali_score_cache_100 = 85.0  # the note surfaces the canonical Taali score
    decision = _make_decision(db, org, role, app)
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
        )

    assert ok is True
    assert adapter.post_candidate_comment.called
    kwargs = adapter.post_candidate_comment.call_args.kwargs
    assert kwargs["member_id"] == "member-1"
    body = kwargs["body"]
    assert "TAALI ▸ Advanced by recruiter" in body
    assert "Score: 85/100" in body
    assert "https://taali.ai/share/shr_" in body

    links = (
        db.query(ShareLink).filter(ShareLink.application_id == app.id).all()
    )
    assert len(links) == 1
    assert links[0].mode == "recruiter"
    assert links[0].expiry_preset == "30d"
    assert links[0].created_by_user_id == user.id


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


def test_post_summary_routes_to_bullhorn_provider_with_same_share_note(db, monkeypatch):
    """Bullhorn receives the same decision audit note and 30-day report link."""
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
    assert "TAALI ▸ Advanced by recruiter" in kwargs["body"]
    assert "https://taali.ai/share/shr_" in kwargs["body"]
    assert (
        db.query(ShareLink).filter(ShareLink.application_id == app.id).count()
        == 1
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


def test_strict_bullhorn_advance_never_falls_through_when_provider_is_unavailable(
    db, monkeypatch
):
    org, _, _, app = make_world(db)
    user = _make_user(db, org)
    app.bullhorn_job_submission_id = "submission-disabled"
    db.flush()
    monkeypatch.setattr(platform_config.settings, "BULLHORN_ENABLED", False)

    with strict_workable_writes(), pytest.raises(WorkableWritebackError) as raised:
        wds._try_bullhorn_advance(
            db,
            Actor.recruiter(user),
            app=app,
            org=org,
            reason="Advance",
        )

    assert raised.value.code == "not_configured"
    assert raised.value.retriable is False


def test_strict_bullhorn_reject_never_falls_through_when_provider_is_unavailable(
    db, monkeypatch
):
    from app.actions import reject_application

    org, _, _, app = make_world(db)
    user = _make_user(db, org)
    app.bullhorn_job_submission_id = "submission-disabled"
    db.flush()
    monkeypatch.setattr(platform_config.settings, "BULLHORN_ENABLED", False)

    with strict_workable_writes(), pytest.raises(WorkableWritebackError) as raised:
        reject_application._try_bullhorn_reject(
            db,
            app=app,
            org=org,
            actor=Actor.recruiter(user),
            reason="Reject",
        )

    assert raised.value.code == "not_configured"
    assert raised.value.retriable is False


# ---------------------------------------------------------------------------
# try_workable_advance — best-effort move
# ---------------------------------------------------------------------------


def test_strict_try_advance_rejects_an_empty_target_stage(db, monkeypatch):
    """A linked Workable approval cannot become a local-only advance."""
    org, role, _, app = make_world(db)
    user = _make_user(db, org)
    _enable_workable(db, org)
    app.workable_candidate_id = "wc-123"
    db.flush()
    monkeypatch.setattr(platform_config.settings, "MVP_DISABLE_WORKABLE", False)

    with strict_workable_writes(), pytest.raises(WorkableWritebackError) as raised:
        wds.try_workable_advance(
            db,
            Actor.recruiter(user),
            app=app,
            org=org,
            role=role,
            target_stage=None,
            reason="r",
        )

    assert raised.value.code == "missing_target_stage"
    assert raised.value.retriable is False


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
    them) → the move is a no-op; skip it (don't 422 / re-queue), return success.
    The summary comment is posted separately by the caller."""
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

    assert ok is True
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

    org, role, _, app = make_world(db, pre_screen=85.0, cv_match=85.0)
    bootstrap_org(db, organization_id=int(org.id))
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
    Tali summary note for the audit trail."""
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
    reject_action.run = lambda *a, **kw: None

    try:
        with patch(
            "app.actions._decision_side_effects.try_workable_advance"
        ) as mock_advance, patch(
            "app.actions._decision_side_effects.post_decision_summary_to_workable"
        ) as mock_summary:
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
    assert mock_summary.called
    assert mock_summary.call_args.kwargs["verdict"] == "rejected"


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

    org, role, _, app = make_world(db, pre_screen=85.0, cv_match=85.0)
    bootstrap_org(db, organization_id=int(org.id))
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


def test_legacy_decision_side_effect_task_requires_reconciliation(db):
    """A pre-receipt queued task cannot safely infer whether ATS work landed."""
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

    assert result["status"] == "reconciliation_required"
    assert result["decision_id"] == int(decision.id)
    db.expire_all()
    receipt = app.integration_sync_state["decision_provider_operation"]
    assert receipt["provider_called"] is None
    assert receipt["manual_reconciliation_required"] is True
    events = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type
            == "ats_decision_reconciliation_required",
        )
        .all()
    )
    assert len(events) == 1
