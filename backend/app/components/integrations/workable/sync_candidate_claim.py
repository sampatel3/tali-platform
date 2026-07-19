"""Read and revalidation phases for one Workable candidate sync."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ....domains.assessments_runtime.role_support import is_resolved
from ....models.candidate import Candidate
from ....models.candidate_application import CandidateApplication
from ....models.organization import Organization
from ....models.role import Role
from ....models.workable_sync_run import WorkableSyncRun
from .sync_provider_boundaries import (
    CandidateProviderClaim,
    WorkableProviderLineageDrift,
    finish_db_phase,
    workable_org_auth_fingerprint,
)


class CandidateClaimDrift(RuntimeError):
    """The DB identity used for a detached provider read is no longer current."""


def filter_payloads_missing_cv(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    payloads_by_id: dict[str, dict],
) -> dict[str, dict]:
    """Read CV presence in one roundtrip and release before resume downloads."""

    candidate_ids = [candidate_id for candidate_id in payloads_by_id if candidate_id]
    if not candidate_ids:
        return {}
    already_have_cv = {
        row[0]
        for row in db.query(CandidateApplication.workable_candidate_id)
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.workable_candidate_id.in_(candidate_ids),
            (CandidateApplication.cv_file_url.isnot(None))
            | (CandidateApplication.cv_text.isnot(None)),
        )
        .all()
        if row[0]
    }
    result = {
        candidate_id: payload
        for candidate_id, payload in payloads_by_id.items()
        if candidate_id not in already_have_cv
    }
    finish_db_phase(db)
    return result


def _cv_fingerprint(row: Candidate | CandidateApplication | None) -> str:
    if row is None:
        return hashlib.sha256(b"missing").hexdigest()
    parts = (
        str(row.cv_file_url or ""),
        str(row.cv_filename or ""),
        str(row.cv_text or ""),
        str(row.cv_uploaded_at.isoformat() if row.cv_uploaded_at else ""),
    )
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _candidate_identity_fingerprint(candidate: Candidate | None) -> str:
    if candidate is None:
        return hashlib.sha256(b"missing").hexdigest()
    parts = (
        str(candidate.organization_id),
        str(candidate.workable_candidate_id or ""),
        str(candidate.email or ""),
        str(candidate.phone_normalized or ""),
        str(candidate.deleted_at.isoformat() if candidate.deleted_at else ""),
    )
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _application_state_fingerprint(application: CandidateApplication | None) -> str:
    if application is None:
        return hashlib.sha256(b"missing").hexdigest()
    payload = {
        "organization_id": application.organization_id,
        "role_id": application.role_id,
        "candidate_id": application.candidate_id,
        "workable_candidate_id": application.workable_candidate_id,
        "version": int(application.version or 1),
        "deleted_at": application.deleted_at,
        "pipeline_stage": application.pipeline_stage,
        "application_outcome": application.application_outcome,
        "workable_stage": application.workable_stage,
        "workable_stage_local_write_at": application.workable_stage_local_write_at,
        "integration_sync_state": application.integration_sync_state,
    }
    encoded = json.dumps(payload, default=str, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _has_cv(row: Candidate | CandidateApplication | None) -> bool:
    return bool(
        row is not None
        and (
            str(row.cv_file_url or "").strip()
            or str(row.cv_text or "").strip()
        )
    )


def _activities_due(
    application: CandidateApplication | None,
    *,
    now: datetime,
    interval: timedelta,
) -> bool:
    if application is None or not is_resolved(application):
        return True
    state = (
        application.integration_sync_state
        if isinstance(application.integration_sync_state, dict)
        else {}
    )
    last_fetch = state.get("last_activities_fetch_at")
    if not last_fetch:
        return True
    try:
        parsed = datetime.fromisoformat(str(last_fetch))
    except (TypeError, ValueError):
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return now - parsed >= interval


def build_candidate_claim(
    db: Session,
    *,
    organization_id: int,
    run_id: int | None,
    role_id: int,
    candidate_external_id: str,
    email: str | None,
    phone_normalized: str | None,
    mode: str,
    terminal: bool,
    now: datetime,
    resolved_activities_interval: timedelta,
) -> CandidateProviderClaim:
    """Capture the exact primitive identity needed before provider I/O."""

    organization = db.get(Organization, organization_id)
    if organization is None:
        raise CandidateClaimDrift("Workable candidate organization disappeared")
    run_status = None
    if run_id is not None:
        run_row = (
            db.query(WorkableSyncRun.status)
            .filter(
                WorkableSyncRun.id == run_id,
                WorkableSyncRun.organization_id == organization_id,
            )
            .first()
        )
        if run_row is None:
            raise CandidateClaimDrift("Workable candidate run disappeared")
        run_status = str(run_row[0] or "")
    role = (
        db.query(Role)
        .filter(Role.id == role_id, Role.organization_id == organization_id)
        .first()
    )
    if role is None or not role.workable_job_id:
        raise WorkableProviderLineageDrift("Workable candidate role is no longer linked")

    application = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplication.workable_candidate_id == candidate_external_id,
        )
        .first()
    )
    candidate = None
    if application is not None:
        candidate = db.get(Candidate, int(application.candidate_id))
    if candidate is None:
        candidate = (
            db.query(Candidate)
            .filter(
                Candidate.organization_id == organization_id,
                Candidate.workable_candidate_id == candidate_external_id,
            )
            .first()
        )
    if candidate is None and email:
        candidate = (
            db.query(Candidate)
            .filter(
                Candidate.organization_id == organization_id,
                Candidate.email == email,
            )
            .first()
        )
    if candidate is None and phone_normalized:
        candidate = (
            db.query(Candidate)
            .filter(
                Candidate.organization_id == organization_id,
                Candidate.phone_normalized == phone_normalized,
            )
            .first()
        )
    if application is None and candidate is not None:
        application = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.organization_id == organization_id,
                CandidateApplication.role_id == role_id,
                CandidateApplication.candidate_id == candidate.id,
            )
            .first()
        )

    resolved = bool(application is not None and is_resolved(application))
    application_cv_fingerprint = _cv_fingerprint(application)
    candidate_cv_fingerprint = _cv_fingerprint(candidate)
    has_cv = _has_cv(application) or _has_cv(candidate)
    activities_due = bool(
        mode == "full"
        and not terminal
        and _activities_due(
            application,
            now=now,
            interval=resolved_activities_interval,
        )
    )
    return CandidateProviderClaim(
        organization_id=organization_id,
        organization_auth_fingerprint=workable_org_auth_fingerprint(organization),
        run_id=run_id,
        run_status=run_status,
        role_id=role_id,
        role_version=int(role.version or 1),
        workable_job_id=str(role.workable_job_id),
        candidate_external_id=candidate_external_id,
        application_id=(int(application.id) if application is not None else None),
        application_version=(int(application.version or 1) if application is not None else None),
        candidate_id=(int(candidate.id) if candidate is not None else None),
        candidate_workable_id=(candidate.workable_candidate_id if candidate is not None else None),
        candidate_email=email,
        candidate_phone_normalized=phone_normalized,
        candidate_identity_fingerprint=_candidate_identity_fingerprint(candidate),
        application_state_fingerprint=_application_state_fingerprint(application),
        application_cv_fingerprint=application_cv_fingerprint,
        candidate_cv_fingerprint=candidate_cv_fingerprint,
        resolved=resolved,
        activities_due=activities_due,
        needs_resume=bool(mode == "full" and not terminal and not resolved and not has_cv),
    )


def revalidate_candidate_claim(
    db: Session,
    claim: CandidateProviderClaim,
) -> tuple[
    Organization,
    WorkableSyncRun | None,
    Role,
    CandidateApplication | None,
    Candidate | None,
]:
    """Lock and compare every identity before applying detached results."""

    organization = (
        db.query(Organization)
        .filter(Organization.id == claim.organization_id)
        .with_for_update(of=Organization)
        .first()
    )
    if organization is None:
        raise CandidateClaimDrift("Workable sync organization disappeared")
    if workable_org_auth_fingerprint(organization) != claim.organization_auth_fingerprint:
        raise WorkableProviderLineageDrift(
            "Workable organization changed during provider read"
        )
    run = None
    if claim.run_id is not None:
        run = (
            db.query(WorkableSyncRun)
            .filter(
                WorkableSyncRun.id == claim.run_id,
                WorkableSyncRun.organization_id == claim.organization_id,
            )
            .with_for_update(of=WorkableSyncRun)
            .first()
        )
        if run is None or str(run.status or "") != claim.run_status:
            raise CandidateClaimDrift("Workable sync run changed during provider read")
    role = (
        db.query(Role)
        .filter(
            Role.id == claim.role_id,
            Role.organization_id == claim.organization_id,
            Role.workable_job_id == claim.workable_job_id,
        )
        .with_for_update(of=Role)
        .first()
    )
    if role is None or int(role.version or 1) != claim.role_version:
        raise WorkableProviderLineageDrift(
            "Workable sync role changed during provider read"
        )

    application = None
    if claim.application_id is not None:
        application = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == claim.application_id,
                CandidateApplication.organization_id == claim.organization_id,
                CandidateApplication.role_id == claim.role_id,
            )
            .with_for_update(of=CandidateApplication)
            .first()
        )
        if (
            application is None
            or int(application.version or 1) != claim.application_version
            or int(application.candidate_id) != claim.candidate_id
            or is_resolved(application) != claim.resolved
            or _application_state_fingerprint(application)
            != claim.application_state_fingerprint
            or _cv_fingerprint(application) != claim.application_cv_fingerprint
        ):
            raise CandidateClaimDrift("Workable application changed during provider read")
    else:
        appeared = (
            db.query(CandidateApplication.id)
            .filter(
                CandidateApplication.organization_id == claim.organization_id,
                CandidateApplication.role_id == claim.role_id,
                CandidateApplication.workable_candidate_id == claim.candidate_external_id,
            )
            .first()
        )
        if appeared is not None:
            raise CandidateClaimDrift("Workable application appeared during provider read")

    candidate = None
    if claim.candidate_id is not None:
        candidate = (
            db.query(Candidate)
            .filter(
                Candidate.id == claim.candidate_id,
                Candidate.organization_id == claim.organization_id,
            )
            .with_for_update(of=Candidate)
            .first()
        )
        if (
            candidate is None
            or candidate.workable_candidate_id != claim.candidate_workable_id
            or _candidate_identity_fingerprint(candidate)
            != claim.candidate_identity_fingerprint
            or _cv_fingerprint(candidate) != claim.candidate_cv_fingerprint
        ):
            raise CandidateClaimDrift("Workable candidate changed during provider read")
        if claim.application_id is None:
            appeared = (
                db.query(CandidateApplication.id)
                .filter(
                    CandidateApplication.organization_id == claim.organization_id,
                    CandidateApplication.role_id == claim.role_id,
                    CandidateApplication.candidate_id == claim.candidate_id,
                )
                .first()
            )
            if appeared is not None:
                raise CandidateClaimDrift("Workable application appeared during provider read")
    else:
        identity_filters = [
            Candidate.workable_candidate_id == claim.candidate_external_id,
        ]
        if claim.candidate_email:
            identity_filters.append(Candidate.email == claim.candidate_email)
        if claim.candidate_phone_normalized:
            identity_filters.append(
                Candidate.phone_normalized == claim.candidate_phone_normalized
            )
        from sqlalchemy import or_

        appeared = (
            db.query(Candidate.id)
            .filter(
                Candidate.organization_id == claim.organization_id,
                or_(*identity_filters),
            )
            .first()
        )
        if appeared is not None:
            raise CandidateClaimDrift("Workable candidate appeared during provider read")
    return organization, run, role, application, candidate


__all__ = [
    "CandidateClaimDrift",
    "build_candidate_claim",
    "filter_payloads_missing_cv",
    "revalidate_candidate_claim",
]
