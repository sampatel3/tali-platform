"""Focused tests for the Agent Chat decision-command boundary.

The underlying Decision Hub workflows have their own route/action suites.  The
tests here lock down what is unique to chat: role/org isolation, the model-safe
alternative vocabulary, compact delegation, and snooze-aware listing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.agent_chat import decision_commands as commands
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.decision_feedback import DecisionFeedback
from app.models.organization import Organization
from app.models.role import Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from app.services.decision_staleness import StalenessReport


def _context(db, *, workable: bool = False):
    org = Organization(name="Decision Command Org", slug=f"decision-command-{id(db)}")
    db.add(org)
    db.flush()
    user = User(
        email=f"decision-command-{id(db)}@test.local",
        hashed_password="x",
        full_name="Recruiter",
        organization_id=int(org.id),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    role = Role(
        organization_id=int(org.id),
        name="Platform Engineer",
        source="manual",
        workable_job_id="workable-role" if workable else None,
    )
    other_role = Role(
        organization_id=int(org.id),
        name="Data Engineer",
        source="manual",
    )
    db.add_all([user, role, other_role])
    db.flush()
    return org, user, role, other_role


def _decision(
    db,
    org: Organization,
    role: Role,
    *,
    label: str,
    decision_type: str = "send_assessment",
    status: str = "pending",
    snoozed_until: datetime | None = None,
) -> AgentDecision:
    candidate = Candidate(
        organization_id=int(org.id),
        email=f"{label}@test.local",
        full_name=f"Candidate {label}",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(org.id),
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="system",
        application_outcome="open",
        source="manual",
        cv_text="Python and distributed systems",
        pre_screen_score_100=82,
    )
    db.add(application)
    db.flush()
    decision = AgentDecision(
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(application.id),
        decision_type=decision_type,
        recommendation=decision_type,
        status=status,
        reasoning=f"Reason for {label}",
        confidence=0.87,
        model_version="test-model",
        prompt_version="test-prompt",
        idempotency_key=f"decision-command:{label}",
        snoozed_until=snoozed_until,
    )
    db.add(decision)
    db.flush()
    return decision


@patch.object(commands.decision_staleness, "evaluate")
def test_list_pending_is_role_scoped_and_snooze_aware(staleness, db):
    org, user, role, other_role = _context(db)
    visible = _decision(db, org, role, label="visible")
    snoozed = _decision(
        db,
        org,
        role,
        label="snoozed",
        decision_type="reject",
        snoozed_until=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    _decision(db, org, role, label="processing", status="processing")
    _decision(db, org, other_role, label="other-role")
    db.commit()
    staleness.return_value = StalenessReport(is_stale=False)

    result = commands.list_pending_decisions(db, role, user)

    assert result["count"] == 1
    assert [row["decision_id"] for row in result["decisions"]] == [int(visible.id)]
    row = result["decisions"][0]
    assert row["candidate_name"] == "Candidate visible"
    assert row["supported_alternatives"] == ["reject", "skip_assessment_advance"]
    assert row["can_approve"] is True
    assert row["role_family"] == {
        "owner": {"id": int(role.id), "name": "Platform Engineer"},
        "related": [],
    }

    including_snoozed = commands.list_pending_decisions(
        db, role, user, include_snoozed=True
    )
    assert including_snoozed["count"] == 2
    assert {row["decision_id"] for row in including_snoozed["decisions"]} == {
        int(visible.id),
        int(snoozed.id),
    }


@patch.object(commands.decision_staleness, "evaluate")
def test_pending_snapshots_include_complete_org_scoped_role_family(staleness, db):
    org, user, owner, _other_role = _context(db)
    related_z = Role(
        organization_id=int(org.id),
        name="Zulu Platform Engineer",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=int(owner.id),
    )
    related_a = Role(
        organization_id=int(org.id),
        name="AI Platform Engineer",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=int(owner.id),
    )
    foreign_org = Organization(
        name="Foreign Decision Command Org",
        slug=f"foreign-decision-command-{id(db)}",
    )
    db.add_all([related_z, related_a, foreign_org])
    db.flush()
    # A malformed cross-tenant sibling must never enter a recruiter-facing
    # preview, even though the self-referential foreign key itself is valid.
    foreign_related = Role(
        organization_id=int(foreign_org.id),
        name="Private Foreign Alternative",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=int(owner.id),
    )
    db.add(foreign_related)
    owner_decision = _decision(db, org, owner, label="owner-family", decision_type="reject")
    related_decision = _decision(
        db,
        org,
        related_a,
        label="related-family",
        decision_type="reject",
    )
    related_application = db.get(
        CandidateApplication,
        int(related_decision.application_id),
    )
    db.add(
        SisterRoleEvaluation(
            organization_id=int(org.id),
            role_id=int(related_a.id),
            candidate_id=int(related_application.candidate_id),
            source_application_id=int(related_application.id),
            status="done",
            pipeline_stage="review",
            application_outcome="open",
            membership_source="direct_addition",
            spec_fingerprint="related-family-spec",
            role_fit_score=80,
        )
    )
    db.commit()
    staleness.return_value = StalenessReport(is_stale=False)

    expected_family = {
        "owner": {"id": int(owner.id), "name": "Platform Engineer"},
        "related": [
            {"id": int(related_a.id), "name": "AI Platform Engineer"},
            {"id": int(related_z.id), "name": "Zulu Platform Engineer"},
        ],
    }
    owner_snapshot = commands.get_pending_decision(
        db,
        owner,
        user,
        int(owner_decision.id),
    )
    related_snapshot = commands.get_pending_decision(
        db,
        related_a,
        user,
        int(related_decision.id),
    )

    assert owner_snapshot["role_family"] == expected_family
    assert related_snapshot["role_family"] == expected_family
    assert "Private Foreign Alternative" not in str(owner_snapshot)
    assert "Private Foreign Alternative" not in str(related_snapshot)


@patch.object(commands.decision_staleness, "evaluate")
def test_get_pending_matches_list_projection_including_snoozed(staleness, db):
    org, user, role, _other_role = _context(db, workable=True)
    decision = _decision(
        db,
        org,
        role,
        label="preview",
        decision_type="advance_to_interview",
        snoozed_until=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.commit()
    staleness.return_value = StalenessReport(
        is_stale=True,
        reasons=["criteria_changed"],
        summary="Role criteria changed",
    )

    preview = commands.get_pending_decision(db, role, user, int(decision.id))
    listed = commands.list_pending_decisions(
        db, role, user, include_snoozed=True
    )["decisions"][0]

    assert preview == listed
    assert preview["candidate_name"] == "Candidate preview"
    assert preview["is_stale"] is True
    assert preview["staleness_reasons"] == ["criteria_changed"]
    assert preview["supported_alternatives"] == ["send_assessment", "reject"]
    assert preview["approval_requires_workable_stage"] is True


def test_get_pending_enforces_role_scope_and_pending_state(db):
    org, user, role, other_role = _context(db)
    wrong_role = _decision(db, org, other_role, label="preview-wrong-role")
    processing = _decision(
        db, org, role, label="preview-processing", status="processing"
    )
    db.commit()

    with pytest.raises(commands.DecisionCommandError) as scoped:
        commands.get_pending_decision(db, role, user, int(wrong_role.id))
    assert scoped.value.code == "decision_not_found"

    with pytest.raises(commands.DecisionCommandError) as not_pending:
        commands.get_pending_decision(db, role, user, int(processing.id))
    assert not_pending.value.code == "decision_not_pending"


@patch.object(commands, "related_decision_staleness")
def test_related_pending_decisions_use_live_independent_membership(
    related_staleness,
    db,
):
    """The ATS/source application is evidence, never related-role membership."""

    org, user, owner, _other_role = _context(db)
    owner.workable_job_id = "owner-workable-job"
    related = Role(
        organization_id=int(org.id),
        name="Independent AI Platform Engineer",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=int(owner.id),
    )
    db.add(related)
    db.flush()

    source_decision = _decision(
        db,
        org,
        owner,
        label="related-member",
        decision_type="advance_to_interview",
    )
    source_app = db.get(CandidateApplication, int(source_decision.application_id))
    source_decision.status = "resolved"
    membership = SisterRoleEvaluation(
        organization_id=int(org.id),
        role_id=int(related.id),
        candidate_id=int(source_app.candidate_id),
        source_application_id=int(source_app.id),
        ats_application_id=int(source_app.id),
        status="done",
        pipeline_stage="assessment",
        application_outcome="open",
        membership_source="initial_snapshot",
        spec_fingerprint="related-spec",
        role_fit_score=91,
        details={"summary": "Related-role evidence"},
    )
    db.add(membership)
    db.flush()
    related_decision = AgentDecision(
        organization_id=int(org.id),
        role_id=int(related.id),
        application_id=int(source_app.id),
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status="pending",
        reasoning="Meets this role's independent threshold",
        confidence=0.91,
        model_version="test-model",
        prompt_version="test-prompt",
        idempotency_key="decision-command:related-role-member",
    )
    db.add(related_decision)

    # A decision row alone must not smuggle an owner candidate into this pool.
    not_member = _decision(db, org, owner, label="not-related-member")
    not_member.status = "resolved"
    invisible = AgentDecision(
        organization_id=int(org.id),
        role_id=int(related.id),
        application_id=int(not_member.application_id),
        decision_type="reject",
        recommendation="reject",
        status="pending",
        reasoning="Must be hidden without membership",
        confidence=0.5,
        model_version="test-model",
        prompt_version="test-prompt",
        idempotency_key="decision-command:related-role-nonmember",
    )
    db.add(invisible)
    # Related membership remains authoritative even if evidence transport is
    # soft-deleted from its original role.
    source_app.deleted_at = datetime.now(timezone.utc)
    db.commit()
    related_staleness.return_value = StalenessReport(is_stale=False)

    listing = commands.list_pending_decisions(db, related, user)
    preview = commands.get_pending_decision(
        db,
        related,
        user,
        int(related_decision.id),
    )

    assert listing["count"] == 1
    assert [row["decision_id"] for row in listing["decisions"]] == [
        int(related_decision.id)
    ]
    assert preview == listing["decisions"][0]
    assert preview["candidate_name"] == "Candidate related-member"
    assert preview["approval_requires_workable_stage"] is True
    assert related_staleness.call_args.args[2].id == membership.id

    membership.deleted_at = datetime.now(timezone.utc)
    db.commit()
    assert commands.list_pending_decisions(db, related, user)["count"] == 0
    with pytest.raises(commands.DecisionCommandError) as removed:
        commands.get_pending_decision(
            db,
            related,
            user,
            int(related_decision.id),
        )
    assert removed.value.code == "decision_subject_not_found"


def test_list_rejects_user_from_another_organization(db):
    org, _user, role, _other_role = _context(db)
    other_org = Organization(name="Other Org", slug=f"other-{id(db)}")
    db.add(other_org)
    db.flush()
    outsider = User(
        email=f"outsider-{id(db)}@test.local",
        hashed_password="x",
        full_name="Outsider",
        organization_id=int(other_org.id),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(outsider)
    db.flush()

    with pytest.raises(commands.DecisionCommandError) as exc:
        commands.list_pending_decisions(db, role, outsider)
    assert exc.value.code == "scope_mismatch"


@patch("app.domains.agentic.routes.approve")
def test_approve_delegates_to_canonical_route_without_force(approve_route, db):
    org, user, role, _other_role = _context(db, workable=True)
    decision = _decision(
        db,
        org,
        role,
        label="advance",
        decision_type="advance_to_interview",
    )
    db.commit()
    approve_route.return_value = {
        "decision_id": int(decision.id),
        "accepted": True,
    }

    with pytest.raises(commands.DecisionCommandError) as missing_stage:
        commands.approve_decision(db, role, user, decision_id=int(decision.id))
    assert missing_stage.value.code == "workable_stage_required"
    approve_route.assert_not_called()

    result = commands.approve_decision(
        db,
        role,
        user,
        decision_id=int(decision.id),
        note="Strong evidence",
        workable_target_stage="Technical Interview",
    )
    assert result["ok"] is True
    assert result["operation"] == "approve_decision"
    assert result["decision_id"] == int(decision.id)
    assert result["accepted"] is True
    kwargs = approve_route.call_args.kwargs
    assert kwargs["force"] is False
    assert kwargs["current_user"] is user
    assert kwargs["body"].note == "Strong evidence"
    assert kwargs["body"].workable_target_stage == "Technical Interview"


@patch("app.domains.agentic.routes.approve")
def test_approve_translates_stale_route_error(approve_route, db):
    org, user, role, _other_role = _context(db)
    decision = _decision(db, org, role, label="stale", decision_type="reject")
    db.commit()
    approve_route.side_effect = HTTPException(
        status_code=409,
        detail={
            "code": "decision_stale",
            "message": "Inputs changed; re-evaluate first.",
            "reasons": ["criteria_changed"],
        },
    )

    with pytest.raises(commands.DecisionCommandError) as exc:
        commands.approve_decision(db, role, user, decision_id=int(decision.id))
    assert exc.value.code == "decision_stale"
    assert exc.value.details["reasons"] == ["criteria_changed"]


@patch("app.domains.agentic.routes.approve")
def test_mutation_cannot_address_decision_from_another_role(approve_route, db):
    org, user, role, other_role = _context(db)
    decision = _decision(db, org, other_role, label="wrong-role", decision_type="reject")
    db.commit()

    with pytest.raises(commands.DecisionCommandError) as exc:
        commands.approve_decision(db, role, user, decision_id=int(decision.id))
    assert exc.value.code == "decision_not_found"
    approve_route.assert_not_called()


@patch("app.domains.agentic.routes.override")
def test_override_enforces_ui_alternatives_and_reason(override_route, db):
    org, user, role, _other_role = _context(db)
    decision = _decision(db, org, role, label="send", decision_type="send_assessment")
    db.commit()

    with pytest.raises(commands.DecisionCommandError) as invalid_action:
        commands.override_decision(
            db,
            role,
            user,
            decision_id=int(decision.id),
            alternative="advance",
            note="Interviewed already",
        )
    assert invalid_action.value.code == "unsupported_decision_alternative"

    with pytest.raises(commands.DecisionCommandError) as missing_reason:
        commands.override_decision(
            db,
            role,
            user,
            decision_id=int(decision.id),
            alternative="reject",
            note="   ",
        )
    assert missing_reason.value.code == "override_reason_required"
    override_route.assert_not_called()

    override_route.return_value = {
        "id": int(decision.id),
        "role_id": int(role.id),
        "application_id": int(decision.application_id),
        "decision_type": "send_assessment",
        "status": "processing",
        "override_action": "reject",
    }
    result = commands.override_decision(
        db,
        role,
        user,
        decision_id=int(decision.id),
        alternative="reject",
        note="Missing mandatory certification",
    )
    assert result["ok"] is True
    body = override_route.call_args.kwargs["body"]
    assert body.override_action == "reject"
    assert body.note == "Missing mandatory certification"


def test_snooze_uses_canonical_window_and_hides_from_default_list(db):
    org, user, role, _other_role = _context(db)
    decision = _decision(db, org, role, label="nap", decision_type="reject")
    db.commit()

    before = datetime.now(timezone.utc)
    result = commands.snooze_decision(
        db, role, user, decision_id=int(decision.id)
    )
    after = datetime.now(timezone.utc)

    assert result["ok"] is True
    db.refresh(decision)
    snoozed_until = decision.snoozed_until
    if snoozed_until.tzinfo is None:  # SQLite drops tz info
        snoozed_until = snoozed_until.replace(tzinfo=timezone.utc)
    assert before + timedelta(minutes=59) <= snoozed_until
    assert snoozed_until <= after + timedelta(hours=1, minutes=1)
    assert commands.list_pending_decisions(db, role, user)["count"] == 0
    assert commands.list_pending_decisions(
        db, role, user, include_snoozed=True
    )["count"] == 1


@patch("app.domains.agentic.routes.re_evaluate")
def test_re_evaluate_is_scoped_then_delegates(re_evaluate_route, db):
    org, user, role, other_role = _context(db)
    wrong_role = _decision(db, org, other_role, label="reeval-wrong")
    decision = _decision(db, org, role, label="reeval")
    db.commit()

    with pytest.raises(commands.DecisionCommandError) as exc:
        commands.re_evaluate_decision(
            db, role, user, decision_id=int(wrong_role.id)
        )
    assert exc.value.code == "decision_not_found"
    re_evaluate_route.assert_not_called()

    re_evaluate_route.return_value = {
        "decision_id": int(decision.id),
        "role_id": int(role.id),
        "application_id": int(decision.application_id),
        "superseded": 1,
        "queued": True,
        "task_id": "task-123",
        "detail": None,
    }
    result = commands.re_evaluate_decision(
        db, role, user, decision_id=int(decision.id)
    )
    assert result == {
        "ok": True,
        "operation": "re_evaluate_decision",
        "decision_id": int(decision.id),
        "role_id": int(role.id),
        "application_id": int(decision.application_id),
        "superseded": 1,
        "queued": True,
        "task_id": "task-123",
        "detail": None,
    }


def test_escalation_is_listed_but_not_executable(db):
    org, user, role, _other_role = _context(db)
    decision = _decision(
        db,
        org,
        role,
        label="escalation",
        decision_type="escalate_low_confidence",
    )
    db.commit()

    listing = commands.list_pending_decisions(db, role, user)
    row = listing["decisions"][0]
    assert row["can_approve"] is False
    assert row["supported_alternatives"] == []

    with pytest.raises(commands.DecisionCommandError) as exc:
        commands.approve_decision(db, role, user, decision_id=int(decision.id))
    assert exc.value.code == "decision_not_approvable"


def test_teach_role_scope_persists_compact_feedback_and_allows_reteach(db):
    org, user, role, _other_role = _context(db)
    decision = _decision(db, org, role, label="teach-role", decision_type="reject")
    db.commit()

    result = commands.teach_decision(
        db,
        role,
        user,
        decision_id=int(decision.id),
        failure_mode="rubric_mismatch",
        correction_text="The evidence supports a borderline pass, not rejection.",
        scope="role",
        # Policy attribution deliberately avoids the derived exemplar write;
        # this test is about the command/feedback contract, not that index.
        attributed_to="policy_combination",
        direction="under",
    )

    assert result["decision_status"] == "reverted_for_feedback"
    assert result["scope"] == "role"
    assert result["cosign_required"] is False
    feedback = db.get(DecisionFeedback, result["feedback_id"])
    assert feedback.role_id == int(role.id)
    assert feedback.graph_write_hints is None
    assert feedback.attributed_to == "policy_combination"
    assert feedback.direction == "under"

    # The domain intentionally accepts a corrected follow-up on a decision
    # already in reverted_for_feedback; Chat preserves that capability.
    second = commands.teach_decision(
        db,
        role,
        user,
        decision_id=int(decision.id),
        failure_mode="other",
        correction_text="More specifically: value the production migration evidence.",
        scope="decision",
    )
    assert second["feedback_id"] != result["feedback_id"]
    assert second["decision_status"] == "reverted_for_feedback"
    second_feedback = db.get(DecisionFeedback, second["feedback_id"])
    assert second_feedback.role_id == int(role.id)


def test_teach_org_scope_is_roleless_and_surfaces_cosign(db):
    org, user, role, _other_role = _context(db)
    decision = _decision(db, org, role, label="teach-org", decision_type="reject")
    db.commit()

    result = commands.teach_decision(
        db,
        role,
        user,
        decision_id=int(decision.id),
        failure_mode="policy_violation",
        correction_text="Apply this compliance rule throughout the organization.",
        scope="org",
        attributed_to="policy_combination",
        direction="over",
    )

    assert result["cosign_required"] is True
    feedback = db.get(DecisionFeedback, result["feedback_id"])
    assert feedback.scope == "org"
    assert feedback.role_id is None
    assert feedback.cosign_required is True


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"failure_mode": "RUBRIC_MISMATCH"}, "unsupported_failure_mode"),
        ({"scope": "workspace"}, "unsupported_scope"),
        ({"attributed_to": "interview_agent"}, "unsupported_attributed_to"),
        ({"direction": "higher"}, "unsupported_direction"),
        ({"correction_text": "   "}, "correction_required"),
    ],
)
def test_teach_validates_exact_taxonomy_and_nonempty_correction(db, changes, code):
    org, user, role, _other_role = _context(db)
    decision = _decision(db, org, role, label=f"teach-invalid-{code}")
    db.commit()
    values = {
        "failure_mode": "other",
        "correction_text": "Use the stronger evidence.",
        "scope": "role",
        "attributed_to": None,
        "direction": None,
        **changes,
    }

    with pytest.raises(commands.DecisionCommandError) as exc:
        commands.teach_decision(
            db, role, user, decision_id=int(decision.id), **values
        )
    assert exc.value.code == code
    assert db.query(DecisionFeedback).count() == 0


def test_teach_rejects_terminal_and_cross_role_decisions(db):
    org, user, role, other_role = _context(db)
    terminal = _decision(
        db, org, role, label="teach-terminal", status="approved"
    )
    wrong_role = _decision(db, org, other_role, label="teach-wrong-role")
    db.commit()
    args = {
        "failure_mode": "other",
        "correction_text": "The recommendation should change.",
        "scope": "decision",
    }

    with pytest.raises(commands.DecisionCommandError) as terminal_exc:
        commands.teach_decision(
            db, role, user, decision_id=int(terminal.id), **args
        )
    assert terminal_exc.value.code == "decision_not_teachable"

    with pytest.raises(commands.DecisionCommandError) as scoped_exc:
        commands.teach_decision(
            db, role, user, decision_id=int(wrong_role.id), **args
        )
    assert scoped_exc.value.code == "decision_not_found"
