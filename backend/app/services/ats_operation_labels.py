"""Provider-aware labels for durable ATS operation audit messages."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..components.integrations.resolver import (
    resolve_application_ats_provider,
    resolve_ats_provider,
)
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization


def active_ats_label(
    db: Session, organization_id: int, payload: dict | None = None
) -> tuple[str, str]:
    """Return ``(slug, label)`` for provider-aware audit/error wording."""

    explicit_provider = str((payload or {}).get("provider") or "").strip().lower()
    if explicit_provider == "bullhorn":
        return "bullhorn", "Bullhorn"
    if explicit_provider == "workable":
        return "workable", "Workable"
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    provider = None
    application_id = (payload or {}).get("application_id")
    if application_id is not None:
        application = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == int(application_id),
                CandidateApplication.organization_id == int(organization_id),
            )
            .first()
        )
        provider = resolve_application_ats_provider(org, db, application)
        if (
            provider is None
            and application is not None
            and application.bullhorn_job_submission_id
        ):
            return "bullhorn", "Bullhorn"
    if provider is None:
        provider = resolve_ats_provider(org, db)
    slug = str(getattr(provider, "ats", "") or "").lower()
    if slug == "bullhorn":
        return "bullhorn", "Bullhorn"
    if slug == "workable":
        return "workable", "Workable"
    # This runner predates provider routing and disconnected/local fixtures can
    # still inject its legacy Workable errors. Bullhorn is always explicit via
    # the resolver; preserve Workable wording for the fallback contract.
    return "workable", "Workable"


__all__ = ["active_ats_label"]
