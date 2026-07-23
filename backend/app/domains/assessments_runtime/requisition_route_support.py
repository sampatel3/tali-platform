"""Request models and mutation helpers for the core requisition routes.

Keeping these route-adjacent contracts here leaves ``requisition_routes``
focused on HTTP orchestration while preserving its historical imports.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...models.role import Role
from ...models.role_brief import RoleBrief
from ...models.user import User
from ...platform.request_context import get_request_id
from ...services.requisition_chat_service import (
    derive_company_blurb,
    seed_opening_message,
    warm_start_fields,
)
from ...services.role_concurrency import assert_role_version, bump_role_version
from ...services.role_change_audit import (
    add_role_change_event,
    capture_role_change_snapshot,
    latest_role_change_actor,
)
from ...services.related_role_service import (
    RelatedRoleError,
    create_related_role_draft,
)
from ...services.requisition_similar_service import (
    apply_agnostic_fields,
    standardize_agnostic_fields,
)
from ...services.requisition_template_service import iter_fields, template_key_to_column
from ...services.role_brief_service import create_brief
from .job_authorization import JobPermission, require_job_permission


class CreateRequisition(BaseModel):
    source_kind: Optional[str] = None
    # When set, start the existing conversational intake as a pre-populated
    # related-role draft cloned once from this logical source role.
    source_role_id: int | None = Field(default=None, ge=1)


class IntakeInput(BaseModel):
    input: str
    source_kind: Optional[str] = None
    expected_version: int | None = Field(default=None, ge=1)


class AnswerRequisition(BaseModel):
    """A single structured answer to one requisition field."""

    field_key: str
    value: Any = None
    expected_version: int | None = Field(default=None, ge=1)


def apply_manual_spec_state(
    brief: RoleBrief, data: dict[str, Any], template: dict[str, Any]
) -> None:
    """Keep manual Brief/JD edits on one canonical-spec revision."""

    override_explicit = "jd_override" in data
    state = dict(brief.agent_state or {})
    changed = False
    change_mode = "manual"
    if override_explicit:
        raw = data.pop("jd_override")
        override = (raw or "").strip() if isinstance(raw, str) else raw
        if override:
            state["jd_override"] = override
            state["canonical_spec_mode"] = "verbatim"
        else:
            state.pop("jd_override", None)
            state["canonical_spec_mode"] = "structured"
        changed = True
    else:
        spec_edit_keys = {
            template_key_to_column(field["key"]) or "custom_fields"
            for _section, field in iter_fields(template)
        }
        if state.get("jd_override") and set(data).intersection(spec_edit_keys):
            state.pop("jd_override", None)
            state["canonical_spec_mode"] = "structured"
            change_mode = "manual_brief"
            changed = True
    if not changed:
        return
    try:
        revision = int(state.get("job_spec_revision") or 0) + 1
    except (TypeError, ValueError):
        revision = 1
    state["job_spec_revision"] = revision
    state["job_spec_last_change_mode"] = change_mode
    state.pop("pending_job_spec_source", None)
    brief.agent_state = state


def start_related_role_requisition(
    db: Session,
    *,
    current_user: User,
    source_role_id: int,
    template: dict[str, Any],
) -> RoleBrief:
    """Authorize and clone an ATS role into the conversational draft flow."""

    require_job_permission(
        db,
        current_user=current_user,
        role_id=int(source_role_id),
        permission=JobPermission.EDIT_ROLE,
    )
    try:
        return create_related_role_draft(
            db,
            role_id=int(source_role_id),
            organization_id=int(current_user.organization_id),
            creator_user_id=int(current_user.id),
            template=template,
        )
    except RelatedRoleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def start_standard_requisition(
    db: Session,
    *,
    current_user: User,
    source_kind: str | None,
    template: dict[str, Any],
) -> RoleBrief:
    """Create and warm-start the normal conversational job draft."""

    brief = create_brief(
        db,
        organization_id=current_user.organization_id,
        created_by_user_id=current_user.id,
        source_kind=source_kind,
    )
    brief.salary_currency = "AED"
    for field, value in warm_start_fields(
        db, current_user.organization_id, exclude_brief_id=brief.id
    ).items():
        setattr(brief, field, value)
    apply_agnostic_fields(
        db,
        brief,
        standardize_agnostic_fields(db, current_user.organization_id),
    )
    try:
        blurb = derive_company_blurb(db, current_user.organization_id)
    except Exception:
        blurb = None
    if blurb:
        custom = dict(brief.custom_fields or {})
        existing = custom.get("company_description")
        if not (isinstance(existing, str) and existing.strip()):
            custom["company_description"] = blurb
            brief.custom_fields = custom
    seed_opening_message(brief, template)
    db.flush()
    db.commit()
    db.refresh(brief)
    return brief


ROLE_CHANGE_ACTION_REQUISITION_UPDATED = "requisition_brief_updated"

# Requisition services mutate a detached working copy during slow provider
# calls, then copy only these business fields onto the locked live row. Linkage,
# tenancy, creator identity, ref codes, and timestamps are controlled elsewhere
# and must never be copied back from an earlier snapshot.
_BRIEF_CONTROL_FIELDS = frozenset(
    {
        "id",
        "organization_id",
        "role_id",
        "source_role_id",
        "ref_code",
        "created_by_user_id",
        "created_at",
        "updated_at",
    }
)
BRIEF_MUTATION_FIELDS = tuple(
    column.name
    for column in RoleBrief.__table__.columns
    if column.name not in _BRIEF_CONTROL_FIELDS
)


def _brief_snapshot(brief: RoleBrief) -> dict[str, Any]:
    return {
        field: deepcopy(getattr(brief, field, None))
        for field in BRIEF_MUTATION_FIELDS
    }


def clone_brief_for_provider_call(brief: RoleBrief) -> RoleBrief:
    """Return a transient copy suitable for an unlocked LLM/provider call.

    Requisition services deliberately accept a ``RoleBrief`` plus ``Session``.
    A transient copy lets them reuse their extraction logic and database reads
    without flushing an UPDATE (and therefore taking the Brief lock) before the
    canonical Role -> RoleBrief commit boundary.
    """

    values = {
        column.name: deepcopy(getattr(brief, column.name, None))
        for column in RoleBrief.__table__.columns
    }
    return RoleBrief(**values)


def apply_provider_brief_changes(
    live_brief: RoleBrief,
    *,
    baseline: RoleBrief,
    working: RoleBrief,
) -> tuple[str, ...]:
    """Apply only provider-produced deltas to an already locked live brief."""

    before = _brief_snapshot(baseline)
    after = _brief_snapshot(working)
    changed = tuple(
        field for field in BRIEF_MUTATION_FIELDS if before[field] != after[field]
    )
    for field in changed:
        setattr(live_brief, field, deepcopy(after[field]))
    return changed


@dataclass(frozen=True)
class BriefMutationAuthorization:
    brief: RoleBrief
    role: Role | None
    brief_before: dict[str, Any]
    role_before: dict[str, Any] | None
    from_version: int | None


def _current_role_for_conflict(role: Role) -> dict[str, Any]:
    return {
        "id": int(role.id),
        "version": int(role.version or 1),
        "name": role.name,
        "job_status": role.job_status,
        "job_spec_text": role.job_spec_text,
        "agentic_mode_enabled": bool(role.agentic_mode_enabled),
    }


def _assert_linked_expected_version(
    db: Session,
    *,
    role: Role,
    current_user: User,
    expected_version: int | None,
) -> None:
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail="expected_version is required when editing a linked requisition",
        )
    assert_role_version(
        role,
        expected_version=int(expected_version),
        current_role=lambda: _current_role_for_conflict(role),
        changed_by=lambda: latest_role_change_actor(
            db,
            organization_id=int(current_user.organization_id),
            role_id=int(role.id),
        ),
    )


def authorize_brief_mutation(
    db: Session,
    *,
    brief: RoleBrief,
    current_user: User,
    expected_version: int | None = None,
    lock_for_update: bool = True,
) -> BriefMutationAuthorization:
    """Authorize a draft or linked requisition mutation.

    A linked requisition is another editor over the live job, so it uses the
    same locked Role revision. An unlinked draft has no job team yet and is
    writable only by its creator or the organization owner. Provider-backed
    routes use ``lock_for_update=False`` for an early permission/version check,
    do slow work on a detached copy, then call this again with locking at the
    commit boundary.
    """

    initial_role_id = int(brief.role_id) if brief.role_id is not None else None
    if initial_role_id is None:
        # There is no Role lock to order first yet. At the commit boundary,
        # lock the draft itself and refuse/retry if it became linked while this
        # request was waiting; do not acquire Role second and invert order.
        brief_query = db.query(RoleBrief).filter(
            RoleBrief.id == int(brief.id),
            RoleBrief.organization_id == int(current_user.organization_id),
        )
        if lock_for_update:
            brief_query = brief_query.with_for_update(
                of=RoleBrief
            ).populate_existing()
        locked_brief = brief_query.first()
        if locked_brief is None:
            raise HTTPException(status_code=404, detail="Requisition not found")
        if locked_brief.role_id is not None:
            raise HTTPException(
                status_code=409,
                detail="The requisition was linked to a job; refresh and retry.",
            )
        if (
            getattr(current_user, "role", None) == "owner"
            or int(locked_brief.created_by_user_id or 0) == int(current_user.id)
        ):
            return BriefMutationAuthorization(
                brief=locked_brief,
                role=None,
                brief_before=_brief_snapshot(locked_brief),
                role_before=None,
                from_version=None,
            )
        raise HTTPException(status_code=403, detail="Forbidden")

    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=initial_role_id,
        permission=JobPermission.EDIT_ROLE,
        lock_for_update=lock_for_update,
    )
    locked_brief = brief
    if lock_for_update:
        # Canonical lock order: Role first (above), then RoleBrief. Compare the
        # expected revision only after both rows are held.
        locked_brief = (
            db.query(RoleBrief)
            .filter(
                RoleBrief.id == int(brief.id),
                RoleBrief.organization_id == int(current_user.organization_id),
            )
            .with_for_update(of=RoleBrief)
            .populate_existing()
            .first()
        )
        if locked_brief is None:
            raise HTTPException(status_code=404, detail="Requisition not found")
        if int(locked_brief.role_id or 0) != initial_role_id:
            raise HTTPException(
                status_code=409,
                detail="The requisition's linked job changed; refresh and retry.",
            )
    _assert_linked_expected_version(
        db,
        role=role,
        current_user=current_user,
        expected_version=expected_version,
    )
    return BriefMutationAuthorization(
        brief=locked_brief,
        role=role,
        brief_before=_brief_snapshot(locked_brief),
        role_before=capture_role_change_snapshot(role),
        from_version=int(role.version or 1),
    )


def finalize_brief_mutation(
    db: Session,
    *,
    authorization: BriefMutationAuthorization,
    current_user: User,
    reason: str,
) -> bool:
    """Flush an actual brief change and advance its linked Role atomically."""

    after = _brief_snapshot(authorization.brief)
    changed_fields = tuple(
        field
        for field in BRIEF_MUTATION_FIELDS
        if authorization.brief_before[field] != after[field]
    )
    if not changed_fields:
        return False

    db.flush()
    role = authorization.role
    if role is not None:
        from_version = int(authorization.from_version or 1)
        to_version = bump_role_version(role)
        changed_field_names = ", ".join(changed_fields)
        add_role_change_event(
            db,
            role=role,
            before=authorization.role_before or {},
            action=ROLE_CHANGE_ACTION_REQUISITION_UPDATED,
            actor_user_id=int(current_user.id),
            from_version=from_version,
            to_version=to_version,
            reason=f"{reason}; requisition fields: {changed_field_names}",
            request_id=get_request_id(),
            allow_empty_changes=True,
        )
    return True


def apply_provider_changes_at_commit(
    db: Session,
    *,
    baseline: RoleBrief,
    working: RoleBrief,
    current_user: User,
    expected_version: int | None,
    reason: str,
) -> RoleBrief:
    """Recheck and apply an unlocked provider result under canonical locks.

    The caller still owns commit/rollback. Keeping this sequence in one helper
    makes it difficult for chat, legacy intake, and responsibility drafting to
    drift apart on the second authorization/version check.
    """

    db.expire_all()
    latest = (
        db.query(RoleBrief)
        .filter(
            RoleBrief.id == int(baseline.id),
            RoleBrief.organization_id == int(current_user.organization_id),
        )
        .first()
    )
    if latest is None:
        raise HTTPException(status_code=404, detail="Requisition not found")
    authorization = authorize_brief_mutation(
        db,
        brief=latest,
        current_user=current_user,
        expected_version=expected_version,
    )
    apply_provider_brief_changes(
        authorization.brief,
        baseline=baseline,
        working=working,
    )
    finalize_brief_mutation(
        db,
        authorization=authorization,
        current_user=current_user,
        reason=reason,
    )
    return authorization.brief


def readable_value(value: Any) -> str:
    """Render an answer value as a short readable transcript string."""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v).strip() for v in value if str(v).strip())
    return "" if value is None else str(value).strip()


def field_label(template: dict[str, Any], field_key: str) -> str:
    """Return the template label for a field key, falling back to the key."""
    for _section, field in iter_fields(template):
        if field.get("key") == field_key:
            return field.get("label") or field_key
    return field_key
