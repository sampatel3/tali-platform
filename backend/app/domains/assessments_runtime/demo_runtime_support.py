"""Demo assessment organization, task, and lead persistence helpers."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.candidate import Candidate
from ...models.organization import Organization
from ...models.task import Task


DEMO_ORG_SLUG = "taali-demo"
DEMO_ORG_NAME = "TAALI Demo Leads"
DEMO_TRACK_TASK_KEYS = {
    # Primary demo tracks use the current flagship tasks. Historical track
    # names remain aliases so existing demo links keep working.
    "data_eng_bronze_ingestion": "data_eng_bronze_ingestion",
    "ai_eng_genai_production_readiness": "ai_eng_genai_production_readiness",
    "data_eng_aws_glue_pipeline_recovery": "data_eng_bronze_ingestion",
    "data_eng_super_platform_crisis": "data_eng_bronze_ingestion",
    "ai_eng_super_production_launch": "ai_eng_genai_production_readiness",
    "data_eng_a_pipeline_reliability": "data_eng_bronze_ingestion",
    "data_eng_b_cdc_fix": "data_eng_bronze_ingestion",
    "data_eng_c_backfill_schema": "data_eng_bronze_ingestion",
    "backend-reliability": "data_eng_bronze_ingestion",
    "frontend-debugging": "data_eng_bronze_ingestion",
    "data-pipeline": "data_eng_bronze_ingestion",
}
DEMO_TRACK_KEYS = set(DEMO_TRACK_TASK_KEYS)


def ensure_demo_org(db: Session) -> Organization:
    org = db.query(Organization).filter(Organization.slug == DEMO_ORG_SLUG).first()
    if org:
        return org

    org = Organization(name=DEMO_ORG_NAME, slug=DEMO_ORG_SLUG, plan="pay_per_use")
    db.add(org)
    try:
        db.commit()
    except Exception:
        db.rollback()
        org = db.query(Organization).filter(Organization.slug == DEMO_ORG_SLUG).first()
        if org:
            return org
        raise HTTPException(
            status_code=500,
            detail="Failed to initialize demo organization",
        )

    db.refresh(org)
    return org


def resolve_demo_task(db: Session, org_id: int, track: str) -> Task | None:
    task_key = DEMO_TRACK_TASK_KEYS.get(track)
    if not task_key:
        return None

    org_task = (
        db.query(Task)
        .filter(
            Task.is_active == True,  # noqa: E712
            Task.organization_id == org_id,
            Task.task_key == task_key,
        )
        .order_by(Task.id.asc())
        .first()
    )
    if org_task:
        return org_task

    return (
        db.query(Task)
        .filter(
            Task.is_active == True,  # noqa: E712
            Task.organization_id == None,  # noqa: E711
            Task.task_key == task_key,
        )
        .order_by(Task.id.asc())
        .first()
    )


def upsert_demo_candidate(
    *,
    db: Session,
    org_id: int,
    full_name: str,
    position: str | None,
    email: str,
    work_email: str | None,
    company_name: str,
    company_size: str,
    marketing_consent: bool,
    lead_source: str,
    workable_data_updates: dict[str, object] | None = None,
) -> Candidate:
    normalized_email = str(email).strip().lower()
    normalized_work_email = str(work_email).strip().lower() if work_email else None

    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.organization_id == org_id,
            Candidate.email == normalized_email,
        )
        .first()
    )
    if not candidate:
        candidate = Candidate(
            organization_id=org_id,
            email=normalized_email,
        )
        db.add(candidate)
        db.flush()

    existing_workable_data = (
        candidate.workable_data
        if isinstance(candidate.workable_data, dict)
        else {}
    )
    candidate.full_name = full_name
    candidate.position = position
    candidate.work_email = normalized_work_email
    candidate.company_name = company_name
    candidate.company_size = company_size
    candidate.lead_source = lead_source
    candidate.marketing_consent = bool(marketing_consent)
    candidate.workable_data = {
        **existing_workable_data,
        **(workable_data_updates or {}),
    }
    return candidate


__all__ = [
    "DEMO_ORG_NAME",
    "DEMO_ORG_SLUG",
    "DEMO_TRACK_KEYS",
    "DEMO_TRACK_TASK_KEYS",
    "ensure_demo_org",
    "resolve_demo_task",
    "upsert_demo_candidate",
]
