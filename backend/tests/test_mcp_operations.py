"""Focused tests for the safe MCP/chat operational read pack."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app.mcp.operations import get_recruiting_overview, list_assessments
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.shared.utils import utcnow


def _role(db, organization_id: int, name: str, **values) -> Role:
    role = Role(
        organization_id=organization_id,
        name=name,
        source="manual",
        **values,
    )
    db.add(role)
    db.flush()
    return role


def _assessment(
    db,
    *,
    organization_id: int,
    role: Role,
    task: Task,
    label: str,
    status: AssessmentStatus,
    created_at,
    pipeline_stage: str,
    expires_at=None,
    invite_email_status: str | None = None,
    scored_at=None,
    scoring_failed: bool = False,
    is_voided: bool = False,
) -> Assessment:
    candidate = Candidate(
        organization_id=organization_id,
        full_name=f"{label} Candidate",
        email=f"{label.lower()}@example.test",
        cv_text=f"RAW_CV_BODY_{label}",
        cv_file_url=f"/private/cvs/{label}.pdf",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=organization_id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage=pipeline_stage,
        application_outcome="open",
        source="manual",
        cv_text=f"RAW_APPLICATION_CV_{label}",
        cv_file_url=f"/private/application-cvs/{label}.pdf",
    )
    db.add(application)
    db.flush()
    assessment = Assessment(
        organization_id=organization_id,
        candidate_id=candidate.id,
        application_id=application.id,
        role_id=role.id,
        task_id=task.id,
        token=f"ASSESSMENT_TOKEN_{label}",
        status=status,
        expires_at=expires_at,
        invite_email_status=invite_email_status,
        scored_at=scored_at,
        scoring_failed=scoring_failed,
        is_voided=is_voided,
        created_at=created_at,
        cli_transcript=[{"secret": f"TRANSCRIPT_{label}"}],
        clone_command=f"git clone https://secret.example/{label}",
    )
    db.add(assessment)
    db.flush()
    return assessment


@pytest.fixture
def operations_data(db):
    now = utcnow()
    org = Organization(name="Operations Org", slug="operations-org")
    other_org = Organization(name="Other Org", slug="operations-other")
    db.add_all([org, other_org])
    db.flush()

    backend = _role(db, org.id, "Backend Engineer")
    data_role = _role(db, org.id, "Data Engineer")
    _role(db, org.id, "Deleted Role", deleted_at=now)
    other_role = _role(db, other_org.id, "Other Tenant Role")
    task = Task(organization_id=org.id, name="Operational exercise", is_active=True)
    other_task = Task(
        organization_id=other_org.id,
        name="Other exercise",
        is_active=True,
    )
    db.add_all([task, other_task])
    db.flush()

    rows = {
        "expiring": _assessment(
            db,
            organization_id=org.id,
            role=backend,
            task=task,
            label="Expiring",
            status=AssessmentStatus.PENDING,
            created_at=now - timedelta(minutes=1),
            pipeline_stage="invited",
            expires_at=now + timedelta(days=1),
        ),
        "safe": _assessment(
            db,
            organization_id=org.id,
            role=backend,
            task=task,
            label="Safe",
            status=AssessmentStatus.IN_PROGRESS,
            created_at=now - timedelta(minutes=2),
            pipeline_stage="in_assessment",
            expires_at=now + timedelta(days=10),
        ),
        "delivery": _assessment(
            db,
            organization_id=org.id,
            role=backend,
            task=task,
            label="Delivery",
            status=AssessmentStatus.PENDING,
            created_at=now - timedelta(minutes=3),
            pipeline_stage="invited",
            expires_at=now + timedelta(days=10),
            invite_email_status="bounced",
        ),
        "scoring": _assessment(
            db,
            organization_id=org.id,
            role=data_role,
            task=task,
            label="Scoring",
            status=AssessmentStatus.COMPLETED,
            created_at=now - timedelta(minutes=4),
            pipeline_stage="review",
        ),
        "failed": _assessment(
            db,
            organization_id=org.id,
            role=data_role,
            task=task,
            label="Failed",
            status=AssessmentStatus.COMPLETED,
            created_at=now - timedelta(minutes=5),
            pipeline_stage="review",
            scored_at=now - timedelta(minutes=4),
            scoring_failed=True,
        ),
        "voided": _assessment(
            db,
            organization_id=org.id,
            role=backend,
            task=task,
            label="Voided",
            status=AssessmentStatus.PENDING,
            created_at=now - timedelta(minutes=6),
            pipeline_stage="applied",
            expires_at=now + timedelta(days=1),
            is_voided=True,
        ),
        "other": _assessment(
            db,
            organization_id=other_org.id,
            role=other_role,
            task=other_task,
            label="OtherTenant",
            status=AssessmentStatus.COMPLETED,
            created_at=now,
            pipeline_stage="review",
            scoring_failed=True,
        ),
    }
    # Org overview candidate count is the directory count, not only candidates
    # who currently have a non-deleted application.
    db.add(
        Candidate(
            organization_id=org.id,
            full_name="Standalone Candidate",
            email="standalone@example.test",
            cv_text="STANDALONE_RAW_CV",
        )
    )
    db.commit()
    return {
        "org": org,
        "other_org": other_org,
        "backend": backend,
        "data_role": data_role,
        "rows": rows,
        "user": SimpleNamespace(organization_id=org.id),
    }


def test_list_assessments_is_org_scoped_compact_and_secret_free(db, operations_data):
    data = operations_data
    result = list_assessments(db, data["user"])

    assert result["total"] == 5
    assert [row["assessment_id"] for row in result["items"]] == [
        data["rows"][name].id
        for name in ("expiring", "safe", "delivery", "scoring", "failed")
    ]
    assert all("token" not in row for row in result["items"])
    assert all("cv_text" not in row for row in result["items"])
    serialized = json.dumps(result)
    assert "ASSESSMENT_TOKEN_" not in serialized
    assert "RAW_CV_BODY_" not in serialized
    assert "RAW_APPLICATION_CV_" not in serialized
    assert "TRANSCRIPT_" not in serialized
    assert "secret.example" not in serialized

    row = result["items"][0]
    assert row["frontend_url"].endswith(
        f"/candidates/{data['rows']['expiring'].application_id}"
        f"?from=jobs/{data['backend'].id}&tab=assessment"
    )
    assert row["role_url"].endswith(f"/jobs/{data['backend'].id}")
    assert result["frontend_url"].endswith("/assessments")


def test_list_assessments_attention_status_role_and_pagination(db, operations_data):
    data = operations_data
    rows = data["rows"]

    expected_attention = {
        rows[name].id for name in ("expiring", "delivery", "scoring", "failed")
    }
    needs_attention = list_assessments(
        db, data["user"], attention="needs_attention"
    )
    assert {row["assessment_id"] for row in needs_attention["items"]} == expected_attention

    no_attention = list_assessments(db, data["user"], attention="none")
    assert [row["assessment_id"] for row in no_attention["items"]] == [rows["safe"].id]

    for attention, expected_name in (
        ("expiring_soon", "expiring"),
        ("delivery_failed", "delivery"),
        ("scoring_pending", "scoring"),
        ("scoring_failed", "failed"),
    ):
        result = list_assessments(db, data["user"], attention=attention)
        assert [row["assessment_id"] for row in result["items"]] == [
            rows[expected_name].id
        ]
        assert attention in result["items"][0]["attention_reasons"]

    pending_for_role = list_assessments(
        db,
        data["user"],
        status="PENDING",
        role_id=data["backend"].id,
    )
    assert {row["assessment_id"] for row in pending_for_role["items"]} == {
        rows["expiring"].id,
        rows["delivery"].id,
    }

    page = list_assessments(db, data["user"], limit=2, offset=1)
    assert page["total"] == 5
    assert [row["assessment_id"] for row in page["items"]] == [
        rows["safe"].id,
        rows["delivery"].id,
    ]


def test_list_assessments_uses_canonical_bounded_score(db, operations_data):
    assessment = operations_data["rows"]["safe"]
    assessment.taali_score = 143.26
    db.commit()

    result = list_assessments(
        db,
        operations_data["user"],
        status=AssessmentStatus.IN_PROGRESS.value,
    )

    assert result["items"][0]["score_100"] == 100.0


def test_recruiting_overview_uses_org_and_optional_role_scope(db, operations_data):
    data = operations_data
    overview = get_recruiting_overview(db, data["user"])

    assert overview["roles"] == {"total": 2}
    assert overview["candidates"] == {"total": 7}
    assert overview["applications"]["total"] == 6
    assert overview["applications"]["open"] == 6
    assert overview["applications"]["pipeline_stages"] == {
        "sourced": 0,
        "applied": 1,
        "invited": 2,
        "in_assessment": 1,
        "review": 2,
        "advanced": 0,
    }
    assert overview["assessments"]["total"] == 5
    assert overview["assessments"]["statuses"]["pending"] == 2
    assert overview["assessments"]["statuses"]["in_progress"] == 1
    assert overview["assessments"]["statuses"]["completed"] == 2
    assert overview["assessments"]["needs_attention"] == 4
    assert overview["assessments"]["attention"] == {
        "expiring_soon": 1,
        "delivery_failed": 1,
        "scoring_pending": 1,
        "scoring_failed": 1,
    }
    assert overview["frontend_url"].endswith("/home")

    scoped = get_recruiting_overview(
        db, data["user"], role_id=data["backend"].id
    )
    assert scoped["scope"] == {
        "organization_id": data["org"].id,
        "role_id": data["backend"].id,
        "role_name": "Backend Engineer",
    }
    assert scoped["roles"] == {"total": 1}
    assert scoped["candidates"] == {"total": 4}
    assert scoped["applications"]["total"] == 4
    assert scoped["assessments"]["total"] == 3
    assert scoped["assessments"]["needs_attention"] == 2
    assert scoped["frontend_url"].endswith(f"/jobs/{data['backend'].id}")

    with pytest.raises(ValueError, match="not found"):
        get_recruiting_overview(
            db, data["user"], role_id=data["rows"]["other"].role_id
        )


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"status": "submitted"}, "status must be one of"),
        ({"attention": "urgent"}, "attention must be one of"),
        ({"limit": 0}, "limit must be between"),
        ({"limit": 101}, "limit must be between"),
        ({"offset": -1}, "offset must be at least"),
        ({"role_id": 0}, "role_id must be a positive integer"),
    ],
)
def test_list_assessments_validates_filters(db, operations_data, kwargs, message):
    with pytest.raises(ValueError, match=message):
        list_assessments(db, operations_data["user"], **kwargs)


def test_operations_use_bounded_queries_and_narrow_projections(db, operations_data):
    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement.lower())

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        result = list_assessments(db, operations_data["user"])
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert len(statements) == 2  # one COUNT + one projected page query
    assert len(result["items"]) == 5
    sql = "\n".join(statements)
    assert "assessments.token" not in sql
    assert "candidates.cv_text" not in sql
    assert "candidate_applications.cv_text" not in sql
    assert "assessments.cli_transcript" not in sql
    assert "assessments.clone_command" not in sql

    statements.clear()
    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        get_recruiting_overview(db, operations_data["user"])
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)
    assert len(statements) == 4  # role, application, candidate, assessment aggregates
