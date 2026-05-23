"""Decision-summary Workable note + advance side effects.

Covers the helper that runs inside ``approve_decision.run`` /
``override_decision.run`` after the underlying action committed —
checks the no-op paths, the body composition, the share-link mint,
and the move-to-advance behavior.
"""

from __future__ import annotations

from unittest.mock import patch

from app.actions import _workable_decision_summary as wds
from app.actions.types import Actor
from app.models.agent_decision import AgentDecision
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


def test_compose_note_includes_score_confidence_reasoning_and_share_url(db):
    org, role, _, app = make_world(db, pre_screen=85.0)
    decision = _make_decision(db, org, role, app)

    body = wds.compose_decision_summary_note(
        decision,
        app,
        verdict="advanced",
        share_url="https://taali.ai/share/shr_xyz",
        reason="Strong referral",
    )

    assert body.startswith("TAALI ▸ Advanced by recruiter")
    assert "Score: 85/100" in body
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
        adapter.post_candidate_activity.return_value = {"success": True}

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
    assert adapter.post_candidate_activity.called
    kwargs = adapter.post_candidate_activity.call_args.kwargs
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
        adapter.post_candidate_activity.return_value = {
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
            "app.actions.approve_decision.try_workable_advance"
        ) as mock_advance, patch(
            "app.actions.approve_decision.post_decision_summary_to_workable"
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
            "app.actions.approve_decision.try_workable_advance"
        ) as mock_advance, patch(
            "app.actions.approve_decision.post_decision_summary_to_workable"
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
            "app.actions.override_decision.try_workable_advance"
        ) as mock_advance, patch(
            "app.actions.override_decision.post_decision_summary_to_workable"
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
        "app.actions.override_decision.post_decision_summary_to_workable"
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
