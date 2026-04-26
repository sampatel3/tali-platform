"""Keep ``RoleCriterion`` rows in sync with the recruiter-supplied requirement
text and the Requirements section of an uploaded job spec.

These helpers are the single write path for criteria today. Until the
frontend exposes per-criterion editing, ``Role.additional_requirements`` and
``Role.job_spec_text`` remain the authoring surface — every save reparses
those fields and upserts the matching ``recruiter`` and ``derived_from_spec``
criteria so downstream scoring (cv_match_v4) can key off stable IDs.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.role import Role
from ..models.role_criterion import (
    CRITERION_SOURCE_DERIVED,
    CRITERION_SOURCE_RECRUITER,
    RoleCriterion,
)
from .fit_matching_service import _extract_recruiter_requirements
from .spec_normalizer import derive_criteria_texts, normalize_spec


def _replace_criteria(
    db: Session,
    role: Role,
    *,
    source: str,
    texts: list[str],
) -> None:
    existing = [c for c in (role.criteria or []) if c.source == source]
    for criterion in existing:
        db.delete(criterion)
    for ordering, text in enumerate(texts):
        db.add(
            RoleCriterion(
                role_id=role.id,
                source=source,
                ordering=ordering,
                weight=1.0,
                must_have=False,
                text=text,
            )
        )


def sync_recruiter_criteria(db: Session, role: Role) -> None:
    """Re-derive ``recruiter``-source criteria from ``role.additional_requirements``."""
    texts = _extract_recruiter_requirements(role.additional_requirements)
    _replace_criteria(db, role, source=CRITERION_SOURCE_RECRUITER, texts=texts)


def sync_derived_criteria(db: Session, role: Role) -> None:
    """Re-derive ``derived_from_spec`` criteria from the Requirements section.

    Falls back to no derived criteria when the spec has no recognizable
    Requirements heading — that signals the recruiter to add explicit
    must-haves rather than let the system anchor on boilerplate.
    """
    spec = normalize_spec(role.job_spec_text)
    texts = derive_criteria_texts(spec.requirements)
    _replace_criteria(db, role, source=CRITERION_SOURCE_DERIVED, texts=texts)


def sync_all_criteria(db: Session, role: Role) -> None:
    sync_recruiter_criteria(db, role)
    sync_derived_criteria(db, role)
