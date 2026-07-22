"""Legacy agent reads must obey the canonical related-role boundary."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.agent_chat.tools import dispatch_tool as dispatch_agent_chat_tool
from app.agent_runtime.cohort_tools import find_apps_in_state, survey_role_state
from app.agent_runtime import tool_registry
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User


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


def test_embedded_agent_overview_and_list_use_related_role_local_truth(db):
    _organization, related, user, member, _second_member, _outsider = _world(db)

    overview = dispatch_agent_chat_tool(
        "get_role_overview", {}, db=db, role=related, user=user
    )
    candidates = dispatch_agent_chat_tool(
        "list_candidates",
        {"bucket": "all", "limit": 20},
        db=db,
        role=related,
        user=user,
    )

    assert overview["open_candidates"] == 1
    assert overview["funnel"]["review"] == 1
    assert overview["funnel"].get("advanced", 0) == 0
    assert candidates["count"] == 1
    assert candidates["candidates"][0]["application_id"] == member.id
    assert candidates["candidates"][0]["stage"] == "review"


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
        "list_candidates",
        {"bucket": "all", "limit": 20},
        db=db,
        role=related,
        user=user,
    )
    rejected_candidates = dispatch_agent_chat_tool(
        "list_candidates",
        {"bucket": "rejected", "limit": 20},
        db=db,
        role=related,
        user=user,
    )
    profile = get_candidate(db, user, candidate_id=int(member.candidate_id))

    assert overview["open_candidates"] == 1
    assert open_candidates["count"] == 1
    assert open_candidates["candidates"][0]["application_id"] == int(member.id)
    assert rejected_candidates["count"] == 1
    assert rejected_candidates["candidates"][0]["application_id"] == int(second_member.id)
    related_membership = next(
        row
        for row in profile["applications"]
        if int(row["role_id"]) == int(related.id)
    )
    assert related_membership["pipeline_stage"] == "review"
    assert related_membership["application_outcome"] == "open"
    assert related_membership["taali_score"] == 88


def test_mcp_role_summaries_count_explicit_related_membership_and_local_stages(db):
    from app.mcp.handlers import get_role, list_roles

    _organization, related, user, member, second_member, _outsider = _world(db)

    listed = {
        row["role_id"]: row
        for row in list_roles(db, user, include_stage_counts=True)
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

    survey = survey_role_state(
        db,
        organization_id=int(organization.id),
        role_id=int(related.id),
    )
    ids = find_apps_in_state(
        db,
        organization_id=int(organization.id),
        role_id=int(related.id),
        state="ready_for_advance_decision",
        limit=20,
    )

    assert survey["counts"]["rejected"] == 0
    assert survey["counts"]["ready_for_assessment_decision"] == 0
    assert survey["counts"]["ready_for_advance_decision"] == 1
    assert ids == [member.id]


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
    memberships = {
        int(row["role_id"]): row for row in candidate["applications"]
    }
    assert memberships[int(related.id)]["pipeline_stage"] == "review"
    assert memberships[int(related.id)]["application_outcome"] == "open"
    assert memberships[int(related.id)]["taali_score"] == 88
    assert memberships[int(member.role_id)]["application_outcome"] == "rejected"
    assert "Production Python" in cv["cv_text"]

    with pytest.raises(ValueError, match="not in role"):
        tool_registry.dispatch(
            "get_application",
            {"application_id": int(outsider.id)},
            db=db,
            agent_run=run,
            role=related,
        )
    with pytest.raises(ValueError, match="not in role"):
        tool_registry.dispatch(
            "get_candidate",
            {"candidate_id": int(outsider.candidate_id)},
            db=db,
            agent_run=run,
            role=related,
        )
    with pytest.raises(ValueError, match="not in role"):
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
    related.agent_action_allowlist = sorted(
        tool_registry.GOVERNED_ACTION_TOOL_NAMES
    )
    member.deleted_at = datetime.now(timezone.utc)
    db.commit()

    with patch("app.candidate_graph.client.is_configured", return_value=True), patch(
        "app.candidate_graph.sync.sync_candidate", return_value=1
    ) as sync_mock:
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
        event_type="pipeline_stage_changed",
        from_stage="review",
        to_stage="advanced",
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
