"""Bounded, constant-query public careers-board reads."""

from __future__ import annotations

from sqlalchemy import and_, or_
from sqlalchemy.orm import Load, Session

from ...models.job_page import JOB_PAGE_STATUS_OPEN, JobPage
from ...models.role import JOB_STATUS_OPEN, Role
from ...models.role_brief import RoleBrief
from ...platform.config import settings
from ...services.job_page_lifecycle import role_accepts_native_applications


def list_public_careers_pages(
    db: Session,
    *,
    organization_id: int,
    limit: int,
    offset: int,
) -> tuple[list[JobPage], bool, int | None]:
    """Return one raw-offset page of live public jobs in a single SQL query.

    The core native lifecycle is filtered in SQL. Provider payload edge cases
    retain the canonical Python policy, but roles arrive in the same query, so
    they cannot recreate the former two relationship lookups per job.
    """
    if not settings.ATS_PUBLIC_APPLY_ENABLED:
        return [], False, None

    rows = (
        db.query(JobPage, Role)
        .join(RoleBrief, RoleBrief.id == JobPage.brief_id)
        .join(
            Role,
            and_(
                Role.id == RoleBrief.role_id,
                Role.organization_id == organization_id,
            ),
        )
        .options(
            Load(JobPage).load_only(
                JobPage.id,
                JobPage.token,
                JobPage.title,
                JobPage.location,
                JobPage.workplace_type,
                JobPage.employment_type,
                JobPage.seniority,
                JobPage.salary_min,
                JobPage.salary_max,
                JobPage.salary_currency,
                JobPage.published_at,
            ),
            Load(Role).load_only(
                Role.id,
                Role.deleted_at,
                Role.job_status,
                Role.agentic_mode_enabled,
                Role.agent_paused_at,
                Role.workable_job_id,
                Role.workable_job_data,
                Role.bullhorn_job_order_id,
                Role.bullhorn_job_data,
            ),
        )
        .filter(
            JobPage.organization_id == organization_id,
            JobPage.status == JOB_PAGE_STATUS_OPEN,
            Role.deleted_at.is_(None),
            or_(
                Role.job_status.is_(None),
                and_(
                    Role.job_status == JOB_STATUS_OPEN,
                    Role.agentic_mode_enabled.is_(True),
                    Role.agent_paused_at.is_(None),
                ),
            ),
        )
        .order_by(JobPage.published_at.desc(), JobPage.id.desc())
        .offset(offset)
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    pages = [page for page, role in page_rows if role_accepts_native_applications(role)]
    return pages, has_more, offset + limit if has_more else None


__all__ = ["list_public_careers_pages"]
