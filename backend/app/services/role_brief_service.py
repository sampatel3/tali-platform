"""Requisition: hiring-brief service (create / update / submit / materialize).

The intake agent and the recruiter both edit a RoleBrief through ``update_brief_fields``;
``materialize_brief_to_role`` turns a finished brief into a real role (name +
description now; criteria + knockouts in the follow-up). Mutators flush but do
NOT commit — the caller owns the transaction.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.role import Role
from ..models.role_brief import (
    BRIEF_SOURCES,
    BRIEF_STATUS_APPLIED,
    BRIEF_STATUS_SUBMITTED,
    RoleBrief,
)

# Fields the agent / recruiter may set on a brief.
_EDITABLE_FIELDS = frozenset(
    {
        "source_kind",
        "title",
        "summary",
        "department",
        "location_city",
        "location_country",
        "workplace_type",
        "employment_type",
        "seniority",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
        "openings",
        "target_start",
        "must_haves",
        "preferred",
        "dealbreakers",
        "success_profile",
        "priorities",
        "tradeoffs",
        "calibration_exemplars",
        "sourcing_signals",
        "assessment_focus",
        "process",
        "evp",
        "raw_input",
        "agent_state",
        "completeness",
    }
)


def create_brief(
    db: Session,
    *,
    organization_id: int,
    created_by_user_id: int | None = None,
    source_kind: str | None = None,
) -> RoleBrief:
    if source_kind is not None and source_kind not in BRIEF_SOURCES:
        raise HTTPException(status_code=422, detail=f"Unsupported source_kind={source_kind!r}")
    brief = RoleBrief(
        organization_id=organization_id,
        created_by_user_id=created_by_user_id,
        source_kind=source_kind,
    )
    db.add(brief)
    db.flush()
    return brief


def update_brief_fields(db: Session, brief: RoleBrief, **fields) -> RoleBrief:
    """Set whitelisted brief fields (ignores unknown keys). Used by the intake
    agent's incremental fills and by recruiter edits."""
    if brief.status == BRIEF_STATUS_APPLIED:
        raise HTTPException(status_code=409, detail="Brief already applied to a role")
    if "source_kind" in fields and fields["source_kind"] not in (None, *BRIEF_SOURCES):
        raise HTTPException(
            status_code=422, detail=f"Unsupported source_kind={fields['source_kind']!r}"
        )
    for key, value in fields.items():
        if key in _EDITABLE_FIELDS:
            setattr(brief, key, value)
    db.flush()
    return brief


def submit_brief(db: Session, brief: RoleBrief) -> RoleBrief:
    """Hiring manager finished the intake; ready for recruiter review."""
    if brief.status != BRIEF_STATUS_APPLIED:
        brief.status = BRIEF_STATUS_SUBMITTED
    db.flush()
    return brief


def materialize_brief_to_role(db: Session, brief: RoleBrief) -> Role:
    """Create (or update) the role this brief describes and mark the brief
    applied. Name + description now; role_criterion + knockout materialization is
    the follow-up step."""
    if brief.role_id:
        role = (
            db.query(Role)
            .filter(Role.id == brief.role_id, Role.organization_id == brief.organization_id)
            .first()
        )
        if role is None:
            raise HTTPException(status_code=404, detail="Linked role not found")
    else:
        role = Role(
            organization_id=brief.organization_id,
            name=(brief.title or "Untitled role"),
            source="requisition",
        )
        db.add(role)
        db.flush()
        brief.role_id = role.id
    if brief.title:
        role.name = brief.title
    if brief.summary:
        role.description = brief.summary
    brief.status = BRIEF_STATUS_APPLIED
    db.flush()
    return role
