"""Legacy agent reads must obey the canonical related-role boundary."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.agent_chat.tools import dispatch_tool as dispatch_agent_chat_tool
from app.agent_runtime import tool_registry
from app.mcp import handlers as mcp_handlers
from app.mcp.catalog import (
    AGENT_CHAT,
    AUTONOMOUS_AGENT,
    PUBLIC_MCP,
    TAALI_CHAT,
)
from app.mcp.shared_reads import dispatch_shared_read
from app.models.agent_run import AgentRun
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.taali_chat_conversation import TaaliChatConversation
from app.models.user import User
from app.services.logical_role_application_authority import (
    authorize_logical_role_candidate,
)
from app.taali_chat.tool_registry import dispatch_tool as dispatch_taali_tool


def _world(db):
    organization = Organization(
        name="Related agent read parity",
        slug=f"related-agent-read-{id(db)}",
    )
    db.add(organization)
    db.flush()
    owner = Role(
        organization_id=int(organization.id),
        name="ATS owner",
        source="workable",
        workable_job_id=f"READ-PARITY-{organization.id}",
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=int(organization.id),
        name="Independent related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(owner.id),
        job_spec_text="Independent production engineering role specification.",
        score_threshold=70,
    )
    user = User(
        organization_id=int(organization.id),
        email=f"related-agent-read-{id(db)}@example.test",
        hashed_password="x",
        full_name="Recruiter",
        is_active=True,
        is_verified=True,
    )
    db.add_all([related, user])
    db.flush()

    candidates = []
    applications = []
    for index in range(3):
        candidate = Candidate(
            organization_id=int(organization.id),
            email=f"related-agent-candidate-{index}-{id(db)}@example.test",
            full_name=f"Candidate {index + 1}",
            cv_text="Production Python and distributed systems.",
        )
        db.add(candidate)
        db.flush()
        application = CandidateApplication(
            organization_id=int(organization.id),
            candidate_id=int(candidate.id),
            role_id=int(owner.id),
            source="workable",
            pipeline_stage="advanced",
            pipeline_stage_source="sync",
            application_outcome="rejected",
            cv_text=candidate.cv_text,
            workable_candidate_id=f"workable-{index}-{id(db)}",
            workable_stage="Technical Interview",
        )
        db.add(application)
        db.flush()
        candidates.append(candidate)
        applications.append(application)

    member = applications[0]
    db.add(
        SisterRoleEvaluation(
            organization_id=int(organization.id),
            role_id=int(related.id),
            candidate_id=int(member.candidate_id),
            source_application_id=int(member.id),
            ats_application_id=int(member.id),
            status="done",
            pipeline_stage="review",
            application_outcome="open",
            membership_source="initial_snapshot",
            spec_fingerprint="related-agent-read-parity",
            role_fit_score=88,
        )
    )
    second_member = applications[1]
    db.add(
        SisterRoleEvaluation(
            organization_id=int(organization.id),
            role_id=int(related.id),
            candidate_id=int(second_member.candidate_id),
            source_application_id=int(second_member.id),
            ats_application_id=int(second_member.id),
            status="done",
            pipeline_stage="applied",
            application_outcome="withdrawn",
            membership_source="initial_snapshot",
            spec_fingerprint="related-agent-read-parity",
            role_fit_score=73,
        )
    )
    db.commit()
    return organization, related, user, member, second_member, applications[2]


def _agent_run(
    db, *, organization: Organization, role: Role, id_offset: int
) -> AgentRun:
    run = AgentRun(
        id=id_offset + int(role.id),
        organization_id=int(organization.id),
        role_id=int(role.id),
        trigger="manual",
        status="running",
        model_version="offline-test",
        prompt_version="agent.role-read-parity",
    )
    db.add(run)
    db.flush()
    return run


def _add_conflicting_profile_assessments(
    db,
    *,
    organization: Organization,
    related: Role,
    member: CandidateApplication,
) -> Role:
    """Give one physical candidate different owner and related score truth."""

    owner = db.get(Role, int(member.role_id))
    assert owner is not None
    member.assessment_score_cache_100 = 96
    member.taali_score_cache_100 = 92
    member.role_fit_score_cache_100 = 88
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            Assessment(
                organization_id=int(organization.id),
                candidate_id=int(member.candidate_id),
                role_id=int(owner.id),
                application_id=int(member.id),
                status=AssessmentStatus.COMPLETED,
                completed_at=now,
                assessment_score=96,
                is_voided=False,
            ),
            Assessment(
                organization_id=int(organization.id),
                candidate_id=int(member.candidate_id),
                role_id=int(related.id),
                application_id=int(member.id),
                status=AssessmentStatus.COMPLETED,
                completed_at=now,
                assessment_score=34,
                taali_score=61,
                is_voided=False,
            ),
        ]
    )
    db.commit()
    return owner


def _membership_by_role(profile: dict) -> dict[int, dict]:
    return {int(row["role_id"]): row for row in profile["applications"]}


def test_public_get_candidate_preserves_each_logical_roles_assessment_truth(db):
    """The public/global profile cannot reuse the ATS owner's assessment."""

    organization, related, user, member, _second_member, _outsider = _world(db)
    owner = _add_conflicting_profile_assessments(
        db,
        organization=organization,
        related=related,
        member=member,
    )

    profile = mcp_handlers.get_candidate(
        db,
        user,
        candidate_id=int(member.candidate_id),
    )

    memberships = _membership_by_role(profile)
    assert set(memberships) == {int(owner.id), int(related.id)}
    assert memberships[int(owner.id)]["assessment_score"] == 96
    assert memberships[int(owner.id)]["assessment_score_cache_100"] == 96
    assert memberships[int(owner.id)]["taali_score"] == 92
    assert memberships[int(related.id)]["assessment_score"] == 34
    assert memberships[int(related.id)]["assessment_score_cache_100"] == 34
    assert memberships[int(related.id)]["taali_score"] == 61


