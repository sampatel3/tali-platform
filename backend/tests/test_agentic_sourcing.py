"""Focused contract tests for the agent-first sourcing path.

These tests deliberately stop at draft preparation.  The only outbound action
is the existing authenticated approve-and-send HITL; the role agent may source
from the internal talent pool and prepare drafts, but it may never send them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.actions import prepare_sourced_outreach, source_candidates
from app.actions.types import Actor
from app.agent_runtime import tool_registry
from app.candidate_search.schemas import ParsedFilter, SearchOutput
from app.mcp import handlers as mcp_handlers
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.job_page import JobPage
from app.models.organization import Organization
from app.models.outreach_campaign import (
    CAMPAIGN_STATUS_DRAFT,
    CAMPAIGN_STATUS_GENERATING,
    CAMPAIGN_STATUS_SENT,
    MESSAGE_STATUS_PENDING,
    OutreachCampaign,
    OutreachMessage,
)
from app.models.role import Role
from app.models.role_brief import RoleBrief
from app.services.email_suppression_service import suppress


def _seed_org(db, *, name: str = "Sourcing test") -> Organization:
    org = Organization(name=name, slug=f"sourcing-{uuid4().hex}")
    db.add(org)
    db.flush()
    return org


def _seed_role(
    db,
    org: Organization,
    *,
    name: str = "Platform Engineer",
    agentic: bool = True,
) -> Role:
    role = Role(
        organization_id=int(org.id),
        name=name,
        source="manual",
        job_spec_text="Build reliable distributed systems with Python.",
        agentic_mode_enabled=agentic,
        monthly_usd_budget_cents=None,
    )
    db.add(role)
    db.flush()
    # Autonomous outreach is only valid when its CTA reaches a real apply
    # surface. Seed the published native destination that production gets from
    # the requisition publish flow.
    brief = RoleBrief(
        organization_id=int(org.id),
        role_id=int(role.id),
        status="applied",
        source_kind="upload",
        title=name,
    )
    db.add(brief)
    db.flush()
    db.add(
        JobPage(
            organization_id=int(org.id),
            brief_id=int(brief.id),
            token=f"sourcing-job-{uuid4().hex}",
            title=name,
            status="open",
        )
    )
    db.flush()
    return role


def _seed_candidate(
    db,
    org: Organization,
    *,
    email: str | None,
    name: str = "Candidate",
    marketing_consent: bool | None = True,
) -> Candidate:
    candidate = Candidate(
        organization_id=int(org.id),
        email=email,
        full_name=name,
        marketing_consent=marketing_consent,
    )
    db.add(candidate)
    db.flush()
    return candidate


def _seed_application(
    db,
    org: Organization,
    role: Role,
    candidate: Candidate,
    *,
    stage: str = "applied",
    outcome: str = "open",
    deleted_at=None,
) -> CandidateApplication:
    application = CandidateApplication(
        organization_id=int(org.id),
        role_id=int(role.id),
        candidate_id=int(candidate.id),
        status=stage,
        pipeline_stage=stage,
        pipeline_stage_source="system",
        application_outcome=outcome,
        source="manual",
        deleted_at=deleted_at,
    )
    db.add(application)
    db.flush()
    return application


def _seed_agent_run(db, org: Organization, role: Role, *, run_id: int = 880001) -> AgentRun:
    # AgentRun uses a BigInteger PK, which SQLite does not auto-increment.
    run = AgentRun(
        id=run_id,
        organization_id=int(org.id),
        role_id=int(role.id),
        trigger="manual",
        status="running",
        model_version="test-model",
        prompt_version="agentic-sourcing.test",
    )
    db.add(run)
    db.flush()
    return run


def test_source_candidates_creates_unscored_sourced_application_with_agent_provenance(db):
    org = _seed_org(db)
    role = _seed_role(db, org)
    candidate = _seed_candidate(db, org, email="ready@example.com")
    run = _seed_agent_run(db, org, role)

    result = source_candidates.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        candidate_id=int(candidate.id),
        source_name="internal_talent_pool",
    )
    db.flush()

    application = db.get(CandidateApplication, int(result.application_id))
    assert result.status == "created"
    assert application.pipeline_stage == "sourced"
    assert application.application_outcome == "open"
    assert application.source == "sourced"
    assert application.source_strategy == "sourced"
    assert application.source_name == "internal_talent_pool"
    assert application.pipeline_stage_source == "agent"
    sourcing = application.external_refs["sourcing"]
    assert sourcing["provider"] == "internal_talent_pool"
    assert sourcing["actor_type"] == "agent"
    assert sourcing["agent_run_id"] == int(run.id)
    assert sourcing["sourced_at"]

    # A sourced lead is pre-application.  Creating it must not fabricate a
    # target-role verdict or any evidence that paid parsing/scoring occurred.
    assert application.cv_match_score is None
    assert application.cv_match_details is None
    assert application.cv_match_scored_at is None
    assert application.pre_screen_score_100 is None
    assert application.genuine_pre_screen_score_100 is None
    assert application.pre_screen_run_at is None
    assert application.taali_score_cache_100 is None
    assert application.score_cached_at is None

    again = source_candidates.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        candidate_id=int(candidate.id),
        source_name="internal_talent_pool",
    )
    assert again.status == "existing"
    assert again.application_id == result.application_id
    assert (
        db.query(CandidateApplication)
        .filter_by(role_id=int(role.id), candidate_id=int(candidate.id))
        .count()
        == 1
    )


def test_source_candidates_agent_cannot_resurrect_a_removed_role_record(db):
    org = _seed_org(db)
    role = _seed_role(db, org)
    candidate = _seed_candidate(db, org, email="removed@example.com")
    removed_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    application = _seed_application(
        db,
        org,
        role,
        candidate,
        stage="review",
        deleted_at=removed_at,
    )
    application.taali_score_cache_100 = 91.0
    db.flush()

    with pytest.raises(HTTPException) as exc:
        source_candidates.run(
            db,
            Actor.agent(880002),
            organization_id=int(org.id),
            role_id=int(role.id),
            candidate_id=int(candidate.id),
            source_name="internal_talent_pool",
            allow_reactivation=False,
        )

    assert exc.value.status_code == 409
    assert application.deleted_at == removed_at
    assert application.pipeline_stage == "review"
    assert application.taali_score_cache_100 == 91.0


def test_rediscover_candidates_searches_org_wide_dedupes_people_and_excludes_ineligible(db):
    org = _seed_org(db)
    target = _seed_role(db, org, name="Target role")
    prior_a = _seed_role(db, org, name="Prior A")
    prior_b = _seed_role(db, org, name="Prior B")

    duplicate_person = _seed_candidate(db, org, email="duplicate@example.com")
    duplicate_app_a = _seed_application(db, org, prior_a, duplicate_person)
    duplicate_app_b = _seed_application(db, org, prior_b, duplicate_person)
    unique_person = _seed_candidate(db, org, email="unique@example.com")
    unique_app = _seed_application(db, org, prior_a, unique_person, outcome="rejected")

    already_target = _seed_candidate(db, org, email="target@example.com")
    _seed_application(db, org, prior_a, already_target)
    _seed_application(db, org, target, already_target, stage="sourced")
    hired = _seed_candidate(db, org, email="hired@example.com")
    _seed_application(db, org, prior_a, hired, outcome="hired")
    missing_email = _seed_candidate(db, org, email=None)
    _seed_application(db, org, prior_a, missing_email, outcome="rejected")
    deleted_history = _seed_candidate(db, org, email="deleted@example.com")
    _seed_application(
        db,
        org,
        prior_a,
        deleted_history,
        outcome="rejected",
        deleted_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )

    other_org = _seed_org(db, name="Other org")
    other_role = _seed_role(db, other_org)
    other_candidate = _seed_candidate(db, other_org, email="other@example.com")
    _seed_application(db, other_org, other_role, other_candidate)
    db.flush()

    captured: dict[str, object] = {}

    def _run_search(**kwargs):
        captured.update(kwargs)
        ids = [
            int(value)
            for (value,) in kwargs["base_query"]
            .with_entities(CandidateApplication.id)
            .order_by(CandidateApplication.id.asc())
            .all()
        ]
        captured["eligible_application_ids"] = ids
        return SearchOutput(
            application_ids=ids,
            parsed_filter=ParsedFilter(
                skills_all=["python"], free_text="python distributed systems"
            ),
            database_matches=len(ids),
        )

    with patch("app.candidate_search.runner.run_search", side_effect=_run_search):
        result = mcp_handlers.rediscover_candidates(
            db,
            SimpleNamespace(organization_id=int(org.id)),
            query="python distributed systems",
            target_role_id=int(target.id),
            rerank=True,
            limit=10,
        )

    assert set(captured["eligible_application_ids"]) == {
        int(duplicate_app_a.id),
        int(duplicate_app_b.id),
        int(unique_app.id),
    }
    assert captured["organization_id"] == int(org.id)
    assert captured["role_id"] is None
    assert captured["metering_role_id"] == int(target.id)
    assert result["provider"] == "internal_talent_pool"
    assert result["candidate_ids"] == [int(duplicate_person.id), int(unique_person.id)]
    assert len(result["candidate_ids"]) == len(set(result["candidate_ids"]))
    assert result["external_sourcing"] == {
        "provider": "linkedin_rsc",
        "status": "partner_access_required",
        "capability": "one_click_export",
        "autonomous_search": False,
        "human_export_required": True,
        "manual_copy_paste_required": False,
        "delegated_agent_option": {
            "provider": "linkedin_hiring_assistant",
            "integration": "rsc_plus_connected_projects",
            "status": "commercial_and_partner_access_required",
            "orchestration_owner": "linkedin",
            "taali_controlled_search_api": False,
        },
    }


def test_prepare_sourced_outreach_rejects_non_agent_actor_before_mutation(db):
    org = _seed_org(db)
    role = _seed_role(db, org)
    candidate = _seed_candidate(db, org, email="candidate@example.com")

    with pytest.raises(HTTPException) as exc:
        prepare_sourced_outreach.run(
            db,
            Actor.system(),
            organization_id=int(org.id),
            role_id=int(role.id),
            candidate_ids=[int(candidate.id)],
        )

    assert exc.value.status_code == 403
    assert db.query(CandidateApplication).count() == 0
    assert db.query(OutreachCampaign).count() == 0


def test_prepare_sourced_outreach_waits_for_real_application_destination(db):
    org = _seed_org(db)
    role = Role(
        organization_id=int(org.id),
        name="Unpublished role",
        source="manual",
        job_spec_text="A role that has not been published yet.",
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.flush()
    run = _seed_agent_run(db, org, role)
    candidate = _seed_candidate(db, org, email="ready@example.com")

    with (
        patch("app.services.role_budget_gate.can_spend_on_role", return_value=True),
        patch("app.tasks.outreach_tasks.generate_campaign_drafts.delay") as generate,
    ):
        result = prepare_sourced_outreach.run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(org.id),
            role_id=int(role.id),
            candidate_ids=[int(candidate.id)],
        )

    assert result.status == "application_destination_required"
    generate.assert_not_called()
    assert db.query(CandidateApplication).count() == 0
    assert db.query(OutreachCampaign).count() == 0


def test_prepare_sourced_outreach_filters_candidates_prepares_once_and_never_sends(db):
    org = _seed_org(db)
    role = _seed_role(db, org)
    prior_role = _seed_role(db, org, name="Prior role")
    run = _seed_agent_run(db, org, role)

    eligible = _seed_candidate(db, org, email="eligible@example.com", name="Eligible")
    no_email = _seed_candidate(db, org, email=None, name="No email")
    no_consent = _seed_candidate(
        db, org, email="no-consent@example.com", name="No consent", marketing_consent=False
    )
    suppressed = _seed_candidate(db, org, email="suppressed@example.com")
    open_elsewhere = _seed_candidate(db, org, email="open@example.com")
    _seed_application(db, org, prior_role, open_elsewhere, stage="review", outcome="open")
    hired = _seed_candidate(db, org, email="hired@example.com")
    _seed_application(db, org, prior_role, hired, stage="advanced", outcome="hired")
    removed = _seed_candidate(db, org, email="removed@example.com")
    _seed_application(
        db,
        org,
        role,
        removed,
        stage="review",
        deleted_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )
    contacted = _seed_candidate(db, org, email="contacted@example.com")
    old_campaign = OutreachCampaign(
        organization_id=int(org.id),
        role_id=int(prior_role.id),
        name="Old campaign",
        origin="manual",
        status=CAMPAIGN_STATUS_SENT,
    )
    db.add(old_campaign)
    db.flush()
    db.add(
        OutreachMessage(
            organization_id=int(org.id),
            campaign_id=int(old_campaign.id),
            candidate_id=int(contacted.id),
            email="contacted@example.com",
            status=CAMPAIGN_STATUS_SENT,
        )
    )
    db.flush()
    suppress(
        db,
        organization_id=int(org.id),
        email="suppressed@example.com",
        reason="unsubscribed",
        source="test",
    )

    candidate_ids = [
        int(eligible.id),
        int(no_email.id),
        int(no_consent.id),
        int(suppressed.id),
        int(open_elsewhere.id),
        int(hired.id),
        int(removed.id),
        int(contacted.id),
    ]
    with (
        patch("app.services.role_budget_gate.can_spend_on_role", return_value=True),
        patch("app.tasks.outreach_tasks.generate_campaign_drafts.delay") as generate,
        patch("app.tasks.outreach_tasks.send_campaign_messages.delay") as send,
        patch("app.actions.prepare_sourced_outreach.on_application_created") as created_event,
    ):
        result = prepare_sourced_outreach.run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(org.id),
            role_id=int(role.id),
            candidate_ids=candidate_ids,
        )
        repeated = prepare_sourced_outreach.run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(org.id),
            role_id=int(role.id),
            candidate_ids=candidate_ids,
        )

    assert result.status == CAMPAIGN_STATUS_GENERATING
    assert result.sourced == 1
    assert result.audience_added == 1
    assert repeated.status == CAMPAIGN_STATUS_GENERATING
    assert repeated.campaign_id == result.campaign_id
    generate.assert_called_once_with(int(result.campaign_id))
    send.assert_not_called()

    assert db.query(OutreachCampaign).filter_by(origin="agent").count() == 1
    campaign = db.get(OutreachCampaign, int(result.campaign_id))
    assert campaign.status == CAMPAIGN_STATUS_GENERATING
    assert campaign.origin == "agent"
    assert campaign.prepared_by_agent_run_id == int(run.id)
    assert campaign.approved_by_user_id is None
    assert campaign.approved_at is None
    assert campaign.idempotency_key.startswith(f"agent-outreach:{int(role.id)}:")
    messages = (
        db.query(OutreachMessage)
        .filter(OutreachMessage.campaign_id == int(campaign.id))
        .all()
    )
    assert [(message.candidate_id, message.status) for message in messages] == [
        (int(eligible.id), MESSAGE_STATUS_PENDING)
    ]
    sourced = (
        db.query(CandidateApplication)
        .filter_by(role_id=int(role.id), candidate_id=int(eligible.id))
        .one()
    )
    assert sourced.pipeline_stage == "sourced"
    assert sourced.taali_score_cache_100 is None
    created_event.assert_called_once()
    assert created_event.call_args.kwargs == {
        "score": False,
        "allow_paid_work": False,
        "parse_origin": None,
    }
    reasons = {item["reason"] for item in result.skipped}
    assert {
        "missing_email",
        "no_marketing_consent",
        "suppressed",
        "open_application",
        "hired",
        "previously_removed",
        "already_contacted",
    }.issubset(reasons)
    assert result.as_dict()["send_requires_human_approval"] is True


def test_prepare_sourced_outreach_broker_failure_is_compensated_and_retryable(db):
    org = _seed_org(db)
    role = _seed_role(db, org)
    run = _seed_agent_run(db, org, role)
    candidate = _seed_candidate(db, org, email="retry@example.com")

    with (
        patch("app.services.role_budget_gate.can_spend_on_role", return_value=True),
        patch(
            "app.tasks.outreach_tasks.generate_campaign_drafts.delay",
            side_effect=RuntimeError("broker unavailable"),
        ),
    ):
        failed = prepare_sourced_outreach.run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(org.id),
            role_id=int(role.id),
            candidate_ids=[int(candidate.id)],
        )

    assert failed.status == "draft_enqueue_failed"
    campaign = db.get(OutreachCampaign, int(failed.campaign_id))
    assert campaign.status == CAMPAIGN_STATUS_DRAFT
    assert db.query(OutreachMessage).filter_by(campaign_id=int(campaign.id)).count() == 1

    # Compensation is useful only if a later autonomous cycle can claim the
    # same campaign and retry its already-built pending audience.
    with (
        patch("app.services.role_budget_gate.can_spend_on_role", return_value=True),
        patch("app.tasks.outreach_tasks.generate_campaign_drafts.delay") as generate,
        patch("app.tasks.outreach_tasks.send_campaign_messages.delay") as send,
    ):
        retried = prepare_sourced_outreach.run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(org.id),
            role_id=int(role.id),
            candidate_ids=[int(candidate.id)],
        )

    assert retried.status == CAMPAIGN_STATUS_GENERATING
    assert retried.campaign_id == failed.campaign_id
    generate.assert_called_once_with(int(failed.campaign_id))
    send.assert_not_called()
    db.refresh(campaign)
    assert campaign.status == CAMPAIGN_STATUS_GENERATING


def test_existing_sourced_backlog_does_not_recontact_legacy_message_email(db):
    org = _seed_org(db)
    role = _seed_role(db, org)
    run = _seed_agent_run(db, org, role)
    candidate = _seed_candidate(db, org, email="already-contacted@example.com")
    _seed_application(db, org, role, candidate, stage="sourced")
    old_campaign = OutreachCampaign(
        organization_id=int(org.id),
        role_id=int(role.id),
        name="Legacy outreach",
        origin="manual",
        status=CAMPAIGN_STATUS_SENT,
    )
    db.add(old_campaign)
    db.flush()
    db.add(
        OutreachMessage(
            organization_id=int(org.id),
            campaign_id=int(old_campaign.id),
            candidate_id=None,
            source_application_id=None,
            email="already-contacted@example.com",
            status=CAMPAIGN_STATUS_SENT,
        )
    )
    db.flush()

    with (
        patch("app.services.role_budget_gate.can_spend_on_role", return_value=True),
        patch("app.tasks.outreach_tasks.generate_campaign_drafts.delay") as generate,
    ):
        result = prepare_sourced_outreach.run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(org.id),
            role_id=int(role.id),
            candidate_ids=[],
        )

    assert result.status == "no_reachable_candidates"
    generate.assert_not_called()
    assert db.query(OutreachCampaign).filter_by(origin="agent").count() == 0


def test_fresh_discovery_does_not_recontact_legacy_message_email(db):
    org = _seed_org(db)
    role = _seed_role(db, org)
    run = _seed_agent_run(db, org, role)
    candidate = _seed_candidate(db, org, email="legacy-recipient@example.com")
    old_campaign = OutreachCampaign(
        organization_id=int(org.id),
        role_id=int(role.id),
        name="Legacy prospect outreach",
        origin="manual",
        status=CAMPAIGN_STATUS_SENT,
    )
    db.add(old_campaign)
    db.flush()
    db.add(
        OutreachMessage(
            organization_id=int(org.id),
            campaign_id=int(old_campaign.id),
            candidate_id=None,
            source_application_id=None,
            email=" Legacy-Recipient@Example.com ",
            status=CAMPAIGN_STATUS_SENT,
        )
    )
    db.flush()

    with patch("app.tasks.outreach_tasks.generate_campaign_drafts.delay") as generate:
        result = prepare_sourced_outreach.run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(org.id),
            role_id=int(role.id),
            candidate_ids=[int(candidate.id)],
        )

    assert result.status == "no_reachable_candidates"
    assert result.skipped == [
        {
            "candidate_id": int(candidate.id),
            "email": "legacy-recipient@example.com",
            "reason": "already_contacted",
        }
    ]
    generate.assert_not_called()
    assert db.query(OutreachCampaign).filter_by(origin="agent").count() == 0


def test_prepare_tool_requires_same_run_discovery_provenance(db):
    org = _seed_org(db)
    role = _seed_role(db, org)
    run = _seed_agent_run(db, org, role)
    candidate = _seed_candidate(db, org, email="guessed@example.com")

    with patch(
        "app.agent_runtime.tool_registry.prepare_sourced_outreach.run"
    ) as prepare:
        blocked = tool_registry.dispatch(
            "prepare_sourced_outreach",
            {"candidate_ids": [int(candidate.id)]},
            db=db,
            agent_run=run,
            role=role,
        )

    assert blocked["status"] == "blocked_by_governance"
    assert "must come from rediscover_candidates" in blocked["reason"]
    prepare.assert_not_called()


def test_rediscovery_persists_ids_for_same_run_prepare(db):
    org = _seed_org(db)
    role = _seed_role(db, org)
    run = _seed_agent_run(db, org, role)
    candidate = _seed_candidate(db, org, email="verified@example.com")
    discovery = {
        "provider": "internal_talent_pool",
        "candidate_ids": [int(candidate.id)],
    }
    prepared = SimpleNamespace(as_dict=lambda: {"status": "generating"})

    with (
        patch(
            "app.agent_runtime.tool_registry.mcp_handlers.rediscover_candidates",
            return_value=discovery,
        ),
        patch(
            "app.agent_runtime.tool_registry.prepare_sourced_outreach.run",
            return_value=prepared,
        ) as prepare,
    ):
        found = tool_registry.dispatch(
            "rediscover_candidates",
            {"query": "verified engineer"},
            db=db,
            agent_run=run,
            role=role,
        )
        result = tool_registry.dispatch(
            "prepare_sourced_outreach",
            {"candidate_ids": [int(candidate.id)]},
            db=db,
            agent_run=run,
            role=role,
        )

    assert found == discovery
    assert result["status"] == "generating"
    assert run.agent_state_snapshot["sourcing_discovery"]["candidate_ids"] == [
        int(candidate.id)
    ]
    prepare.assert_called_once()


def test_prepare_sourced_outreach_tool_is_hard_capped_once_per_agent_cycle(db):
    org = _seed_org(db)
    role = _seed_role(db, org)
    run = _seed_agent_run(db, org, role)
    fake_result = SimpleNamespace(
        as_dict=lambda: {
            "status": "no_reachable_candidates",
            "send_requires_human_approval": True,
        }
    )

    with patch(
        "app.agent_runtime.tool_registry.prepare_sourced_outreach.run",
        return_value=fake_result,
    ) as prepare:
        first = tool_registry.dispatch(
            "prepare_sourced_outreach",
            {"candidate_ids": []},
            db=db,
            agent_run=run,
            role=role,
        )
        second = tool_registry.dispatch(
            "prepare_sourced_outreach",
            {"candidate_ids": []},
            db=db,
            agent_run=run,
            role=role,
        )

    assert first["status"] == "no_reachable_candidates"
    assert second["status"] == "blocked_by_governance"
    assert second["reason"] == "per-cycle sourcing preparation cap reached (1)"
    prepare.assert_called_once()
