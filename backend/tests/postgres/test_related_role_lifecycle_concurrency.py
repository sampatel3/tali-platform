"""PostgreSQL serialization tests for independent related-role lifecycles."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.services.decision_reevaluation_service import (
    RelatedApplicationResolvedError,
    RelatedDecisionNotActionableError,
    re_evaluate_related_decision,
)
from app.services.related_role_action_service import (
    transition_related_role_outcome_action,
)


@dataclass(frozen=True)
class _World:
    organization_id: int
    owner_role_id: int
    role_id: int
    candidate_id: int
    application_id: int
    evaluation_id: int
    decision_id: int


def _seed_world(factory) -> _World:
    stamp = uuid4().hex
    now = datetime.now(timezone.utc)
    with factory() as db:
        organization = Organization(
            name=f"Related lifecycle {stamp}",
            slug=f"related-lifecycle-{stamp}",
            credits_balance=1_000_000,
        )
        db.add(organization)
        db.flush()
        owner = Role(
            organization_id=organization.id,
            name="ATS transport owner",
            source="manual",
            job_spec_text="Owner role",
        )
        candidate = Candidate(
            organization_id=organization.id,
            full_name="Concurrency Candidate",
            email=f"related-lifecycle-{stamp}@example.test",
            cv_text="Python and production AI systems",
        )
        db.add_all([owner, candidate])
        db.flush()
        related = Role(
            organization_id=organization.id,
            name="Independent related role",
            source="sister",
            role_kind=ROLE_KIND_SISTER,
            ats_owner_role_id=owner.id,
            job_spec_text="Production Python engineer",
            agentic_mode_enabled=True,
        )
        application = CandidateApplication(
            organization_id=organization.id,
            candidate_id=candidate.id,
            role_id=owner.id,
            source="manual",
            pipeline_stage="review",
            pipeline_stage_updated_at=now,
            application_outcome="open",
            application_outcome_updated_at=now,
            cv_text=candidate.cv_text,
        )
        db.add_all([related, application])
        db.flush()
        evaluation = SisterRoleEvaluation(
            organization_id=organization.id,
            role_id=related.id,
            candidate_id=candidate.id,
            source_application_id=application.id,
            ats_application_id=application.id,
            status="stale_held",
            pipeline_stage="review",
            pipeline_stage_updated_at=now,
            application_outcome="open",
            application_outcome_updated_at=now,
            spec_fingerprint="old-spec",
            cv_fingerprint="old-cv",
            last_error_code="shared_inputs_changed",
        )
        decision = AgentDecision(
            organization_id=organization.id,
            role_id=related.id,
            application_id=application.id,
            decision_type="advance_to_interview",
            recommendation="advance_to_interview",
            status="pending",
            reasoning="Stale related-role recommendation",
            evidence={"sister_evaluation_id": 0},
            model_version="test",
            prompt_version="test",
            idempotency_key=f"related-lifecycle:{stamp}",
        )
        db.add_all([evaluation, decision])
        db.flush()
        decision.evidence = {"sister_evaluation_id": int(evaluation.id)}
        world = _World(
            organization_id=int(organization.id),
            owner_role_id=int(owner.id),
            role_id=int(related.id),
            candidate_id=int(candidate.id),
            application_id=int(application.id),
            evaluation_id=int(evaluation.id),
            decision_id=int(decision.id),
        )
        db.commit()
        return world


def _cleanup(factory, world: _World) -> None:
    with factory() as db:
        db.query(CandidateApplicationEvent).filter(
            CandidateApplicationEvent.organization_id == world.organization_id
        ).delete(synchronize_session=False)
        db.query(AgentDecision).filter(
            AgentDecision.organization_id == world.organization_id
        ).delete(synchronize_session=False)
        db.query(SisterRoleEvaluation).filter(
            SisterRoleEvaluation.organization_id == world.organization_id
        ).delete(synchronize_session=False)
        db.query(CandidateApplication).filter(
            CandidateApplication.organization_id == world.organization_id
        ).delete(synchronize_session=False)
        db.query(Candidate).filter(
            Candidate.organization_id == world.organization_id
        ).delete(synchronize_session=False)
        db.query(Role).filter(Role.organization_id == world.organization_id).delete(
            synchronize_session=False
        )
        db.query(Organization).filter(
            Organization.id == world.organization_id
        ).delete(synchronize_session=False)
        db.commit()


def _run_reevaluation(factory, world: _World) -> str:
    with factory() as db:
        decision = db.get(AgentDecision, world.decision_id)
        application = db.get(CandidateApplication, world.application_id)
        role = db.get(Role, world.role_id)
        try:
            re_evaluate_related_decision(
                db,
                decision=decision,
                application=application,
                role=role,
                workspace_paused=False,
            )
        except RelatedApplicationResolvedError:
            db.rollback()
            return "resolved"
        except RelatedDecisionNotActionableError as exc:
            db.rollback()
            return f"decision:{exc.status}"
        return "re_evaluated"


def test_terminal_transition_wins_against_concurrent_related_reevaluation(
    postgres_search_engine,
):
    factory = sessionmaker(bind=postgres_search_engine, expire_on_commit=False)
    world = _seed_world(factory)
    try:
        with factory() as terminal_db:
            application = (
                terminal_db.query(CandidateApplication)
                .filter(CandidateApplication.id == world.application_id)
                .with_for_update(of=CandidateApplication)
                .one()
            )
            result = transition_related_role_outcome_action(
                terminal_db,
                application=application,
                acting_role_id=world.role_id,
                to_outcome="rejected",
                source="recruiter",
                actor_type="recruiter",
                reason="Concurrent terminal decision",
                idempotency_key="postgres-related-terminal-race",
            )
            assert result is not None and result.changed is True
            # Materialize the role-scoped event and decision cleanup before the
            # competing transaction acquires Organization. Otherwise the
            # event's FK check would legitimately wait on that parent lock and
            # the test would manufacture an order no production action uses.
            terminal_db.flush()

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_reevaluation, factory, world)
                terminal_db.commit()
                assert future.result(timeout=10) == "resolved"

        with factory() as check:
            assert check.get(SisterRoleEvaluation, world.evaluation_id).application_outcome == "rejected"
            assert check.get(AgentDecision, world.decision_id).status == "discarded"
    finally:
        _cleanup(factory, world)


def test_processing_acceptance_wins_against_concurrent_related_reevaluation(
    postgres_search_engine, monkeypatch
):
    factory = sessionmaker(bind=postgres_search_engine, expire_on_commit=False)
    world = _seed_world(factory)
    reeval_holds_organization = Event()

    from app.services import workspace_agent_control

    original_snapshot = workspace_agent_control.workspace_agent_control_snapshot

    def observe_organization_lock(*args, **kwargs):
        result = original_snapshot(*args, **kwargs)
        reeval_holds_organization.set()
        return result

    monkeypatch.setattr(
        workspace_agent_control,
        "workspace_agent_control_snapshot",
        observe_organization_lock,
    )
    try:
        with factory() as approval_db:
            decision = (
                approval_db.query(AgentDecision)
                .filter(AgentDecision.id == world.decision_id)
                .with_for_update(of=AgentDecision)
                .one()
            )
            decision.status = "processing"
            approval_db.flush()
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_reevaluation, factory, world)
                assert reeval_holds_organization.wait(timeout=10)
                approval_db.commit()
                assert future.result(timeout=10) == "decision:processing"

        with factory() as check:
            evaluation = check.get(SisterRoleEvaluation, world.evaluation_id)
            assert evaluation.status == "stale_held"
            assert evaluation.application_outcome == "open"
            assert check.get(AgentDecision, world.decision_id).status == "processing"
    finally:
        _cleanup(factory, world)
