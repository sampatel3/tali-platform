"""Canonical role/JD/criteria conversion across scoring entry points."""

from __future__ import annotations

from app.cv_matching.schemas import Priority
from app.models.role import Role
from app.models.role_criterion import RoleCriterion
from app.services.role_requirement_service import (
    build_scoring_requirements,
    resolve_role_job_spec,
)


def test_constraint_keeps_constraint_semantics(db):
    role = Role(organization_id=1, name="R", source="manual", job_spec_text="JD")
    role.criteria = [
        RoleCriterion(id=11, text="Must be in UAE", bucket="constraint", ordering=0),
        RoleCriterion(id=12, text="Python", bucket="must", must_have=True, ordering=1),
        RoleCriterion(id=13, text="Kafka", bucket="preferred", ordering=2),
    ]

    requirements = build_scoring_requirements(role)

    assert [item.priority for item in requirements] == [
        Priority.CONSTRAINT,
        Priority.MUST_HAVE,
        Priority.STRONG_PREFERENCE,
    ]


def test_job_spec_never_falls_back_to_marketing_description():
    role = Role(name="R", description="marketing copy", job_spec_text=None)

    assert resolve_role_job_spec(role) == ""


def test_recruiter_overlays_are_stable_and_preserved():
    role = Role(name="R", job_spec_text="Canonical JD")
    left = resolve_role_job_spec(
        role,
        role_intent={"free_text": "Prioritise ownership", "version": 2},
        exemplars_text="Recruiter corrected an under-score.",
    )
    right = resolve_role_job_spec(
        role,
        role_intent={"version": 2, "free_text": "Prioritise ownership"},
        exemplars_text="Recruiter corrected an under-score.",
    )

    assert left == right
    assert left.startswith("Canonical JD")
    assert "Prioritise ownership" in left
    assert "Recruiter corrected an under-score." in left
