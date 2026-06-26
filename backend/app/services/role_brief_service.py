"""Requisition: hiring-brief service (create / update / submit / materialize).

The intake agent and the recruiter both edit a RoleBrief through ``update_brief_fields``;
``materialize_brief_to_role`` turns a finished brief into a real role (name +
description now; criteria + knockouts in the follow-up). Mutators flush but do
NOT commit — the caller owns the transaction.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.job_page import JOB_PAGE_STATUS_OPEN, JobPage
from ..models.org_criterion import BUCKET_CONSTRAINT, BUCKET_MUST, BUCKET_PREFERRED
from ..models.role import Role
from ..models.role_criterion import CRITERION_SOURCE_RECRUITER, RoleCriterion
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
        "client_id",
        "client_rate",
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
        "custom_fields",
        "messages",
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


def _criterion_text(item) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("text") or item.get("label") or "").strip()
    return str(item).strip()


def _materialize_criteria(db: Session, brief: RoleBrief, role: Role) -> None:
    """Create role_criterion rows from the brief's must_haves / preferred /
    dealbreakers (-> must / preferred / constraint buckets) so the published role
    is immediately scoreable. Idempotent: skips if the role already has criteria,
    so re-publishing never duplicates. (Dealbreakers also become knockout
    questions once screening_questions reaches prod.)"""
    has_any = (
        db.query(RoleCriterion.id)
        .filter(RoleCriterion.role_id == role.id, RoleCriterion.deleted_at.is_(None))
        .first()
    )
    if has_any:
        return
    ordering = 0
    for items, bucket, must in (
        (brief.must_haves, BUCKET_MUST, True),
        (brief.preferred, BUCKET_PREFERRED, False),
        (brief.dealbreakers, BUCKET_CONSTRAINT, False),
    ):
        for item in items or []:
            text = _criterion_text(item)
            if not text:
                continue
            db.add(
                RoleCriterion(
                    role_id=role.id,
                    text=text,
                    bucket=bucket,
                    must_have=must,
                    source=CRITERION_SOURCE_RECRUITER,
                    ordering=ordering,
                )
            )
            ordering += 1
    db.flush()


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
    _materialize_criteria(db, brief, role)
    brief.status = BRIEF_STATUS_APPLIED
    db.flush()
    return role


def _brief_location(brief: RoleBrief) -> str | None:
    """Public location string: "City, Country" from the brief's parts. Omits a
    missing half (so just a city or just a country still renders), returns None
    when neither is set."""
    parts = [
        (brief.location_city or "").strip(),
        (brief.location_country or "").strip(),
    ]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def publish_job_page(db: Session, brief: RoleBrief, *, jd_markdown: str) -> JobPage:
    """Create or refresh the PUBLIC job page for this brief and return it.

    Idempotent — one JobPage per ``brief_id``. The first publish mints an
    unguessable ``token`` (the public address); a re-publish reuses it and just
    refreshes the snapshot. Only PUBLIC-safe fields are copied — NEVER the
    consultancy ``client_id`` / ``client_rate`` / margin. The brief's own
    ``status`` is deliberately left untouched so it stays editable for re-publish
    (unlike ``materialize_brief_to_role``). Flushes but does not commit.
    """
    page = (
        db.query(JobPage)
        .filter(
            JobPage.brief_id == brief.id,
            JobPage.organization_id == brief.organization_id,
        )
        .first()
    )
    if page is None:
        page = JobPage(
            organization_id=brief.organization_id,
            brief_id=brief.id,
            token=secrets.token_urlsafe(8),
        )
        db.add(page)

    page.jd_markdown = jd_markdown
    page.title = brief.title
    page.location = _brief_location(brief)
    page.workplace_type = brief.workplace_type
    page.employment_type = brief.employment_type
    page.seniority = brief.seniority
    page.salary_min = brief.salary_min
    page.salary_max = brief.salary_max
    page.salary_currency = brief.salary_currency
    page.status = JOB_PAGE_STATUS_OPEN
    page.published_at = datetime.now(timezone.utc)
    db.flush()
    return page