def test_role_bound_candidate_profiles_use_related_assessment_truth_everywhere(db):
    """Authority, Taali Chat, and autonomous compatibility reads stay aligned."""

    organization, related, user, member, _second_member, _outsider = _world(db)
    owner = _add_conflicting_profile_assessments(
        db,
        organization=organization,
        related=related,
        member=member,
    )
    conversation = TaaliChatConversation(
        organization_id=int(organization.id),
        user_id=int(user.id),
        role_id=int(related.id),
        title="Related assessment profile truth",
    )
    db.add(conversation)
    db.flush()

    context = authorize_logical_role_candidate(
        db,
        role=related,
        candidate_id=int(member.candidate_id),
    )
    presented = context.presented_application
    taali_profile = dispatch_taali_tool(
        "get_candidate",
        {"candidate_id": int(member.candidate_id)},
        db=db,
        user=user,
        conversation=conversation,
    )
    autonomous_profile = tool_registry.dispatch(
        "get_candidate",
        {"candidate_id": int(member.candidate_id)},
        db=db,
        agent_run=_agent_run(
            db,
            organization=organization,
            role=related,
            id_offset=995000,
        ),
        role=related,
    )

    assert presented.assessment_score_cache_100 == 34
    assert presented.taali_score_cache_100 == 61
    for profile in (taali_profile, autonomous_profile):
        memberships = _membership_by_role(profile)
        assert set(memberships) == {int(related.id)}
        assert int(owner.id) not in memberships
        assert memberships[int(related.id)]["assessment_score"] == 34
        assert memberships[int(related.id)]["assessment_score_cache_100"] == 34
        assert memberships[int(related.id)]["taali_score"] == 61


def test_embedded_agent_overview_and_search_use_related_role_local_truth(db):
    _organization, related, user, member, _second_member, _outsider = _world(db)

    overview = dispatch_agent_chat_tool(
        "get_role_overview", {}, db=db, role=related, user=user
    )
    candidates = dispatch_agent_chat_tool(
        "search_role_candidates",
        {"limit": 20},
        db=db,
        role=related,
        user=user,
    )

    assert overview["open_candidates"] == 1
    assert overview["funnel"]["review"] == 1
    assert overview["funnel"].get("advanced", 0) == 0
    assert candidates["total"] == 1
    assert candidates["items"][0]["application_id"] == member.id
    assert candidates["items"][0]["pipeline_stage"] == "review"


