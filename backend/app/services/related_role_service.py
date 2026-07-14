"""Create a Taali related-role scoring view over a Workable candidate pool.

The persisted model and API routes retain their historical ``sister`` names,
but product surfaces use the clearer term "related role".  Keeping creation in
one service lets the HTTP dialog, role agent, and global chat share the same
validation, roster accounting, and worker-dispatch behaviour.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, ROLE_KIND_STANDARD, Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_ERROR,
    SISTER_EVAL_PENDING,
    SisterRoleEvaluation,
)
from ..tasks.sister_role_tasks import score_sister_role
from .sister_role_service import ensure_sister_evaluations

logger = logging.getLogger("taali.related_roles")

# Same holistic scoring path and planning estimate used by role-chat rescoring.
ESTIMATED_SCORE_COST_USD = 0.083


class RelatedRoleError(ValueError):
    """A user-correctable related-role validation error."""


def get_related_role_source(
    db: Session, *, role_id: int, organization_id: int
) -> Role:
    role = (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if role is None:
        raise RelatedRoleError("Role not found.")
    if str(role.role_kind or ROLE_KIND_STANDARD) == ROLE_KIND_SISTER:
        raise RelatedRoleError(
            "Create a related role from the original Workable role, not from another related role."
        )
    if not role.workable_job_id:
        raise RelatedRoleError(
            "Related roles currently require a Workable-linked original role."
        )
    return role


def related_role_roster_counts(db: Session, source: Role) -> dict[str, int]:
    filters = (
        CandidateApplication.organization_id == source.organization_id,
        CandidateApplication.role_id == source.id,
        CandidateApplication.deleted_at.is_(None),
    )
    total = int(
        db.query(func.count(CandidateApplication.id)).filter(*filters).scalar() or 0
    )
    with_cv = int(
        db.query(func.count(CandidateApplication.id))
        .outerjoin(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            *filters,
            or_(
                func.length(
                    func.trim(func.coalesce(CandidateApplication.cv_text, ""))
                )
                > 0,
                func.length(func.trim(func.coalesce(Candidate.cv_text, ""))) > 0,
            ),
        )
        .scalar()
        or 0
    )
    return {"total": total, "with_cv": with_cv, "missing_cv": total - with_cv}


def preview_related_role(
    db: Session, *, role_id: int, organization_id: int
) -> dict[str, Any]:
    source = get_related_role_source(
        db, role_id=role_id, organization_id=organization_id
    )
    counts = related_role_roster_counts(db, source)
    return {
        "type": "related_role_preview",
        "source_role_id": int(source.id),
        "source_role_name": source.name,
        "candidates_total": counts["total"],
        "candidates_with_cv": counts["with_cv"],
        "candidates_missing_cv": counts["missing_cv"],
        "estimated_cost_usd": round(
            counts["with_cv"] * ESTIMATED_SCORE_COST_USD, 2
        ),
        "message": (
            f"The related role will share {counts['total']} candidates with "
            f"{source.name}; {counts['with_cv']} can be scored now. Candidate "
            "stages and actions will continue to write back to the original Workable job."
        ),
    }


def _mark_dispatch_error(db: Session, *, role_id: int) -> None:
    db.query(SisterRoleEvaluation).filter(
        SisterRoleEvaluation.role_id == int(role_id),
        SisterRoleEvaluation.status == SISTER_EVAL_PENDING,
    ).update(
        {
            SisterRoleEvaluation.status: SISTER_EVAL_ERROR,
            SisterRoleEvaluation.error_message: (
                "Scoring worker unavailable; retry the roster"
            ),
        },
        synchronize_session=False,
    )
    db.commit()


def create_related_role(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
    name: str,
    job_spec_text: str,
) -> tuple[Role, dict[str, int]]:
    """Persist, commit, and queue a related scoring role.

    The commit intentionally precedes worker dispatch so scoring workers can
    read the new role and evaluation rows as soon as they receive the task.
    """
    source = get_related_role_source(
        db, role_id=role_id, organization_id=organization_id
    )
    clean_name = str(name or "").strip()
    clean_spec = str(job_spec_text or "").strip()
    if not clean_name:
        raise RelatedRoleError("Give the related role a name.")
    if len(clean_name) > 200:
        raise RelatedRoleError("The related-role name must be 200 characters or fewer.")
    if len(clean_spec) < 80:
        raise RelatedRoleError(
            "Paste the complete updated job specification (at least 80 characters)."
        )
    if len(clean_spec) > 100_000:
        raise RelatedRoleError("The job specification is too long.")

    related = Role(
        organization_id=int(organization_id),
        name=clean_name,
        description=f"Coupled scoring view of {source.name}",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=source.id,
        job_spec_text=clean_spec,
        job_spec_filename="Taali related role specification",
        auto_reject_threshold_mode="manual",
        agentic_mode_enabled=False,
        auto_reject=False,
        auto_reject_pre_screen=False,
        auto_promote=False,
        auto_skip_assessment=False,
    )
    db.add(related)
    try:
        db.flush()
        evaluation_counts = ensure_sister_evaluations(db, related)
        db.commit()
        db.refresh(related)
    except Exception:
        db.rollback()
        raise

    try:
        score_sister_role.apply_async(args=[related.id], queue="scoring")
    except Exception:  # pragma: no cover - persisted role remains retryable
        logger.exception(
            "Failed to dispatch initial related-role scoring role_id=%s", related.id
        )
        _mark_dispatch_error(db, role_id=int(related.id))
    return related, evaluation_counts


def related_role_created_payload(
    related: Role, evaluation_counts: dict[str, int]
) -> dict[str, Any]:
    return {
        "type": "related_role_created",
        "created": True,
        "role_id": int(related.id),
        "role_name": related.name,
        "source_role_id": int(related.ats_owner_role_id),
        "evaluation_counts": dict(evaluation_counts),
        "frontend_url": f"/jobs/{related.id}",
        "message": (
            f"Created {related.name} and queued its shared candidate roster for scoring. "
            "Candidate stages and actions remain coupled to the original Workable role."
        ),
    }


__all__ = [
    "RelatedRoleError",
    "create_related_role",
    "get_related_role_source",
    "preview_related_role",
    "related_role_created_payload",
    "related_role_roster_counts",
]
