"""Detached provider-read helpers shared by Workable integration routes."""

from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...components.integrations.workable.service import WorkableService
from ...models.organization import Organization
from ...models.user import User

logger = logging.getLogger(__name__)


def get_org_for_user(db: Session, current_user: User) -> Organization:
    org = (
        db.query(Organization)
        .filter(Organization.id == current_user.organization_id)
        .first()
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


def assert_workable_connected(org: Organization) -> None:
    if (
        not org.workable_connected
        or not org.workable_access_token
        or not org.workable_subdomain
    ):
        raise HTTPException(status_code=400, detail="Workable is not connected")


def workable_client_snapshot(org: Organization) -> WorkableService | None:
    """Build a provider client from loaded scalars without retaining an ORM row."""
    if (
        not bool(org.workable_connected)
        or not str(org.workable_access_token or "").strip()
        or not str(org.workable_subdomain or "").strip()
    ):
        return None
    return WorkableService(
        access_token=str(org.workable_access_token),
        subdomain=str(org.workable_subdomain),
    )


def release_for_workable_provider(db: Session) -> None:
    """Release the request transaction before any remote provider call."""
    db.rollback()
    if db.in_transaction():
        raise RuntimeError("Workable provider call retained a database transaction")


def run_workable_diagnostic(client: WorkableService | None) -> dict:
    """Run the read-only Workable API diagnostic from a detached client."""
    if client is None:
        return {"error": "Workable not connected"}
    result: dict = {
        "jobs": {
            "count": 0,
            "first_job_keys": [],
            "first_shortcode": None,
            "first_id": None,
            "first_title": None,
        },
        "job_details": {
            "top_level_keys": [],
            "job_wrapper_keys": [],
            "details_keys": [],
        },
        "candidates": {
            "count": 0,
            "first_candidate_keys": [],
            "first_email": None,
            "first_stage": None,
        },
    }
    try:
        jobs = client.list_open_jobs()
        result["jobs"]["count"] = len(jobs)
        if jobs:
            first_job = jobs[0]
            result["jobs"]["first_job_keys"] = list(first_job.keys())
            result["jobs"]["first_shortcode"] = first_job.get("shortcode")
            result["jobs"]["first_id"] = first_job.get("id")
            result["jobs"]["first_title"] = first_job.get("title")
            shortcode = first_job.get("shortcode") or first_job.get("id")
            if shortcode:
                details = client.get_job_details(str(shortcode))
                if isinstance(details, dict):
                    result["job_details"]["top_level_keys"] = list(details.keys())
                    wrapped = details.get("job")
                    if isinstance(wrapped, dict):
                        result["job_details"]["job_wrapper_keys"] = list(
                            wrapped.keys()
                        )[:20]
                        detail_fields = wrapped.get("details")
                        if isinstance(detail_fields, dict):
                            result["job_details"]["details_keys"] = list(
                                detail_fields.keys()
                            )
                candidates = client.list_job_candidates(
                    str(shortcode),
                    paginate=True,
                    max_pages=2,
                )
                result["candidates"]["count"] = len(candidates)
                if candidates:
                    first_candidate = candidates[0]
                    result["candidates"]["first_candidate_keys"] = list(
                        first_candidate.keys()
                    )
                    result["candidates"]["first_email"] = first_candidate.get("email")
                    result["candidates"]["first_stage"] = first_candidate.get(
                        "stage"
                    ) or first_candidate.get("stage_name")
        result["api_reachable"] = True
    except Exception:
        result["api_reachable"] = False
        result["error"] = "Workable API diagnostic failed"
        logger.exception("Workable diagnostic failed")
    return result


__all__ = [
    "assert_workable_connected",
    "get_org_for_user",
    "release_for_workable_provider",
    "run_workable_diagnostic",
    "workable_client_snapshot",
]
