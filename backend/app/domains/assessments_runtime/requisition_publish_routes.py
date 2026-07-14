"""Requisition PUBLISH surface — snapshot a brief into a shareable PUBLIC job page.

Split out of ``requisition_routes`` and re-composed there via
``router.include_router``; the paths/prefix are unchanged.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.role import JOB_STATUS_DRAFT, Role
from ...models.user import User
from ...platform.database import get_db
from ...services.cv_score_orchestrator import mark_role_scores_stale
from ...services.requisition_chat_capture import compute_gaps
from ...services.requisition_reconfiguration import (
    prepare_running_role_reconfiguration,
)
from ...services.requisition_template_service import resolve_template
from ...services.role_brief_service import (
    ensure_ref_code,
    materialize_brief_to_role,
    publish_job_page,
)
from ...services.role_criteria_service import sync_derived_criteria
from ...services.role_intent_fingerprint import role_intent_fingerprint
from ...services.task_provisioning_service import (
    MIN_ASSESSMENT_INPUT_CHARS,
    role_assessment_input_text,
)
from ..identity_access.organization_serialization import resolve_active_ats
from .requisition_shared import _ats_spec, _get_brief, _job_page_url, _org
from .roles_management_routes import (
    _request_autogenerate_assessment_task,
)

router = APIRouter(tags=["Requisitions"])


class PublishRequisition(BaseModel):
    jd_markdown: str = ""


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
    brief = _get_brief(db, current_user.organization_id, brief_id)
    # Enforce the same "required brief fields must be filled" gate the frontend
    # applies — the API is the source of truth, so a direct call can't publish a
    # half-filled requisition that skips the UI guard.
    org = _org(db, current_user.organization_id)
    gaps = compute_gaps(brief, resolve_template(org))
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
    ref_code = ensure_ref_code(db, brief)
    old_intent_fingerprint: str | None = None
    role_was_enabled = False
    existing_role = brief.role_id is not None
    if existing_role:
        # Serialize republish against a deferred activation worker before
        # materialization reads or mutates the old task/role snapshot.
        locked = (
            db.query(Role)
            .filter(
                Role.id == int(brief.role_id),
                Role.organization_id == int(current_user.organization_id),
            )
            .with_for_update()
            .one_or_none()
        )
        if locked is None:
            raise HTTPException(status_code=404, detail="Linked role not found")
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
        "job_status": role.job_status,
        "ats_provider": ats_provider,
        "ats_spec": ats_spec,
        "workable_spec": ats_spec,
    }
