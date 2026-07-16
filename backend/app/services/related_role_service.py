"""Create a Taali related-role scoring view over an ATS candidate pool.

The persisted model and API routes retain their historical ``sister`` names,
but product surfaces use the clearer term "related role".  Keeping creation in
one service lets the HTTP dialog, role agent, and global chat share the same
validation, roster accounting, and worker-dispatch behaviour.
"""

from __future__ import annotations

from copy import deepcopy
import logging
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.job_hiring_team import (
    TEAM_ROLE_HIRING_MANAGER,
    JobHiringTeam,
)
from ..models.org_criterion import BUCKET_CONSTRAINT, BUCKET_MUST, BUCKET_PREFERRED
from ..models.role import ROLE_KIND_SISTER, ROLE_KIND_STANDARD, Role
from ..models.role_brief import RoleBrief
from ..tasks.sister_role_tasks import score_sister_role
from .ats_role_lifecycle import ats_job_lifecycle
from .requisition_chat_capture import compute_completeness
from .related_role_spec_hydration import hydrate_related_role_draft_from_saved_spec
from .role_brief_service import create_brief, materialize_brief_to_role
from .role_criteria_service import sync_derived_criteria
from .sister_role_service import ensure_sister_evaluations

logger = logging.getLogger("taali.related_roles")

# Same holistic scoring path and planning estimate used by role-chat rescoring.
ESTIMATED_SCORE_COST_USD = 0.083


class RelatedRoleError(ValueError):
    """A user-correctable related-role validation error."""


_BRIEF_CLONE_FIELDS = (
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
)