@pytest.mark.parametrize(
    ("exposure", "bound_role"),
    [
        (PUBLIC_MCP, False),
        (TAALI_CHAT, False),
        (AGENT_CHAT, True),
        (AUTONOMOUS_AGENT, True),
    ],
)
def test_every_agent_surface_uses_related_role_owned_assessment_truth(
    db, exposure, bound_role
):
    organization, related, user, member, second_member, _outsider = _world(db)
    owner = db.get(Role, int(member.role_id))
    second_evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == int(related.id),
            SisterRoleEvaluation.source_application_id == int(second_member.id),
        )
        .one()
    )
    second_evaluation.application_outcome = "open"
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            Assessment(
                organization_id=int(organization.id),
                candidate_id=int(member.candidate_id),
                role_id=int(owner.id),
                application_id=int(member.id),
                status=AssessmentStatus.COMPLETED,
                completed_at=now,
                assessment_score=99,
                is_voided=False,
            ),
            Assessment(
                organization_id=int(organization.id),
                candidate_id=int(member.candidate_id),
                role_id=int(related.id),
                application_id=int(member.id),
                status=AssessmentStatus.COMPLETED,
                completed_at=now,
                assessment_score=82,
                taali_score=85,
                is_voided=False,
            ),
            Assessment(
                organization_id=int(organization.id),
                candidate_id=int(second_member.candidate_id),
                role_id=int(related.id),
                application_id=int(second_member.id),
                status=AssessmentStatus.COMPLETED,
                completed_at=now,
                assessment_score=55,
                taali_score=64,
                is_voided=False,
            ),
        ]
    )
    db.commit()

    search_args = {
        "score_type": "assessment",
        "min_score": 70.0,
        "sort_by": "assessment_score",
        "limit": 20,
    }
    detail_args = {"application_id": int(member.id)}
    if not bound_role:
        search_args["role_id"] = int(related.id)
        detail_args["role_id"] = int(related.id)
    dispatch_kwargs = {
        "exposure": exposure,
        "db": db,
        "principal": user,
    }
    if bound_role:
        dispatch_kwargs["bound_role_id"] = int(related.id)

    search = dispatch_shared_read(
        "search_role_candidates",
        search_args,
        **dispatch_kwargs,
    )
    detail = dispatch_shared_read(
        "get_role_candidate",
        detail_args,
        **dispatch_kwargs,
    )

    assert search["total"] == 1
    assert [row["application_id"] for row in search["items"]] == [member.id]
    assert search["items"][0]["assessment_score"] == 82
    assert search["items"][0]["taali_score"] == 85
    assert detail["assessment_score"] == 82
    assert detail["taali_score"] == 85


def test_embedded_agent_reads_keep_live_members_when_evidence_is_soft_deleted(db):
    from app.mcp.handlers import get_candidate

    _organization, related, user, member, second_member, _outsider = _world(db)
    rejected_membership = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == int(related.id),
            SisterRoleEvaluation.source_application_id == int(second_member.id),
        )
        .one()
    )
    rejected_membership.application_outcome = "rejected"
    member.deleted_at = datetime.now(timezone.utc)
    second_member.deleted_at = datetime.now(timezone.utc)
    db.commit()

    overview = dispatch_agent_chat_tool(
        "get_role_overview", {}, db=db, role=related, user=user
    )
    open_candidates = dispatch_agent_chat_tool(
        "search_role_candidates",
        {"limit": 20},
        db=db,
        role=related,
        user=user,
    )
    rejected_candidates = dispatch_agent_chat_tool(
        "search_role_candidates",
        {"application_outcome": "rejected", "limit": 20},
        db=db,
        role=related,
        user=user,
    )
    profile = get_candidate(db, user, candidate_id=int(member.candidate_id))

    assert overview["open_candidates"] == 1
    assert open_candidates["total"] == 1
    assert open_candidates["items"][0]["application_id"] == int(member.id)
    assert rejected_candidates["total"] == 1
    assert rejected_candidates["items"][0]["application_id"] == int(second_member.id)
    related_membership = next(
        row for row in profile["applications"] if int(row["role_id"]) == int(related.id)
    )
    assert related_membership["pipeline_stage"] == "review"
    assert related_membership["application_outcome"] == "open"
    assert related_membership["taali_score"] == 88


