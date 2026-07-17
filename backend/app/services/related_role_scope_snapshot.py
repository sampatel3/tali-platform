"""Canonical paid-work cohort snapshots for related roles."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.role import ROLE_KIND_STANDARD
from .related_role_roster import active_source_applications_for_related_role
from .sister_role_service import (
    application_cv_text,
    source_application_is_globally_closed,
    text_fingerprint,
)


def related_role_scope_counts(db: Session, role: Role) -> dict[str, int]:
    """Return the live cohort counts without hydrating or hashing CV bodies.

    The progress endpoint calls this every few seconds while scoring is active.
    Keep the exact roster predicates and CV fallback semantics used by
    :func:`related_role_scope_snapshot`, but let the database return four
    aggregate integers instead of transferring every application and full CV
    into the API process.  Exact identity/content fingerprints remain a
    deliberate, one-shot recovery-authority operation.
    """

    owner_role_id = int(role.ats_owner_role_id or role.id)
    has_cv_text = or_(
        func.length(func.trim(func.coalesce(CandidateApplication.cv_text, ""))) > 0,
        func.length(func.trim(func.coalesce(Candidate.cv_text, ""))) > 0,
    )
    is_excluded = or_(
        func.coalesce(CandidateApplication.application_outcome, "open") != "open",
        CandidateApplication.workable_disqualified.is_(True),
    )
    row = (
        db.query(
            func.count(CandidateApplication.id),
            func.coalesce(
                func.sum(case((is_excluded, 1), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(
                    case((and_(~is_excluded, has_cv_text), 1), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case((and_(~is_excluded, ~has_cv_text), 1), else_=0)
                ),
                0,
            ),
        )
        .select_from(CandidateApplication)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.organization_id == role.organization_id,
            CandidateApplication.role_id == owner_role_id,
            CandidateApplication.deleted_at.is_(None),
            Candidate.organization_id == role.organization_id,
            Candidate.deleted_at.is_(None),
            Role.organization_id == role.organization_id,
            Role.role_kind == ROLE_KIND_STANDARD,
            Role.ats_owner_role_id.is_(None),
            Role.deleted_at.is_(None),
        )
        .one()
    )
    total, excluded, scoreable, unscorable = (int(value or 0) for value in row)
    return {
        "total": total,
        "scoreable": scoreable,
        "unscorable": unscorable,
        "excluded": excluded,
    }


def related_role_scope_snapshot(db: Session, role: Role) -> dict[str, Any]:
    """Return counts plus a stable identity/CV/disposition fingerprint."""

    applications = active_source_applications_for_related_role(db, role)
    entries = []
    scoreable = 0
    unscorable = 0
    excluded = 0
    for application in applications:
        cv_text = application_cv_text(application)
        if source_application_is_globally_closed(application):
            disposition = "excluded"
            excluded += 1
        elif cv_text:
            disposition = "scoreable"
            scoreable += 1
        else:
            disposition = "unscorable"
            unscorable += 1
        entries.append(
            {
                "application_id": int(application.id),
                "cv_fingerprint": text_fingerprint(cv_text) if cv_text else None,
                "disposition": disposition,
            }
        )
    encoded = json.dumps(
        sorted(entries, key=lambda item: item["application_id"]),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "total": len(applications),
        "scoreable": scoreable,
        "unscorable": unscorable,
        "excluded": excluded,
        "cohort_fingerprint": hashlib.sha256(encoded).hexdigest(),
    }


__all__ = ["related_role_scope_counts", "related_role_scope_snapshot"]
