"""Create a Taali related-role scoring view over an ATS candidate pool.

The persisted model and API routes retain their historical ``sister`` names,
but product surfaces use the clearer term "related role".  Keeping creation in
one service lets the HTTP dialog, role agent, and global chat share the same
validation, roster accounting, and worker-dispatch behaviour.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import logging
from typing import Any

from sqlalchemy.orm import Session

from ..models.organization import Organization
from ..models.job_hiring_team import (
    TEAM_ROLE_HIRING_MANAGER,
    JobHiringTeam,
)
from ..models.org_criterion import BUCKET_CONSTRAINT, BUCKET_MUST, BUCKET_PREFERRED
from ..models.role import ROLE_KIND_SISTER, ROLE_KIND_STANDARD, Role
from ..models.role_brief import RoleBrief
from ..tasks.sister_role_tasks import score_sister_role
from .agent_policy_settings import apply_workspace_agent_defaults
from .ats_role_lifecycle import ats_job_lifecycle
from .requisition_chat_capture import compute_completeness
from .related_role_paid_work_authorization import related_role_budget_preview
from .related_role_spec_hydration import (
    clone_related_role_brief_fields,
    hydrate_related_role_draft_from_saved_spec,
)
from .related_role_payloads import (
    related_role_created_payload,
    related_role_draft_payload,
)
from .role_brief_service import create_brief, materialize_brief_to_role
from .role_criteria_service import sync_derived_criteria
from .related_role_roster import active_source_applications_for_related_role
from .related_role_policy import disable_owner_auto_reject_for_new_family
from .sister_role_service import (
    application_cv_text,
    ensure_sister_evaluations,
    source_application_is_globally_closed,
)

logger = logging.getLogger("taali.related_roles")

class RelatedRoleError(ValueError):
    """A user-correctable related-role validation error."""


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
        # Refresh an identity-map hit too: preview callers may already have
        # loaded this row before a concurrent source edit committed.
        query = query.populate_existing().with_for_update(of=Role)
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
    applications = active_source_applications_for_related_role(db, source)
    excluded = sum(
        1 for application in applications
        if source_application_is_globally_closed(application)
    )
    scoreable = sum(
        1 for application in applications
        if not source_application_is_globally_closed(application)
        and bool(application_cv_text(application))
    )
    unscorable = len(applications) - excluded - scoreable
    return {
        "total": len(applications),
        "with_cv": scoreable,
        "missing_cv": unscorable,
        "scoreable": scoreable,
        "unscorable": unscorable,
        "excluded": excluded,
    }


def preview_related_role(
    db: Session, *, role_id: int, organization_id: int
) -> dict[str, Any]:
    source = get_related_role_source(
        db, role_id=role_id, organization_id=organization_id
    )
    counts = related_role_roster_counts(db, source)
    budget_preview = related_role_budget_preview(
        db.get(Organization, int(organization_id)),
        scoreable_count=counts["with_cv"],
    )
    source_ats_provider = ats_job_lifecycle(source).provider
    provider_label = "Bullhorn" if source_ats_provider == "bullhorn" else "Workable"
    return {
        "type": "related_role_preview",
        "source_role_id": int(source.id),
        "source_role_name": source.name,
        "source_role_version": int(source.version or 1),
        "source_ats_provider": source_ats_provider,
        "candidates_total": counts["total"],
        "candidates_with_cv": counts["with_cv"],
        "candidates_missing_cv": counts["missing_cv"],
        "candidates_scoreable": counts["scoreable"],
        "candidates_unscorable": counts["unscorable"],
        "candidates_excluded": counts["excluded"],
        **budget_preview,
        "message": (
            f"The related role will share {counts['total']} candidates with "
            f"{source.name} #{source.id}; {counts['with_cv']} can be scored now. It will have "
            "its own Taali funnel and scoring Agent with a proposed "
            f"${budget_preview['proposed_monthly_budget_cents'] / 100:.2f} monthly cap. Each future "
            f"scoreable ATS application costs about ${budget_preview['ongoing_score_cost_usd']:.3f} "
            "until that cap is reached. The ATS application remains "
            f"shared in {provider_label}, so rejection applies to every linked role."
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
    commit: bool = True,
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
    clean_spec_override = (
        str(job_spec_text).strip() if job_spec_text is not None else None
    )
    # Whitespace-only optional input means "clone the source", not "erase the
    # source specification".  Enforce the same payload ceiling as direct role
    # creation at the service boundary so REST and chat callers agree.
    if not clean_spec_override:
        clean_spec_override = None
    if clean_spec_override is not None and len(clean_spec_override) > 100_000:
        raise RelatedRoleError("The job specification is too long.")

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
        clone_related_role_brief_fields(brief, source_brief)

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
        clean_spec_override
        if clean_spec_override is not None
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
                f"I've copied **{source.name} #{source.id}** into a new related-role draft, "
                f"{copied_note}, and populated every structured field I could "
                "read from it. Tell me what should change for this version. "
                "You can describe only the differences; I'll save those into the "
                "brief and ask only about details the source does not answer. When "
                "you're ready, review the shared candidate count and use **Create "
                "and score candidates** to create the new scoring "
                f"role. Candidate stages and actions will stay coupled to "
                f"**{source.name} #{source.id}**, the original {provider_label} job."
            ),
            "attachments": [],
            "suggested_replies": [],
        }
    ]
    brief.completeness = compute_completeness(brief, template)
    db.flush()
    if commit:
        db.commit()
        db.refresh(brief)
    return brief


def create_related_role(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
    creator_user_id: int,
    name: str,
    job_spec_text: str,
    brief: RoleBrief | None = None,
    commit: bool = True,
    dispatch: bool = True,
    monthly_budget_cents: int | None = None,
    authorize_evaluation_counts: Callable[[Role, dict[str, int]], None] | None = None,
) -> tuple[Role, dict[str, int]]:
    """Persist and queue a related scoring role.

    HTTP callers retain the historical commit-before-dispatch behavior. Chat
    callers use ``commit=False`` so role creation, command completion, and the
    consumed-confirmation transcript commit atomically. Their initial task has
    a short countdown; the existing evaluation recovery sweep remains the
    durable fallback if the queue is unavailable or wins the commit race.

    The new role's hiring team is written before the same caller-owned commit,
    so no caller can expose an inaccessible role or dispatch work for one after
    a crash.
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
        description=f"Related Taali role based on {source.name} #{source.id}",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=source.id,
        job_spec_text=clean_spec,
        job_spec_filename="Taali related role specification",
        auto_reject_threshold_mode="manual",
        agentic_mode_enabled=True,
        auto_reject=False,
        auto_reject_pre_screen=False,
        auto_promote=False,
        auto_skip_assessment=True,
    )
    # A related role is an independent Taali workflow with its own scoring
    # authority and spend cap. It does not inherit the source role's Agent
    # switch or budget. Automatic rejection stays off because it closes the
    # shared ATS application across the whole role family.
    apply_workspace_agent_defaults(
        related,
        db.get(Organization, int(organization_id)),
        explicit_budget_cents=monthly_budget_cents,
    )
    related.agentic_mode_enabled = True
    related.auto_reject = False
    related.auto_reject_pre_screen = False
    related.auto_promote = False
    related.auto_send_assessment = False
    related.auto_resend_assessment = False
    related.auto_advance = False
    related.auto_skip_assessment = True
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
        if authorize_evaluation_counts is not None:
            authorize_evaluation_counts(related, evaluation_counts)
        # The paid-scope confirmation above must compare against the exact
        # source snapshot the recruiter approved. Disable unsafe owner-level
        # auto-reject only after that check, in this same transaction. Exclude
        # the just-created role so a first family member still triggers it.
        disable_owner_auto_reject_for_new_family(
            db,
            source=source,
            creator_user_id=int(creator_user_id),
            ignore_related_role_id=int(related.id),
        )
        if commit:
            db.commit()
            db.refresh(related)
    except Exception:
        db.rollback()
        raise

    if dispatch:
        try:
            task_kwargs: dict[str, Any] = {
                "args": [related.id],
                "queue": "scoring",
            }
            if not commit:
                task_kwargs["countdown"] = 2
            score_sister_role.apply_async(**task_kwargs)
        except Exception as exc:  # pragma: no cover - Beat owns durable recovery
            logger.error(
                "Initial related-role kick unavailable role_id=%s error_code=queue_unavailable error_type=%s",
                related.id,
                type(exc).__name__,
            )
    return related, evaluation_counts

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