def test_mcp_role_summaries_count_explicit_related_membership_and_local_stages(db):
    from app.mcp.handlers import get_role, list_roles

    _organization, related, user, member, second_member, _outsider = _world(db)

    listed = {
        row["role_id"]: row for row in list_roles(db, user, include_stage_counts=True)
    }
    detail = get_role(db, user, role_id=int(related.id))

    assert listed[related.id]["applications_count"] == 2
    assert listed[related.id]["stage_counts"]["review"] == 1
    assert listed[related.id]["stage_counts"]["applied"] == 0
    assert detail["applications_count"] == 2
    assert detail["stage_counts"]["review"] == 1
    assert detail["stage_counts"]["advanced"] == 0
    assert {member.id, second_member.id} == {
        row.source_application_id
        for row in db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == related.id)
        .all()
    }


def test_autonomous_cohort_reads_use_related_membership_and_local_state(db):
    organization, related, _user, member, _second_member, _outsider = _world(db)
    run = _agent_run(
        db,
        organization=organization,
        role=related,
        id_offset=970000,
    )

    survey = tool_registry.dispatch(
        "survey_role_state",
        {},
        db=db,
        agent_run=run,
        role=related,
    )
    found = tool_registry.dispatch(
        "find_apps_in_state",
        {"state": "ready_for_advance_decision", "limit": 20},
        db=db,
        agent_run=run,
        role=related,
    )

    assert survey["counts"]["rejected"] == 0
    assert survey["counts"]["ready_for_assessment_decision"] == 0
    assert survey["counts"]["ready_for_advance_decision"] == 1
    assert found == {
        "state": "ready_for_advance_decision",
        "application_ids": [member.id],
        "count": 1,
    }


def test_autonomous_cohort_reads_keep_live_member_with_deleted_evidence(db):
    organization, related, _user, member, _second_member, _outsider = _world(db)
    member.deleted_at = datetime.now(timezone.utc)
    db.flush()
    run = _agent_run(
        db,
        organization=organization,
        role=related,
        id_offset=980000,
    )

    survey = tool_registry.dispatch(
        "survey_role_state",
        {},
        db=db,
        agent_run=run,
        role=related,
    )
    found = tool_registry.dispatch(
        "find_apps_in_state",
        {"state": "ready_for_advance_decision", "limit": 20},
        db=db,
        agent_run=run,
        role=related,
    )

    assert survey["counts"]["ready_for_advance_decision"] == 1
    assert found["application_ids"] == [member.id]


