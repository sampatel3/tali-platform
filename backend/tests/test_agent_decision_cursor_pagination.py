"""Keyset pagination contracts for ``GET /agent-decisions``."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers


def _seed_decisions(
    db, *, organization_id: int, status: str = "approved"
) -> tuple[Role, list[AgentDecision]]:
    role = Role(
        organization_id=organization_id,
        name="Cursor contract",
        source="manual",
        agentic_mode_enabled=True,
    )
    candidate = Candidate(
        organization_id=organization_id,
        email="decision-cursor@example.test",
        full_name="Decision Cursor",
    )
    db.add_all([role, candidate])
    db.flush()
    application = CandidateApplication(
        organization_id=organization_id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="system",
        application_outcome="open",
        source="manual",
    )
    db.add(application)
    db.flush()
    shared_created_at = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    decisions = [
        AgentDecision(
            organization_id=organization_id,
            role_id=role.id,
            application_id=application.id,
            decision_type="advance_to_interview",
            recommendation="advance_to_interview",
            status=status,
            reasoning="cursor contract",
            model_version="cursor-test",
            prompt_version="cursor-test",
            idempotency_key=f"cursor-contract:{index}",
            created_at=shared_created_at,
        )
        for index in range(5)
    ]
    db.add_all(decisions)
    db.commit()
    return role, decisions


def test_decision_cursor_walks_timestamp_ties_without_gaps_or_duplicates(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    role, decisions = _seed_decisions(db, organization_id=int(user.organization_id))

    first = client.get(
        "/api/v1/agent-decisions",
        params={"status": "all", "role_id": role.id, "limit": 2},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    first_rows = first.json()
    assert [row["id"] for row in first_rows] == sorted(
        (decision.id for decision in decisions), reverse=True
    )[:2]

    last = first_rows[-1]
    second = client.get(
        "/api/v1/agent-decisions",
        params={
            "status": "all",
            "role_id": role.id,
            "limit": 200,
            "before_created_at": last["created_at"],
            "before_id": last["id"],
        },
        headers=headers,
    )
    assert second.status_code == 200, second.text
    walked_ids = [row["id"] for row in first_rows + second.json()]
    assert walked_ids == sorted((decision.id for decision in decisions), reverse=True)
    assert len(walked_ids) == len(set(walked_ids))


def test_decision_cursor_requires_the_complete_ordering_pair(client, db):
    headers, _ = auth_headers(client)

    incomplete = client.get(
        "/api/v1/agent-decisions",
        params={"status": "all", "before_id": 1},
        headers=headers,
    )

    assert incomplete.status_code == 422
    assert "must be supplied together" in incomplete.json()["detail"]


def test_execution_snapshot_cursor_is_complete_and_keeps_payload_bounded(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    role, decisions = _seed_decisions(
        db,
        organization_id=int(user.organization_id),
        status="pending",
    )

    first = client.get(
        "/api/v1/agent-decisions/execution-snapshots",
        params={"role_id": role.id, "limit": 2},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    first_rows = first.json()
    assert set(first_rows[0]) == {
        "id",
        "role_id",
        "application_id",
        "decision_type",
        "recommendation",
        "status",
        "created_at",
        "candidate_name",
        "role_family",
        "workable_job_id",
        "workable_stage",
    }

    last = first_rows[-1]
    second = client.get(
        "/api/v1/agent-decisions/execution-snapshots",
        params={
            "role_id": role.id,
            "limit": 2,
            "before_created_at": last["created_at"],
            "before_id": last["id"],
        },
        headers=headers,
    )
    assert second.status_code == 200, second.text
    second_rows = second.json()
    assert len(second_rows) == 2

    tail = second_rows[-1]
    third = client.get(
        "/api/v1/agent-decisions/execution-snapshots",
        params={
            "role_id": role.id,
            "limit": 2,
            "before_created_at": tail["created_at"],
            "before_id": tail["id"],
        },
        headers=headers,
    )
    assert third.status_code == 200, third.text
    walked_ids = [row["id"] for row in first_rows + second_rows + third.json()]
    assert walked_ids == sorted((decision.id for decision in decisions), reverse=True)
    assert len(walked_ids) == len(set(walked_ids))


def test_execution_snapshots_do_not_disclose_another_tenants_role(client, db):
    headers, _ = auth_headers(client)
    other_org = Organization(
        name="Other decision snapshot tenant",
        slug=f"other-decision-snapshot-{id(db)}",
    )
    db.add(other_org)
    db.flush()
    role, _ = _seed_decisions(
        db,
        organization_id=int(other_org.id),
        status="pending",
    )

    response = client.get(
        "/api/v1/agent-decisions/execution-snapshots",
        params={"role_id": role.id},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json() == []
