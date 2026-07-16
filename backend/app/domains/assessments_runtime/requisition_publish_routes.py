"""Requisition PUBLISH surface — snapshot a brief into a shareable PUBLIC job page.

Split out of ``requisition_routes`` and re-composed there via
``router.include_router``; the paths/prefix are unchanged.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.role import JOB_STATUS_DRAFT, Role
from ...models.job_hiring_team import (
    TEAM_ROLE_HIRING_MANAGER,
    JobHiringTeam,
)
from ...models.role_brief import BRIEF_STATUS_APPLIED, RoleBrief
from ...models.user import User
from ...platform.database import get_db
from ...platform.request_context import get_request_id
from ...services.cv_score_orchestrator import mark_role_scores_stale
from ...services.requisition_chat_capture import compute_completeness, compute_gaps
from ...services.requisition_reconfiguration import (
    prepare_running_role_reconfiguration,
)
from ...services.related_role_service import (
    RelatedRoleError,
    create_related_role,
    related_role_created_payload,
)
from ...services.related_role_spec_hydration import (
    hydrate_related_role_draft_from_saved_spec,
)
from ...services.requisition_template_service import resolve_template
from ...services.role_brief_service import (
    ensure_ref_code,
    materialize_brief_to_role,
    publish_job_page,
)
from ...services.role_criteria_service import sync_derived_criteria
from ...services.role_intent_fingerprint import role_intent_fingerprint
from ...services.role_concurrency import assert_role_version, bump_role_version
from ...services.role_change_audit import (
    ROLE_CHANGE_ACTION_JOB_SPEC_UPDATED,
    add_role_change_event,
    capture_role_change_snapshot,
    latest_role_change_actor,
)
from ...services.task_provisioning_service import (
    MIN_ASSESSMENT_INPUT_CHARS,
    role_assessment_input_text,
)
from ..identity_access.organization_serialization import resolve_active_ats
from .requisition_shared import _ats_spec, _job_page_url, _org
from .job_authorization import JobPermission, require_job_permission
from .roles_management_routes import (
    _request_autogenerate_assessment_task,
)

router = APIRouter(tags=["Requisitions"])


class PublishRequisition(BaseModel):
    jd_markdown: str = ""
    # Initial publication creates a new Role and has no prior version. Every
    # re-publication is a shared job-spec write and must name the snapshot the
    # recruiter reviewed.
    expected_version: int | None = Field(default=None, ge=1)


@router.post("/requisitions/{brief_id}/publish")
def publish_requisition(
    brief_id: int,
    data: PublishRequisition,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Publish the brief: stand up an INACTIVE Taali job + a shareable spec.

    Three things, all idempotent and re-publish-safe (the brief is NEVER locked,
    so it stays editable):
      1. Mint-once a ``ref_code`` (the ATS bridge match key).
      2. Create/refresh an inactive ``Role`` (``job_status=draft``) linked to the
         brief and materialize its criteria — the job the recruiter sees in Jobs
         and whose spec can optionally be distributed through the active ATS.
      3. Snapshot public-safe fields onto the PUBLIC careers JobPage (one per
         brief; re-publish reuses the token).

    Returns provider-neutral ATS metadata plus ``ats_spec``.  The historical
    ``workable_spec`` key remains as a compatibility alias.
    """
    # Read the link state first so linked writes can follow the global lock
    # order (Role, then RoleBrief). Initial publication has no Role yet and
    # therefore locks only the draft brief.
    brief_probe = (
        db.query(RoleBrief)
        .filter(
            RoleBrief.id == brief_id,
            RoleBrief.organization_id == current_user.organization_id,
        )
        .first()
    )
    if brief_probe is None:
        raise HTTPException(status_code=404, detail="Requisition not found")
    initial_role_id = (
        int(brief_probe.role_id) if brief_probe.role_id is not None else None
    )
    source_role_id = (
        int(brief_probe.source_role_id)
        if brief_probe.source_role_id is not None
        else None
    )
    locked: Role | None = None
    if source_role_id is not None:
        # Related-role creation is a source-job mutation boundary. Lock and
        # authorize the original ATS role before the draft, preserving the
        # platform-wide Role -> RoleBrief lock order.
        locked = require_job_permission(
            db,
            current_user=current_user,
            role_id=initial_role_id or source_role_id,
            permission=JobPermission.EDIT_ROLE,
        )
        brief = (
            db.query(RoleBrief)
            .filter(
                RoleBrief.id == brief_id,
                RoleBrief.organization_id == current_user.organization_id,
            )
            .with_for_update(of=RoleBrief)
            .populate_existing()
            .first()
        )
        if brief is None:
            raise HTTPException(status_code=404, detail="Requisition not found")
        if int(brief.source_role_id or 0) != source_role_id:
            raise HTTPException(
                status_code=409,
                detail="The related-role draft's source changed; refresh and retry.",
            )
        if (int(brief.role_id) if brief.role_id is not None else None) != initial_role_id:
            raise HTTPException(
                status_code=409,
                detail="The related-role draft changed; refresh and retry.",
            )
    elif initial_role_id is not None:
        locked = require_job_permission(
            db,
            current_user=current_user,
            role_id=initial_role_id,
            permission=JobPermission.EDIT_ROLE,
        )
        brief = (
            db.query(RoleBrief)
            .filter(
                RoleBrief.id == brief_id,
                RoleBrief.organization_id == current_user.organization_id,
            )
            .with_for_update(of=RoleBrief)
            .populate_existing()
            .first()
        )
        if brief is None:
            raise HTTPException(status_code=404, detail="Requisition not found")
        if int(brief.role_id or 0) != initial_role_id:
            raise HTTPException(
                status_code=409,
                detail="The requisition's linked job changed; refresh and retry.",
            )
    else:
        brief = (
            db.query(RoleBrief)
            .filter(
                RoleBrief.id == brief_id,
                RoleBrief.organization_id == current_user.organization_id,
            )
            .with_for_update(of=RoleBrief)
            .populate_existing()
            .first()
        )
        if brief is None:
            raise HTTPException(status_code=404, detail="Requisition not found")
        if brief.role_id is not None:
            raise HTTPException(
                status_code=409,
                detail="The requisition was published; refresh and retry.",
            )
        if (
            getattr(current_user, "role", None) != "owner"
            and int(brief.created_by_user_id or 0) != int(current_user.id)
        ):
            raise HTTPException(status_code=403, detail="Forbidden")
    # Enforce the same "required brief fields must be filled" gate the frontend
    # applies — the API is the source of truth, so a direct call can't publish a
    # half-filled requisition that skips the UI guard.
    org = _org(db, current_user.organization_id)
    template = resolve_template(org)
    # Pre-fix related-role drafts may still carry their cloned JD only in
    # ``agent_state.jd_override``. Recover explicitly headed responsibilities
    # before applying the authoritative gap gate so a direct API publish has the
    # same backward-compatible behaviour as opening the draft in the UI.
    if source_role_id is not None and hydrate_related_role_draft_from_saved_spec(brief):
        brief.completeness = compute_completeness(brief, template)
        db.flush()
    gaps = compute_gaps(brief, template)
    if gaps:
        labels = [g.get("label") or g.get("key") or "a required field" for g in gaps]
        raise HTTPException(
            status_code=422,
            detail=(
                "This requisition can't be published yet — fill the required fields first: "
                + ", ".join(labels)
            ),
        )
    if not (data.jd_markdown or "").strip():
        raise HTTPException(
            status_code=422,
            detail=(
                "A rendered job description is required before publishing so "
                "the role can be scored and run by the agent."
            ),
        )
    if source_role_id is not None:
        if initial_role_id is not None:
            raise HTTPException(
                status_code=409,
                detail="This related-role draft has already been created.",
            )
        try:
            related, evaluation_counts = create_related_role(
                db,
                role_id=source_role_id,
                organization_id=int(current_user.organization_id),
                creator_user_id=int(current_user.id),
                name=(brief.title or "Untitled related role"),
                job_spec_text=data.jd_markdown,
                brief=brief,
            )
        except RelatedRoleError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        receipt = related_role_created_payload(related, evaluation_counts)
        return {
            **receipt,
            "related_role": True,
            "status": BRIEF_STATUS_APPLIED,
            "role_id": int(related.id),
            "version": int(related.version or 1),
            "job_status": related.job_status,
            "source_role_id": source_role_id,
        }
    ref_code = ensure_ref_code(db, brief)
    old_intent_fingerprint: str | None = None
    role_was_enabled = False
    existing_role = brief.role_id is not None
    audit_before: dict | None = None
    audit_from_version: int | None = None
    if existing_role:
        # Authorization and the Role→Brief locks were acquired above, so hiring
        # team removal and linked-brief edits cannot invert lock order here.
        assert locked is not None
        if data.expected_version is None:
            raise HTTPException(
                status_code=422,
                detail="expected_version is required when republishing a requisition",
            )
        assert_role_version(
            locked,
            expected_version=int(data.expected_version),
            current_role=lambda: {
                "id": int(locked.id),
                "version": int(locked.version or 1),
                "name": locked.name,
                "job_spec_text": locked.job_spec_text,
                "agentic_mode_enabled": bool(locked.agentic_mode_enabled),
            },
            changed_by=lambda: latest_role_change_actor(
                db,
                organization_id=int(current_user.organization_id),
                role_id=int(locked.id),
            ),
        )
        audit_before = capture_role_change_snapshot(locked)
        audit_from_version = int(locked.version or 1)
        old_intent_fingerprint = role_intent_fingerprint(locked, db=db)
        role_was_enabled = bool(locked.agentic_mode_enabled)
        db.expire(locked, ["tasks"])
    role = materialize_brief_to_role(
        db,
        brief,
        mark_applied=False,
        job_status=JOB_STATUS_DRAFT,
        job_spec_text=data.jd_markdown,
    )
    if not existing_role:
        db.add(
            JobHiringTeam(
                organization_id=int(current_user.organization_id),
                role_id=int(role.id),
                user_id=int(current_user.id),
                team_role=TEAM_ROLE_HIRING_MANAGER,
            )
        )
    # Publication and autonomous task generation must agree on whether the
    # persisted role has enough input. Requisition criteria and structured
    # fields count alongside the rendered Markdown. Fail now—before exposing a
    # preview—instead of accepting publish and leaving Turn on blocked later.
    assessment_input_length = len(role_assessment_input_text(role))
    if (
        not bool(getattr(role, "auto_skip_assessment", False))
        and assessment_input_length < MIN_ASSESSMENT_INPUT_CHARS
    ):
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail=(
                "This requisition needs a little more role detail before "
                "publishing so the agent can create its assessment "
                f"({assessment_input_length}/{MIN_ASSESSMENT_INPUT_CHARS} characters)."
            ),
        )
    # Publish is the job-spec/intent mutation boundary. Keep every downstream
    # artifact aligned in the same transaction: derived criteria, score
    # staleness, pending-decision supersession, and tech-question invalidation.
    sync_derived_criteria(db, role)
    db.flush()
    new_intent_fingerprint = role_intent_fingerprint(role, db=db)
    material_intent_changed = bool(
        not existing_role
        or old_intent_fingerprint != new_intent_fingerprint
    )
    if existing_role and material_intent_changed:
        mark_role_scores_stale(
            db,
            role.id,
            reason="requisition_republished",
            dispatch_tech_questions=False,
        )
    page = publish_job_page(db, brief, jd_markdown=data.jd_markdown)
    reconfiguration = None
    if existing_role and material_intent_changed and role_was_enabled:
        reconfiguration = prepare_running_role_reconfiguration(
            db,
            role=role,
            user_id=int(current_user.id),
            target_fingerprint=new_intent_fingerprint,
        )
    elif not existing_role or material_intent_changed:
        _request_autogenerate_assessment_task(
            role,
            reason="requisition_publish",
            supersede_generated_drafts=True,
            defer_until_activation=True,
        )
    if existing_role and material_intent_changed:
        audit_to_version = bump_role_version(role)
        add_role_change_event(
            db,
            role=role,
            before=audit_before or {},
            action=ROLE_CHANGE_ACTION_JOB_SPEC_UPDATED,
            actor_user_id=int(current_user.id),
            from_version=int(audit_from_version or 1),
            to_version=audit_to_version,
            reason="requisition republished with changed role intent",
            request_id=get_request_id(),
            allow_empty_changes=True,
        )
    db.commit()
    db.refresh(role)
    db.refresh(page)

    if reconfiguration is not None and reconfiguration.dispatch_generation:
        # Latency optimization only: the durable pending request is recovered
        # by the minute sweep if the broker is unavailable or this process dies.
        try:
            from ...tasks.assessment_tasks import generate_assessment_task_for_role

            generate_assessment_task_for_role.delay(
                int(role.id), int(role.organization_id)
            )
        except Exception:
            pass

    # Initial publication is spend-free: only the recruiter's first Turn on
    # authorizes model-backed work. A materially changed, already-running role
    # reuses that durable authorization to replace its generated artifacts and
    # resume automatically; ambiguous/manual task choices fail closed for HITL.
    active_ats = resolve_active_ats(org)
    ats_provider = active_ats if active_ats in {"workable", "bullhorn"} else None
    ats_spec = _ats_spec(data.jd_markdown, ref_code)
    return {
        "job_page_id": page.id,
        "token": page.token,
        "url": _job_page_url(page.token),
        "status": page.status,
        "published_at": page.published_at.isoformat() if page.published_at else None,
        "ref_code": ref_code,
        "role_id": role.id,
        "version": int(role.version or 1),
        "job_status": role.job_status,
        "ats_provider": ats_provider,
        "ats_spec": ats_spec,
        "workable_spec": ats_spec,
    }