def test_autonomous_legacy_reads_delegate_to_role_local_contract(db):
    organization, related, _user, member, second_member, outsider = _world(db)
    run = AgentRun(
        id=990000 + int(related.id),
        organization_id=int(organization.id),
        role_id=int(related.id),
        trigger="manual",
        status="running",
        model_version="offline-test",
        prompt_version="agent.role-read-parity",
    )
    db.add(run)
    db.flush()

    search = tool_registry.dispatch(
        "search_applications",
        {},
        db=db,
        agent_run=run,
        role=related,
    )
    detail = tool_registry.dispatch(
        "get_application",
        {"application_id": int(member.id)},
        db=db,
        agent_run=run,
        role=related,
    )
    comparison = tool_registry.dispatch(
        "compare_applications",
        {"application_ids": [int(member.id), int(second_member.id)]},
        db=db,
        agent_run=run,
        role=related,
    )
    candidate = tool_registry.dispatch(
        "get_candidate",
        {"candidate_id": int(member.candidate_id)},
        db=db,
        agent_run=run,
        role=related,
    )
    cv = tool_registry.dispatch(
        "get_candidate_cv",
        {"candidate_id": int(member.candidate_id)},
        db=db,
        agent_run=run,
        role=related,
    )

    assert [row["application_id"] for row in search] == [member.id]
    assert search[0]["role_id"] == related.id
    assert search[0]["pipeline_stage"] == "review"
    assert search[0]["application_outcome"] == "open"
    assert detail["role_id"] == related.id
    assert detail["current_state"]["pipeline_stage"] == "review"
    assert detail["current_state"]["application_outcome"] == "open"
    assert comparison["missing_ids"] == []
    assert [row["application_id"] for row in comparison["applications"]] == [
        member.id,
        second_member.id,
    ]
    assert [row["role_id"] for row in comparison["applications"]] == [
        related.id,
        related.id,
    ]
    assert [row["pipeline_stage"] for row in comparison["applications"]] == [
        "review",
        "applied",
    ]
    assert [row["application_outcome"] for row in comparison["applications"]] == [
        "open",
        "withdrawn",
    ]
    assert candidate["candidate_id"] == int(member.candidate_id)
    memberships = {int(row["role_id"]): row for row in candidate["applications"]}
    assert set(memberships) == {int(related.id)}
    assert memberships[int(related.id)]["pipeline_stage"] == "review"
    assert memberships[int(related.id)]["application_outcome"] == "open"
    assert memberships[int(related.id)]["taali_score"] == 88
    assert "Production Python" in cv["cv_text"]

    with pytest.raises(ValueError, match="not in .*role.*candidate pool"):
        tool_registry.dispatch(
            "get_application",
            {"application_id": int(outsider.id)},
            db=db,
            agent_run=run,
            role=related,
        )
    with pytest.raises(ValueError, match="not in .*role.*candidate pool"):
        tool_registry.dispatch(
            "get_candidate",
            {"candidate_id": int(outsider.candidate_id)},
            db=db,
            agent_run=run,
            role=related,
        )
    with pytest.raises(ValueError, match="not in .*role.*candidate pool"):
        tool_registry.dispatch(
            "get_candidate_cv",
            {"candidate_id": int(outsider.candidate_id)},
            db=db,
            agent_run=run,
            role=related,
        )


def test_autonomous_graph_refresh_authorizes_logical_related_membership(db):
    from unittest.mock import patch

    organization, related, _user, member, _second_member, outsider = _world(db)
    run = AgentRun(
        id=991000 + int(related.id),
        organization_id=int(organization.id),
        role_id=int(related.id),
        trigger="manual",
        status="running",
        model_version="offline-test",
        prompt_version="agent.role-read-parity",
    )
    db.add(run)
    related.agent_action_allowlist = sorted(tool_registry.GOVERNED_ACTION_TOOL_NAMES)
    member.deleted_at = datetime.now(timezone.utc)
    db.commit()

    with (
        patch("app.candidate_graph.client.is_configured", return_value=True),
        patch("app.candidate_graph.sync.sync_candidate", return_value=1) as sync_mock,
    ):
        included = tool_registry.dispatch(
            "refresh_candidate_graph",
            {"application_id": int(member.id)},
            db=db,
            agent_run=run,
            role=related,
        )
        excluded = tool_registry.dispatch(
            "refresh_candidate_graph",
            {"application_id": int(outsider.id)},
            db=db,
            agent_run=run,
            role=related,
        )

    assert included["status"] == "ok"
    assert included["application_id"] == int(member.id)
    assert excluded["status"] == "not_found"
    sync_mock.assert_called_once()
    assert sync_mock.call_args.kwargs["bill_role_id"] == int(related.id)


def test_ordinary_action_history_survives_application_soft_delete(db):
    from datetime import datetime, timezone

    from app.mcp.handlers import list_candidate_actions
    from app.models.candidate_application_event import CandidateApplicationEvent

    organization, _related, user, member, _second_member, _outsider = _world(db)
    owner = db.get(Role, int(member.role_id))
    event = CandidateApplicationEvent(
        application_id=int(member.id),
        organization_id=int(organization.id),
        role_id=int(owner.id),
        event_type="workable_moved",
        from_stage="review",
        to_stage=None,
        target_stage="Technical Interview",
        effect_status="confirmed",
        actor_type="recruiter",
        reason="Confirmed before the application was archived",
    )
    db.add(event)
    db.flush()
    member.deleted_at = datetime.now(timezone.utc)
    db.commit()

    history = list_candidate_actions(
        db,
        user,
        role_id=int(owner.id),
        action="advanced",
    )

    assert history["total_is_exact"] is True
    assert history["total"] == 1
    assert history["items"][0]["event_id"] == int(event.id)
    assert history["items"][0]["in_current_role_pool"] is False
    assert history["items"][0]["current_state"] is None


