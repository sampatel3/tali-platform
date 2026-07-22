"""Create independent related roles through every product surface."""

from __future__ import annotations

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
from ..models.role import (
    JOB_STATUS_OPEN,
    ROLE_KIND_SISTER,
    Role,
)
from ..models.role_brief import RoleBrief
from ..tasks.sister_role_tasks import score_sister_role
from .agent_policy_settings import apply_workspace_agent_defaults
from .ats_role_lifecycle import ats_job_lifecycle
from .requisition_chat_capture import compute_completeness
from .related_role_payloads import related_role_created_payload
from .related_role_preview import (
    RelatedRoleError,
    get_related_role_source,
    preview_related_role,
    related_role_roster_counts,
)
from .related_role_spec_hydration import hydrate_related_role_draft_from_saved_spec
from .role_brief_service import create_brief, materialize_brief_to_role
from .role_criteria_service import sync_derived_criteria
from .sister_role_service import (
    ensure_sister_evaluations,
    related_role_ats_owner,
    related_role_source_fingerprint,
    select_related_role_source_members,
)

logger = logging.getLogger("taali.related_roles")

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
    ``source_role_id`` remains the durable one-time source selection used when
    the draft is finally converted. It is not an ATS or fan-out authority.
    """
    source = get_related_role_source(
        db,
        role_id=role_id,
        organization_id=organization_id,
        lock_for_update=True,
    )
    source_counts = related_role_roster_counts(db, source)
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
            "candidate_count": int(source_counts["total"]),
            "fingerprint": source_counts["snapshot_fingerprint"],
        }
    }
    if cloned_spec:
        state["jd_override"] = cloned_spec
        state["canonical_spec_mode"] = "verbatim"
        state["job_spec_revision"] = 1
        state["job_spec_last_change_mode"] = "clone"
    brief.agent_state = state

    provider = ats_job_lifecycle(related_role_ats_owner(db, source)).provider
    provider_label = (
        "Bullhorn"
        if provider == "bullhorn"
        else "Workable"
        if provider == "workable"
        else "ATS"
    )
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
                "you're ready, review the initial candidate snapshot and use **Create "
                "and score candidates** to create the new scoring "
                "role. The new role will then own its membership, stages, "
                f"decisions, and Agent. The original {provider_label} job "
                f"(**{source.name} #{source.id}**) is only an optional write-back link."
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
    expected_source_snapshot_fingerprint: str | None = None,
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
    source_members = select_related_role_source_members(db, source)
    source_snapshot_fingerprint = related_role_source_fingerprint(source_members)
    if (
        expected_source_snapshot_fingerprint
        and expected_source_snapshot_fingerprint != source_snapshot_fingerprint
    ):
        raise RelatedRoleError(
            "The source candidate snapshot changed. Refresh the preview and confirm again."
        )
    ats_owner = related_role_ats_owner(db, source)
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
        related_source_role_id=int(source.id),
        ats_owner_role_id=int(ats_owner.id) if ats_owner is not None else None,
        job_status=JOB_STATUS_OPEN,
        job_spec_text=clean_spec,
        job_spec_filename="Taali related role specification",
        auto_reject_threshold_mode="manual",
        agentic_mode_enabled=True,
        auto_reject=False,
        auto_reject_pre_screen=False,
        auto_promote=False,
        auto_skip_assessment=True,
    )
    # A related role is an independent workflow with its own membership,
    # scoring authority, funnel, decisions, settings, and spend cap. The source
    # role is not modified merely because this role was created.
    apply_workspace_agent_defaults(
        related, db.get(Organization, int(organization_id))
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
            brief_state = dict(brief.agent_state or {})
            brief_state["related_role_source_snapshot"] = {
                "role_id": int(source.id),
                "role_name": source.name,
                "role_version": int(source.version or 1),
                "candidate_count": len(source_members),
                "fingerprint": source_snapshot_fingerprint,
            }
            brief.agent_state = brief_state
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
        source_team_members = (
            db.query(JobHiringTeam)
            .filter(
                JobHiringTeam.organization_id == int(organization_id),
                JobHiringTeam.role_id == int(source.id),
            )
            .order_by(JobHiringTeam.id.asc())
            .all()
        )
        if source_team_members:
            db.add_all(
                [
                    JobHiringTeam(
                        organization_id=int(organization_id),
                        role_id=int(related.id),
                        user_id=int(member.user_id),
                        team_role=member.team_role,
                    )
                    for member in source_team_members
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
        evaluation_counts = ensure_sister_evaluations(
            db,
            related,
            seed_missing=True,
            source_role=source,
            source_members=source_members,
        )
        db.commit()
        db.refresh(related)
        if ats_owner is not None:
            # The source Role instance can have a partially populated inverse
            # collection after the new relationship is flushed. Creation
            # receipts promise a complete family, so force the next read to
            # reload every current sibling from the database.
            db.expire(ats_owner, ["sister_roles"])
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
