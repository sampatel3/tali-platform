"""Real PostgreSQL race tests for the logical decision queue slot."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.actions import queue_decision
from app.actions.types import Actor
from app.models.agent_decision import (
    AGENT_DECISION_ACTIVE_STATUSES,
    AgentDecision,
)
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import Role


@dataclass(frozen=True)
class _World:
    organization_id: int
    role_id: int
    candidate_id: int
    application_id: int
    run_ids: tuple[int, int]


def _seed_world(factory) -> _World:
    stamp = uuid4().hex
    now = datetime.now(timezone.utc)
    with factory() as db:
        organization = Organization(
            name=f"Decision slot {stamp}",
            slug=f"decision-slot-{stamp}",
            credits_balance=1_000_000,
        )
        db.add(organization)
        db.flush()
        role = Role(
            organization_id=int(organization.id),
            name="Decision-slot role",
            source="manual",
            job_spec_text="Production software engineering",
            agentic_mode_enabled=True,
        )
        candidate = Candidate(
            organization_id=int(organization.id),
            full_name="Concurrent Candidate",
            email=f"decision-slot-{stamp}@example.test",
            cv_text="Python and production systems",
        )
        db.add_all([role, candidate])
        db.flush()
        application = CandidateApplication(
            organization_id=int(organization.id),
            candidate_id=int(candidate.id),
            role_id=int(role.id),
            source="manual",
            pipeline_stage="review",
            pipeline_stage_updated_at=now,
            pipeline_stage_source="system",
            application_outcome="open",
            application_outcome_updated_at=now,
            cv_text=candidate.cv_text,
        )
        runs = [
            AgentRun(
                organization_id=int(organization.id),
                role_id=int(role.id),
                trigger="cron",
                status="running",
                model_version="test",
                prompt_version="test",
            )
            for _ in range(2)
        ]
        db.add_all([application, *runs])
        db.flush()
        world = _World(
            organization_id=int(organization.id),
            role_id=int(role.id),
            candidate_id=int(candidate.id),
            application_id=int(application.id),
            run_ids=(int(runs[0].id), int(runs[1].id)),
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
        db.query(AgentRun).filter(
            AgentRun.organization_id == world.organization_id
        ).delete(synchronize_session=False)
        db.query(CandidateApplication).filter(
            CandidateApplication.id == world.application_id
        ).delete(synchronize_session=False)
        db.query(Candidate).filter(Candidate.id == world.candidate_id).delete(
            synchronize_session=False
        )
        db.query(Role).filter(Role.id == world.role_id).delete(
            synchronize_session=False
        )
        db.query(Organization).filter(Organization.id == world.organization_id).delete(
            synchronize_session=False
        )
        db.commit()


def _queue_resend(
    factory,
    world: _World,
    barrier: Barrier,
    *,
    run_id: int,
    assessment_id: int,
) -> tuple[int, bool]:
    with factory() as db:
        barrier.wait(timeout=10)
        decision = queue_decision.run(
            db,
            Actor.agent(run_id),
            organization_id=world.organization_id,
            role_id=world.role_id,
            application_id=world.application_id,
            decision_type="resend_assessment_invite",
            reasoning=f"Resend assessment {assessment_id}",
            evidence={"assessment_id": assessment_id},
            confidence=0.8,
            model_version="test",
            prompt_version="test",
            idempotency_key_suffix=f"assess{assessment_id}",
            skip_episode=True,
        )
        result = (
            int(decision.id),
            bool(getattr(decision, "_just_created", False)),
        )
        db.commit()
        return result


def test_concurrent_resends_share_one_role_application_slot(
    postgres_search_engine,
):
    """Distinct assessments and agent runs cannot create duplicate cards."""

    factory = sessionmaker(bind=postgres_search_engine, expire_on_commit=False)
    world = _seed_world(factory)
    barrier = Barrier(2)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _queue_resend,
                    factory,
                    world,
                    barrier,
                    run_id=run_id,
                    assessment_id=assessment_id,
                )
                for run_id, assessment_id in zip(
                    world.run_ids,
                    (9001, 9002),
                    strict=True,
                )
            ]
            results = [future.result(timeout=15) for future in futures]

        assert len({decision_id for decision_id, _created in results}) == 1
        assert sorted(created for _decision_id, created in results) == [False, True]
        with factory() as check:
            active = (
                check.query(AgentDecision)
                .filter(
                    AgentDecision.organization_id == world.organization_id,
                    AgentDecision.role_id == world.role_id,
                    AgentDecision.application_id == world.application_id,
                    AgentDecision.status.in_(AGENT_DECISION_ACTIVE_STATUSES),
                )
                .all()
            )
            assert len(active) == 1
            assert active[0].decision_type == "resend_assessment_invite"
            assert active[0].evidence in (
                {"assessment_id": 9001},
                {"assessment_id": 9002},
            )
    finally:
        _cleanup(factory, world)


def _insert_without_admission_lock(
    factory,
    world: _World,
    barrier: Barrier,
    *,
    run_id: int,
    decision_type: str,
) -> str:
    """Model a legacy/direct producer that bypasses the canonical service."""

    with factory() as db:
        barrier.wait(timeout=10)
        db.add(
            AgentDecision(
                organization_id=world.organization_id,
                role_id=world.role_id,
                application_id=world.application_id,
                agent_run_id=run_id,
                decision_type=decision_type,
                recommendation=decision_type,
                status="pending",
                reasoning=f"Concurrent direct {decision_type}",
                evidence=(
                    {"assessment_id": 9101}
                    if decision_type == "resend_assessment_invite"
                    else None
                ),
                confidence=0.8,
                model_version="test",
                prompt_version="test",
                idempotency_key=f"direct-race:{run_id}:{decision_type}",
            )
        )
        try:
            db.commit()
            return "created"
        except IntegrityError:
            db.rollback()
            return "conflict"


def test_database_slot_serializes_legacy_cross_type_race(
    postgres_search_engine,
):
    """The DB invariant covers bypass writers and ignores decision type."""

    factory = sessionmaker(bind=postgres_search_engine, expire_on_commit=False)
    world = _seed_world(factory)
    barrier = Barrier(2)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _insert_without_admission_lock,
                    factory,
                    world,
                    barrier,
                    run_id=run_id,
                    decision_type=decision_type,
                )
                for run_id, decision_type in zip(
                    world.run_ids,
                    ("resend_assessment_invite", "reject"),
                    strict=True,
                )
            ]
            assert sorted(future.result(timeout=15) for future in futures) == [
                "conflict",
                "created",
            ]

        with factory() as check:
            active = (
                check.query(AgentDecision)
                .filter(
                    AgentDecision.organization_id == world.organization_id,
                    AgentDecision.role_id == world.role_id,
                    AgentDecision.application_id == world.application_id,
                    AgentDecision.status.in_(AGENT_DECISION_ACTIVE_STATUSES),
                )
                .all()
            )
            assert len(active) == 1
            assert active[0].decision_type in {
                "resend_assessment_invite",
                "reject",
            }
    finally:
        _cleanup(factory, world)