def test_related_action_history_survives_membership_soft_delete(db):
    from datetime import datetime, timezone

    from app.mcp.handlers import list_candidate_actions
    from app.models.candidate_application_event import CandidateApplicationEvent

    organization, related, user, member, _second_member, _outsider = _world(db)
    membership = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == int(related.id),
            SisterRoleEvaluation.source_application_id == int(member.id),
        )
        .one()
    )
    event = CandidateApplicationEvent(
        application_id=int(member.id),
        organization_id=int(organization.id),
        role_id=int(related.id),
        event_type="role_application_outcome_changed",
        from_outcome="open",
        to_outcome="rejected",
        target_stage="rejected",
        effect_status="confirmed",
        actor_type="recruiter",
        reason="Confirmed before explicit role membership was archived",
    )
    db.add(event)
    db.flush()
    membership.deleted_at = datetime.now(timezone.utc)
    db.commit()

    history = list_candidate_actions(
        db,
        user,
        role_id=int(related.id),
        action="rejected",
    )

    assert history["total_is_exact"] is True
    assert history["total"] == 1
    assert history["items"][0]["event_id"] == int(event.id)
    assert history["items"][0]["role_id"] == int(related.id)
    assert history["items"][0]["in_current_role_pool"] is False
    assert history["items"][0]["current_state"] is None


def test_candidate_action_view_deduplicates_local_and_ats_events(db):
    from app.mcp.handlers import list_candidate_actions
    from app.models.candidate_application_event import CandidateApplicationEvent

    organization, _related, user, member, _second_member, _outsider = _world(db)
    owner = db.get(Role, int(member.role_id))
    other_recruiter = User(
        organization_id=int(organization.id),
        email=f"other-recruiter-{id(db)}@example.test",
        hashed_password="x",
        full_name="Other Recruiter",
        is_active=True,
        is_verified=True,
    )
    db.add(other_recruiter)
    db.flush()
    local = CandidateApplicationEvent(
        application_id=int(member.id),
        organization_id=int(organization.id),
        role_id=int(owner.id),
        event_type="pipeline_stage_changed",
        from_stage="review",
        to_stage="advanced",
        effect_status="confirmed",
        actor_type="recruiter",
        actor_id=int(user.id),
        event_metadata={"workable_target_stage": "Technical Interview"},
    )
    ats = CandidateApplicationEvent(
        application_id=int(member.id),
        organization_id=int(organization.id),
        role_id=int(owner.id),
        event_type="workable_moved",
        target_stage="Technical Interview",
        effect_status="confirmed",
        actor_type="recruiter",
        actor_id=int(user.id),
    )
    distractor = CandidateApplicationEvent(
        application_id=int(member.id),
        organization_id=int(organization.id),
        role_id=int(owner.id),
        event_type="workable_moved",
        target_stage="Final Interview",
        effect_status="confirmed",
        actor_type="recruiter",
        actor_id=int(other_recruiter.id),
    )
    db.add_all([local, ats, distractor])
    db.commit()

    events = list_candidate_actions(
        db,
        user,
        role_id=int(owner.id),
        action="advanced",
        actor_type="recruiter",
        actor_id=int(user.id),
        result_view="events",
    )
    candidates = list_candidate_actions(
        db,
        user,
        role_id=int(owner.id),
        action="advanced",
        actor_type="recruiter",
        actor_id=int(user.id),
        result_view="candidates",
    )

    assert events["total"] == 2
    assert candidates["total"] == 1
    [candidate] = candidates["items"]
    assert candidate["application_id"] == int(member.id)
    assert candidate["event_count"] == 2
    assert set(candidate["event_ids"]) == {int(local.id), int(ats.id)}
    all_recruiters = list_candidate_actions(
        db,
        user,
        role_id=int(owner.id),
        action="advanced",
        actor_type="recruiter",
        result_view="events",
    )
    assert all_recruiters["total"] == 3