def get_related_role_source(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
    lock_for_update: bool = False,
) -> Role:
    query = db.query(Role).filter(
        Role.id == int(role_id),
        Role.organization_id == int(organization_id),
        Role.deleted_at.is_(None),
    )
    if lock_for_update:
        # Hiring-team mutations acquire the same Role lock first. This keeps
        # the copied membership set stable until the related role commits.
        query = query.with_for_update(of=Role)
    role = query.first()
    if role is None:
        raise RelatedRoleError("Role not found.")
    if str(role.role_kind or ROLE_KIND_STANDARD) == ROLE_KIND_SISTER:
        raise RelatedRoleError(
            "Create a related role from the original ATS role, not from another related role."
        )
    if not ats_job_lifecycle(role).external_job_id:
        raise RelatedRoleError(
            "Related roles require a Workable- or Bullhorn-linked original role."
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
    source_ats_provider = ats_job_lifecycle(source).provider
    provider_label = "Bullhorn" if source_ats_provider == "bullhorn" else "Workable"
    return {
        "type": "related_role_preview",
        "source_role_id": int(source.id),
        "source_role_name": source.name,
        "source_ats_provider": source_ats_provider,
        "candidates_total": counts["total"],
        "candidates_with_cv": counts["with_cv"],
        "candidates_missing_cv": counts["missing_cv"],
        "estimated_cost_usd": round(
            counts["with_cv"] * ESTIMATED_SCORE_COST_USD, 2
        ),
        "message": (
            f"The related role will share {counts['total']} candidates with "
            f"{source.name}; {counts['with_cv']} can be scored now. Candidate "
            f"stages and actions will continue to write back to the original {provider_label} job."
        ),
    }


def create_related_role_draft(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
    creator_user_id: int,
    template: dict[str, Any],
    name: str | None = None,
    job_spec_text: str | None = None,
) -> RoleBrief:
    """Clone an ATS role into the existing conversational job-draft model.

    The clone is a snapshot: subsequent chat/edit turns change only this draft.
    ``source_role_id`` remains the durable coupling pointer used when the draft
    is finally converted into a related scoring role.
    """
    source = get_related_role_source(
        db,
        role_id=role_id,
        organization_id=organization_id,
        lock_for_update=True,
    )
    clean_name = str(name or "").strip()
    if len(clean_name) > 200:
        raise RelatedRoleError("The related-role name must be 200 characters or fewer.")

    source_brief = (
        db.query(RoleBrief)
        .filter(
            RoleBrief.organization_id == int(organization_id),
            RoleBrief.role_id == int(source.id),
        )
        .order_by(RoleBrief.id.desc())
        .first()
    )
    brief = create_brief(
        db,
        organization_id=int(organization_id),
        created_by_user_id=int(creator_user_id),
        source_kind="conversational",
    )
    brief.source_role_id = int(source.id)

    if source_brief is not None:
        for field in _BRIEF_CLONE_FIELDS:
            setattr(brief, field, deepcopy(getattr(source_brief, field, None)))

    brief.title = clean_name or f"{source.name} · Related"
    brief.summary = brief.summary or source.description
    for field in (
        "department",
        "location_city",
        "location_country",
        "workplace_type",
        "employment_type",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
    ):
        if getattr(brief, field, None) in (None, ""):
            setattr(brief, field, deepcopy(getattr(source, field, None)))

    criteria_by_bucket: dict[str, list[str]] = {
        BUCKET_MUST: [],
        BUCKET_PREFERRED: [],
        BUCKET_CONSTRAINT: [],
    }
    for criterion in source.criteria:
        if criterion.deleted_at is not None or not str(criterion.text or "").strip():
            continue
        criteria_by_bucket.setdefault(criterion.bucket, []).append(
            str(criterion.text).strip()
        )
    brief.must_haves = brief.must_haves or criteria_by_bucket[BUCKET_MUST]
    brief.preferred = brief.preferred or criteria_by_bucket[BUCKET_PREFERRED]
    brief.dealbreakers = brief.dealbreakers or criteria_by_bucket[BUCKET_CONSTRAINT]

    source_override = (
        (source_brief.agent_state or {}).get("jd_override")
        if source_brief is not None
        else None
    )
    cloned_spec = str(
        job_spec_text
        if job_spec_text is not None
        else (source.job_spec_text or source_override or "")
    ).strip()
    if cloned_spec:
        # The verbatim JD is source material for later intake turns, not merely
        # a rendered/publishing override.  Keep it on the cloned brief so future
        # chat hydration can revisit the document without relying on attachment
        # bytes or the source role still being unchanged.
        hydrate_related_role_draft_from_saved_spec(brief, cloned_spec)
    state: dict[str, Any] = {
        "related_role_source_snapshot": {
            "role_id": int(source.id),
            "role_name": source.name,
            "role_version": int(source.version or 1),
        }
    }
    if cloned_spec:
        state["jd_override"] = cloned_spec
        state["canonical_spec_mode"] = "verbatim"
        state["job_spec_revision"] = 1
        state["job_spec_last_change_mode"] = "clone"
    brief.agent_state = state

    provider = ats_job_lifecycle(source).provider
    provider_label = "Bullhorn" if provider == "bullhorn" else "Workable"
    copied_note = (
        "including its complete job specification"
        if cloned_spec
        else "using the role details currently available"
    )
    brief.messages = [
        {
            "role": "assistant",
            "content": (
                f"I've copied **{source.name}** into a new related-role draft, "
                f"{copied_note}, and populated every structured field I could "
                "read from it. Tell me what should change for this version. "
                "You can describe only the differences; I'll save those into the "
                "brief and ask only about details the source does not answer. When "
                "you're ready, review the shared candidate count and use **Create "
                "and score candidates** to create the new scoring "
                f"role. Candidate stages and actions will stay coupled to the original {provider_label} job."
            ),
            "attachments": [],
            "suggested_replies": [],
        }
    ]
    brief.completeness = compute_completeness(brief, template)
    db.commit()
    db.refresh(brief)
    return brief


def related_role_draft_payload(brief: RoleBrief) -> dict[str, Any]:
    source = brief.source_role
    return {
        "type": "related_role_draft",
        "created": True,
        "brief_id": int(brief.id),
        "source_role_id": int(brief.source_role_id),
        "source_role_name": source.name if source is not None else None,
        "proposed_name": brief.title,
        "completeness": int(brief.completeness or 0),
        "frontend_url": f"/requisitions?brief={brief.id}",
        "message": (
            "Created a pre-populated related-role draft in the job-creation chat. "
            "Review the cloned specification, describe any differences, then confirm creation and scoring there."
        ),
    }


def create_related_role(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
    creator_user_id: int,
    name: str,
    job_spec_text: str,
    brief: RoleBrief | None = None,
) -> tuple[Role, dict[str, int]]:
    """Persist, assign a team, commit, and queue a related scoring role.

    The commit intentionally precedes worker dispatch so scoring workers can
    read the new role and evaluation rows as soon as they receive the task.
    The new role's hiring team is written before that same commit so no caller
    can expose an inaccessible role (or dispatch work for one) after a crash.
    """
    source = get_related_role_source(
        db,
        role_id=role_id,
        organization_id=organization_id,
        lock_for_update=True,
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
        if brief is not None:
            if int(brief.organization_id) != int(organization_id):
                raise RelatedRoleError("The related-role draft belongs to another organization.")
            if int(brief.source_role_id or 0) != int(source.id):
                raise RelatedRoleError("The related-role draft is linked to a different source role.")
            if brief.role_id is not None:
                raise RelatedRoleError("This related-role draft has already been created.")
            brief.role_id = int(related.id)
            # Reuse the normal requisition materializer so structured fields,
            # recruiter criteria, and the rich brief remain identical whether
            # a job started natively or as a related scoring view.
            materialize_brief_to_role(
                db,
                brief,
                mark_applied=True,
                job_spec_text=clean_spec,
            )
        sync_derived_criteria(db, related)
        source_members = (
            db.query(JobHiringTeam)
            .filter(
                JobHiringTeam.organization_id == int(organization_id),
                JobHiringTeam.role_id == int(source.id),
            )
            .order_by(JobHiringTeam.id.asc())
            .all()
        )
        if source_members:
            db.add_all(
                [
                    JobHiringTeam(
                        organization_id=int(organization_id),
                        role_id=int(related.id),
                        user_id=int(member.user_id),
                        team_role=member.team_role,
                    )
                    for member in source_members
                ]
            )
        else:
            db.add(
                JobHiringTeam(
                    organization_id=int(organization_id),
                    role_id=int(related.id),
                    user_id=int(creator_user_id),
                    team_role=TEAM_ROLE_HIRING_MANAGER,
                )
            )
        evaluation_counts = ensure_sister_evaluations(db, related)
        db.commit()
        db.refresh(related)
    except Exception:
        db.rollback()
        raise

    try:
        score_sister_role.apply_async(args=[related.id], queue="scoring")
    except Exception as exc:  # pragma: no cover - Beat owns durable recovery
        logger.error(
            "Initial related-role kick unavailable role_id=%s error_code=queue_unavailable error_type=%s",
            related.id,
            type(exc).__name__,
        )
    return related, evaluation_counts


def related_role_created_payload(
    related: Role, evaluation_counts: dict[str, int]
) -> dict[str, Any]:
    owner = getattr(related, "ats_owner_role", None)
    source_ats_provider = ats_job_lifecycle(owner).provider
    provider_label = "Bullhorn" if source_ats_provider == "bullhorn" else "Workable"
    return {
        "type": "related_role_created",
        "created": True,
        "role_id": int(related.id),
        "role_name": related.name,
        "source_role_id": int(related.ats_owner_role_id),
        "source_ats_provider": source_ats_provider,
        "evaluation_counts": dict(evaluation_counts),
        "frontend_url": f"/jobs/{related.id}",
        "message": (
            f"Created {related.name} and queued its shared candidate roster for scoring. "
            f"Candidate stages and actions remain coupled to the original {provider_label} role."
        ),
    }


__all__ = [
    "RelatedRoleError",
    "create_related_role",
    "create_related_role_draft",
    "get_related_role_source",
    "preview_related_role",
    "related_role_created_payload",
    "related_role_draft_payload",
    "related_role_roster_counts",
]
