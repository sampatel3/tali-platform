"""Regression tests for bounded, stable collection reads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from app.components.scoring.assessment_metrics import score_10, score_100
from app.domains.assessments_runtime.analytics_routes import (
    _build_dimension_averages,
    _build_score_buckets,
)
from app.domains.assessments_runtime.base_analytics_queries import (
    get_base_analytics_summary,
)
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.job_page import JobPage
from app.models.organization import Organization
from app.models.role import JOB_STATUS_OPEN, Role
from app.models.role_brief import RoleBrief
from app.models.task import Task
from app.models.user import User
from tests.conftest import auth_headers


def _org_id(db, email: str) -> int:
    return int(db.query(User).filter(User.email == email).one().organization_id)


def _task(org_id: int, index: int, **overrides) -> Task:
    values = {
        "organization_id": org_id,
        "name": f"Task {index:03d}",
        "description": f"Collection task {index}",
        "task_type": "python",
        "difficulty": "medium",
        "duration_minutes": 30,
        "is_template": False,
        "is_active": True,
    }
    values.update(overrides)
    return Task(**values)


def test_role_and_task_defaults_are_bounded_with_stable_later_pages(client, db):
    headers, email = auth_headers(client, organization_name="Bounded Lists")
    org_id = _org_id(db, email)
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    roles = [
        Role(
            organization_id=org_id,
            name=f"Role {index:03d}",
            source="manual",
            created_at=stamp,
            updated_at=stamp,
        )
        for index in range(55)
    ]
    tasks = [_task(org_id, index) for index in range(55)]
    db.add_all([*roles, *tasks])
    db.commit()

    first_roles = client.get("/api/v1/roles?include_total=true", headers=headers)
    later_roles = client.get("/api/v1/roles?limit=10&offset=50", headers=headers)
    assert first_roles.status_code == later_roles.status_code == 200
    assert len(first_roles.json()) == 50
    assert first_roles.headers["x-total-count"] == "55"
    assert len(later_roles.json()) == 5
    role_ids = [row["id"] for row in first_roles.json() + later_roles.json()]
    assert role_ids == sorted(role_ids, reverse=True)
    assert len(set(role_ids)) == 55

    first_tasks = client.get("/api/v1/tasks/", headers=headers)
    later_tasks = client.get("/api/v1/tasks/?limit=10&offset=50", headers=headers)
    assert first_tasks.status_code == later_tasks.status_code == 200
    assert len(first_tasks.json()) == 50
    assert [row["name"] for row in later_tasks.json()] == [f"Task {i:03d}" for i in range(50, 55)]
    assert client.get("/api/v1/tasks/?search=Task%20054", headers=headers).json()[0]["name"] == "Task 054"

    for url in ("/api/v1/roles?limit=101", "/api/v1/tasks/?limit=101"):
        assert client.get(url, headers=headers).status_code == 422


def test_generated_drafts_filter_in_sql_and_requisition_list_is_summary_paged(client, db):
    headers, email = auth_headers(client, organization_name="Summary Lists")
    org_id = _org_id(db, email)
    db.add_all([
        _task(org_id, 1, name="Generated", is_active=False, extra_data={"generated": True}),
        _task(org_id, 2, name="Manual inactive", is_active=False, extra_data={"generated": False}),
        *[
            RoleBrief(
                organization_id=org_id,
                title=f"Brief {index:03d}",
                status="draft",
                completeness=index,
                messages=[{"role": "user", "content": "confidential history"}],
            )
            for index in range(30)
        ],
    ])
    db.commit()

    drafts = client.get("/api/v1/tasks/drafts", headers=headers).json()
    assert [row["name"] for row in drafts] == ["Generated"]

    first = client.get("/api/v1/requisitions", headers=headers)
    later = client.get("/api/v1/requisitions?limit=10&offset=25", headers=headers)
    assert first.status_code == later.status_code == 200
    assert len(first.json()) == 25
    assert len(later.json()) == 5
    summary_keys = {
        "id",
        "source_role_id",
        "brief_kind",
        "title",
        "status",
        "completeness",
    }
    assert all(set(row) == summary_keys for row in first.json())
    assert all(row["source_role_id"] is None for row in first.json())
    assert all(row["brief_kind"] == "standard" for row in first.json())
    detail = client.get(f"/api/v1/requisitions/{later.json()[0]['id']}", headers=headers).json()
    assert detail["messages"] == [{"role": "user", "content": "confidential history"}]
    assert client.get("/api/v1/requisitions?limit=101", headers=headers).status_code == 422


def test_task_filters_use_complete_paginated_sql_facets(client, db):
    headers, email = auth_headers(client, organization_name="Task Facets")
    org_id = _org_id(db, email)
    db.add_all([
        _task(org_id, 1, role="Alpha", difficulty="Easy", task_type="Debug"),
        _task(org_id, 2, role="Beta", difficulty="Hard", task_type="Repo"),
        _task(org_id, 3, role=None, difficulty=None, task_type=None),
    ])
    db.commit()

    first = client.get("/api/v1/tasks/facets?limit=1", headers=headers)
    second = client.get("/api/v1/tasks/facets?limit=1&offset=1", headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["has_more"] is True
    assert first.json()["next_offset"] == 1
    assert first.json()["roles"] == ["Alpha"]
    assert second.json()["roles"] == ["Beta"]

    filtered = client.get(
        "/api/v1/tasks/?role=Beta&difficulty=hard&task_type=repo",
        headers=headers,
    )
    assert [row["name"] for row in filtered.json()] == ["Task 002"]
    defaults = client.get("/api/v1/tasks/?role=General%20engineering", headers=headers)
    assert [row["name"] for row in defaults.json()] == ["Task 003"]
    assert client.get("/api/v1/tasks/facets?limit=201", headers=headers).status_code == 422


def test_careers_board_is_bounded_and_constant_query_count(client, db, monkeypatch):
    headers, email = auth_headers(client, organization_name="Careers Scale")
    del headers
    org_id = _org_id(db, email)
    org = db.query(Organization).filter(Organization.id == org_id).one()
    monkeypatch.setattr(
        "app.domains.job_pages.careers_board_queries.settings.ATS_PUBLIC_APPLY_ENABLED",
        True,
    )
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(30):
        role = Role(
            organization_id=org_id,
            name=f"Public {index:03d}",
            source="requisition",
            job_status=JOB_STATUS_OPEN,
            agentic_mode_enabled=True,
        )
        db.add(role)
        db.flush()
        brief = RoleBrief(organization_id=org_id, role_id=role.id, title=role.name)
        db.add(brief)
        db.flush()
        db.add(JobPage(
            organization_id=org_id,
            brief_id=brief.id,
            token=f"public-token-{index}",
            title=role.name,
            status="open",
            published_at=stamp + timedelta(minutes=index),
        ))
    db.commit()
    slug = org.slug

    statements: list[str] = []
    engine = db.get_bind()

    def record(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        first = client.get(f"/api/v1/public/careers/{slug}")
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert first.status_code == 200, first.text
    assert len(first.json()["jobs"]) == 24
    assert first.json()["has_more"] is True
    assert first.json()["next_offset"] == 24
    assert len(statements) <= 2, statements

    later = client.get(f"/api/v1/public/careers/{slug}?limit=10&offset=24")
    assert len(later.json()["jobs"]) == 6
    assert later.json()["has_more"] is False
    assert client.get(f"/api/v1/public/careers/{slug}?limit=101").status_code == 422


def test_base_analytics_sql_aggregate_matches_canonical_helpers_in_one_query(db):
    org = Organization(name="Analytics Aggregate", slug="analytics-aggregate")
    db.add(org)
    db.flush()
    candidate_a = Candidate(organization_id=org.id, email="a@example.com", full_name="A")
    candidate_b = Candidate(organization_id=org.id, email="b@example.com", full_name="B")
    task_a = _task(org.id, 1)
    task_b = _task(org.id, 2)
    db.add_all([candidate_a, candidate_b, task_a, task_b])
    db.flush()
    org_id = int(org.id)
    now = datetime.now(timezone.utc)
    completed = [
        Assessment(
            organization_id=org.id,
            candidate_id=candidate_a.id,
            task_id=task_a.id,
            status=AssessmentStatus.COMPLETED,
            started_at=now - timedelta(minutes=45),
            completed_at=now - timedelta(minutes=15),
            taali_score=82.3,
            score_breakdown={"category_scores": {"task_completion": 8.0, "independence": 7.0}},
        ),
        Assessment(
            organization_id=org.id,
            candidate_id=candidate_b.id,
            task_id=task_b.id,
            status=AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
            started_at=now - timedelta(minutes=90),
            completed_at=now - timedelta(minutes=30),
            final_score=55.4,
            score_breakdown={"category_scores": {"task_completion": 6.0, "independence": 5.0}},
        ),
    ]
    pending = Assessment(
        organization_id=org.id,
        candidate_id=candidate_a.id,
        task_id=task_a.id,
        status=AssessmentStatus.IN_PROGRESS,
        started_at=now - timedelta(minutes=5),
    )
    db.add_all([*completed, pending])
    db.commit()

    scores_10 = [score_10(row) for row in completed]
    scores_100 = [score_100(row) for row in completed]
    statements: list[str] = []
    engine = db.get_bind()

    def record(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        summary = get_base_analytics_summary(db, organization_id=org_id, now=now)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(statements) == 1
    assert summary["total_assessments"] == 3
    assert summary["total_candidates"] == 2
    assert summary["total_tasks"] == 2
    assert summary["completed_count"] == 2
    assert summary["timed_out_count"] == 1
    assert summary["top_score"] == max(scores_10)
    assert summary["avg_score"] == round(sum(scores_10) / len(scores_10), 1)
    assert summary["avg_time_minutes"] == 45
    assert summary["score_buckets"] == _build_score_buckets(scores_100)
    assert summary["dimension_averages"] == _build_dimension_averages(completed)
