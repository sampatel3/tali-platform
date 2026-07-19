"""JobOrder → Role upsert for the Bullhorn full sync.

Mirrors Workable's ``_upsert_role`` (``workable/sync_service.py``): map the
remote job's structural fields onto a Taali :class:`Role`, keep the raw payload
in a JSON blob (``bullhorn_job_data``), and build one formatted job-spec string
for display/attachment + downstream CV matching.

Keyed on ``(organization_id, bullhorn_job_order_id)`` via the role's unique
constraint, so a re-sync updates the same role instead of minting a duplicate.
Job-spec side effects (attachment upload, derived-criteria re-derive) are gated
on the spec actually changing — re-deriving an unchanged spec on every sync tick
would churn row ids and spuriously invalidate every pending decision for the
role (the same trap Workable guards against).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ....models.organization import Organization
from ....models.role import JOB_STATUS_DRAFT, JOB_STATUS_OPEN, Role
from ....models.role_brief import RoleBrief
from ....services.document_service import (
    sanitize_json_for_storage,
    sanitize_text_for_storage,
)
from ....services.agent_policy_settings import apply_workspace_agent_defaults
from ....services.job_spec_override_service import has_manual_job_spec_override
from ....services.role_change_audit import (
    ROLE_CHANGE_ACTION_JOB_SPEC_UPDATED,
    ROLE_CHANGE_ACTION_RESTORED,
    ROLE_CHANGE_ACTION_SOFT_DELETED,
    ROLE_CHANGE_ACTION_UPDATED,
    add_role_change_event,
    build_role_change_diff,
    capture_role_change_snapshot,
)
from ....services.role_concurrency import bump_role_version
from ....services.role_lifecycle import (
    restore_role_from_ats,
    stop_role_for_ats_deletion,
)
from ....services.s3_service import generate_s3_key, upload_bytes_to_s3

logger = logging.getLogger(__name__)


def _locked_existing_role(db: Session, *criteria: object) -> Role | None:
    """Lock a role and refresh an older identity-mapped version if necessary."""

    locked = (
        db.query(Role.id, Role.version)
        .filter(*criteria)
        .with_for_update(of=Role)
        .first()
    )
    if locked is None:
        return None
    role = db.get(Role, int(locked.id))
    if role is None:
        return None
    locked_version = int(locked.version or 1)
    if int(role.version or 1) != locked_version:
        db.refresh(role)
    return role


def _record_bullhorn_role_change(
    db: Session,
    *,
    role: Role,
    before: dict | None,
    from_version: int | None,
    job_id: str,
    force_audit: bool = False,
) -> None:
    """Version and audit one material Bullhorn update in the sync transaction."""

    if before is None or from_version is None:
        return
    after = capture_role_change_snapshot(role)
    changes = build_role_change_diff(before, after)
    if not changes and not force_audit:
        return
    to_version = bump_role_version(role)
    spec_fields = {
        "description",
        "job_spec_text",
        "job_spec_filename",
        "job_spec_file_url",
        "job_spec_uploaded_at",
        "job_spec_manually_edited_at",
    }
    restored = (
        before.get("deleted_at") is not None
        and after.get("deleted_at") is None
    )
    add_role_change_event(
        db,
        role=role,
        before=before,
        action=(
            ROLE_CHANGE_ACTION_RESTORED
            if restored
            else (
                ROLE_CHANGE_ACTION_JOB_SPEC_UPDATED
                if spec_fields.intersection(changes)
                else ROLE_CHANGE_ACTION_UPDATED
            )
        ),
        actor_user_id=None,
        from_version=from_version,
        to_version=to_version,
        reason=(
            "Bullhorn role restored with agent off"
            if restored
            else (
                "Bullhorn requisition role linked"
                if force_audit
                else "Bullhorn pull sync"
            )
        ),
        request_id=f"bullhorn-job:{job_id}",
        allow_empty_changes=force_audit,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _job_order_id(job_order: dict) -> str:
    return str(job_order.get("id") or "").strip()


def complete_open_job_order_ids(job_orders: list[dict]) -> set[str]:
    """Validate a complete open-JobOrder snapshot and return its unique ids.

    Pagination completeness is proven by
    :meth:`BullhornService.search_open_job_orders_complete`; this second gate
    proves that every returned row has a usable, unique id and is actually open.
    Any malformed/duplicate row aborts before missing-id repair can mutate the
    database.
    """
    open_ids: set[str] = set()
    for job_order in job_orders:
        if not isinstance(job_order, dict):
            raise ValueError("complete JobOrder snapshot contains an invalid row")
        job_id = _job_order_id(job_order)
        if not job_id.isdigit():
            raise ValueError("complete JobOrder snapshot contains an invalid id")
        is_open = job_order.get("isOpen")
        if type(is_open) is not bool or is_open is not True:
            raise ValueError("complete open JobOrder snapshot contains a non-open row")
        if job_id in open_ids:
            raise ValueError("complete JobOrder snapshot contains a duplicate id")
        open_ids.add(job_id)
    return open_ids


def soft_close_role(role: Role, *, closed_at: datetime | None = None) -> bool:
    """Apply the canonical Bullhorn close/delete semantics to one local role."""
    if role.deleted_at is not None:
        return False
    role.deleted_at = closed_at or _now()
    payload = dict(role.bullhorn_job_data) if isinstance(role.bullhorn_job_data, dict) else {}
    payload["isOpen"] = False
    role.bullhorn_job_data = sanitize_json_for_storage(payload)
    return True


def repair_roles_from_complete_open_snapshot(
    db: Session,
    org: Organization,
    job_orders: list[dict],
    *,
    closed_at: datetime | None = None,
) -> tuple[set[str], dict[str, int]]:
    """Soft-close local Bullhorn roles absent from a proven-complete open set.

    Callers fetch ``job_orders`` with ``search_open_job_orders_complete`` while
    holding the existing per-org Bullhorn mutex. Validation happens in full
    before the active local rows are read or changed. Workable-linked roles are
    excluded because Workable is authoritative during a dual-connect migration.
    The returned telemetry contains counts only, never remote ids or payloads.
    """
    open_ids = complete_open_job_order_ids(job_orders)
    active_roles = (
        db.query(Role)
        .filter(
            Role.organization_id == org.id,
            Role.bullhorn_job_order_id.isnot(None),
            Role.workable_job_id.is_(None),
            Role.deleted_at.is_(None),
        )
        .order_by(Role.id.asc())
        .with_for_update(of=Role)
        .populate_existing()
        .all()
    )
    closed = 0
    stamp = closed_at or _now()
    for role in active_roles:
        local_job_id = str(role.bullhorn_job_order_id or "").strip()
        if local_job_id in open_ids:
            continue
        audit_before = capture_role_change_snapshot(role)
        audit_from_version = int(role.version or 1)
        closed_role = soft_close_role(role, closed_at=stamp)
        stopped_agent = stop_role_for_ats_deletion(
            role,
            deleted_at=stamp,
            provider="Bullhorn",
        )
        if closed_role or stopped_agent:
            audit_to_version = bump_role_version(role)
            add_role_change_event(
                db,
                role=role,
                before=audit_before,
                action=ROLE_CHANGE_ACTION_SOFT_DELETED,
                actor_user_id=None,
                from_version=audit_from_version,
                to_version=audit_to_version,
                reason="Bullhorn complete snapshot closed missing job; agent turned off",
                request_id=f"bullhorn-job-repair:{local_job_id}",
            )
            closed += 1
    db.flush()
    counts = {
        "remote_open_count": len(open_ids),
        "local_active_before": len(active_roles),
        "roles_closed": closed,
        "local_active_after": len(active_roles) - closed,
    }
    logger.info(
        "Bullhorn open-role repair org_id=%s remote_open_count=%s "
        "local_active_before=%s roles_closed=%s local_active_after=%s",
        org.id,
        counts["remote_open_count"],
        counts["local_active_before"],
        counts["roles_closed"],
        counts["local_active_after"],
    )
    return open_ids, counts


def format_job_spec_from_job_order(job_order: dict) -> str:
    """Build a formatted, sanitized job-spec string from a JobOrder payload.

    Bullhorn's JobOrder carries free-text ``description`` (and sometimes
    ``publicDescription``) plus a handful of scalar structural fields. We render
    a small Markdown block — title, structural fields, description — never a raw
    dict/list repr. Sanitized for storage on the way out.
    """
    if not isinstance(job_order, dict) or not job_order:
        return ""
    lines: list[str] = []
    title = (
        job_order.get("title")
        or job_order.get("name")
        or f"Bullhorn job {_job_order_id(job_order) or 'unknown'}"
    )
    lines.append(f"# {title}")
    lines.append("")

    def _scalar(value: object) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value)
        return None

    # Bullhorn address is a nested object; render city/state/country compactly.
    address = job_order.get("address")
    if isinstance(address, dict):
        parts = [
            _scalar(address.get(k))
            for k in ("city", "state", "countryName", "countryCode", "zip")
        ]
        loc = ", ".join(p for p in parts if p)
        if loc:
            lines.append(f"**Location:** {loc}")

    for key, label in (
        ("employmentType", "Employment type"),
        ("status", "Status"),
        ("categories", "Category"),
        ("clientCorporation", "Client"),
    ):
        raw = job_order.get(key)
        # clientCorporation / categories come back as nested objects; pull a name.
        if isinstance(raw, dict):
            raw = raw.get("name")
        value = _scalar(raw)
        if value:
            lines.append(f"**{label}:** {value}")
    lines.append("")

    for key, label in (("description", "Description"), ("publicDescription", "Description")):
        raw = job_order.get(key)
        if isinstance(raw, str) and raw.strip():
            # Bullhorn descriptions are HTML; reuse the Workable strip helper so
            # we never store markup or a repr.
            from ..workable.sync_service import _strip_html

            cleaned = _strip_html(raw).strip()
            if cleaned:
                lines.append(f"## {label}")
                lines.append("")
                lines.append(cleaned)
                lines.append("")
                break  # description OR publicDescription, not both

    result = "\n".join(lines).strip()
    return sanitize_text_for_storage(result)


def _adopt_requisition_role(
    db: Session,
    org: Organization,
    *,
    job_id: str,
    title: str,
    job_order: dict,
    audit_context: dict[str, object] | None = None,
) -> Role | None:
    """Link an open Bullhorn JobOrder to its stamped requisition role.

    Requisition publish embeds a mint-once ``Taali ref: TAL-XXXXX`` marker in
    the external job specification.  Before creating a Bullhorn-only duplicate,
    scan the imported description/title and adopt the matching unlinked role.
    It may still be a draft or already be native-live because Turn on does not
    wait for the next ATS pull. The existing role retains its native JobPage,
    workspace/brief criteria, agent settings, and budget; only its ATS linkage
    and live job status change.
    """

    if not job_id:
        return None

    from ....services.role_brief_service import find_ref_code

    code = None
    for raw in (
        job_order.get("description"),
        job_order.get("publicDescription"),
        title,
    ):
        if isinstance(raw, str):
            code = find_ref_code(raw)
            if code:
                break
    if not code:
        return None

    brief = (
        db.query(RoleBrief)
        .filter(
            RoleBrief.organization_id == org.id,
            RoleBrief.ref_code == code,
        )
        .first()
    )
    if brief is None or not brief.role_id:
        return None

    role = _locked_existing_role(
        db,
        Role.id == brief.role_id,
        Role.organization_id == org.id,
    )
    if role is None:
        return None
    # Never hijack a terminal role or a role linked to either provider. Workable
    # remains authoritative in dual-connect migrations, matching
    # resolve_ats_provider's precedence.
    if (
        role.workable_job_id
        or role.bullhorn_job_order_id
        or role.job_status not in (None, JOB_STATUS_DRAFT, JOB_STATUS_OPEN)
    ):
        return None

    if audit_context is not None:
        audit_context["before"] = capture_role_change_snapshot(role)
        audit_context["from_version"] = int(role.version or 1)
        audit_context["adopted"] = True

    role.bullhorn_job_order_id = job_id
    role.job_status = JOB_STATUS_OPEN
    role.deleted_at = None
    logger.info(
        "Bullhorn bridge: adopted requisition role_id=%s into job_order_id=%s via ref %s",
        role.id,
        job_id,
        code,
    )
    return role


def upsert_role_from_job_order(
    db: Session, org: Organization, job_order: dict
) -> tuple[Role | None, bool]:
    """Upsert a Role from a Bullhorn JobOrder. Returns ``(role, created)``.

    Returns ``(None, False)`` when the JobOrder has no usable id (nothing to key
    on). Mirrors Workable's role upsert: map structural fields, store the raw
    blob, build the spec, and only re-do the expensive spec side effects when the
    spec actually changed (or the role was just created).
    """
    job_id = sanitize_text_for_storage(_job_order_id(job_order))
    if not job_id:
        logger.debug("Skipping Bullhorn JobOrder with no id: keys=%s", list(job_order.keys()))
        return None, False

    title = sanitize_text_for_storage(
        str(job_order.get("title") or job_order.get("name") or f"Bullhorn job {job_id}").strip()
    )

    role = _locked_existing_role(
        db,
        Role.organization_id == org.id,
        Role.bullhorn_job_order_id == job_id,
    )
    if role is not None and str(role.workable_job_id or "").strip():
        # Workable has provider precedence while both integrations are linked.
        # Keep the Bullhorn id as migration evidence, but do not let this mirror
        # overwrite the Workable-owned role's profile, lifecycle, agent, or spec.
        logger.info(
            "Skipping Bullhorn JobOrder upsert for Workable-owned role_id=%s",
            role.id,
        )
        return role, False
    created = False
    audit_before = None
    audit_from_version = None
    adoption_audit: dict[str, object] = {}
    if not role:
        role = _adopt_requisition_role(
            db,
            org,
            job_id=job_id,
            title=title,
            job_order=job_order,
            audit_context=adoption_audit,
        )
        if role is not None:
            audit_before = adoption_audit.get("before")
            audit_from_version = adoption_audit.get("from_version")
    if not role:
        role = Role(
            organization_id=org.id,
            source="bullhorn",
            bullhorn_job_order_id=job_id,
            name=title,
        )
        apply_workspace_agent_defaults(role, org)
        db.add(role)
        created = True
    elif not adoption_audit.get("adopted"):
        audit_before = capture_role_change_snapshot(role)
        audit_from_version = int(role.version or 1)

    previous_job_data = (
        role.bullhorn_job_data if isinstance(role.bullhorn_job_data, dict) else None
    )
    previous_ats_spec = None
    if previous_job_data and any(
        isinstance(previous_job_data.get(key), str)
        and previous_job_data.get(key).strip()
        for key in ("description", "publicDescription")
    ):
        previous_ats_spec = format_job_spec_from_job_order(previous_job_data)
    manual_spec_override = has_manual_job_spec_override(
        role,
        ats_source="bullhorn",
        cached_ats_spec=previous_ats_spec,
    )

    restore_role_from_ats(role, restored_at=_now(), provider="Bullhorn")
    role.source = "bullhorn"
    role.bullhorn_job_order_id = job_id
    role.bullhorn_job_data = sanitize_json_for_storage(job_order)
    role.name = title

    prev_job_spec = role.job_spec_text or ""
    formatted_spec = format_job_spec_from_job_order(job_order)
    if formatted_spec and not manual_spec_override:
        role.job_spec_text = formatted_spec
        role.description = formatted_spec
    elif formatted_spec and manual_spec_override:
        logger.info(
            "Preserving recruiter-edited job spec during Bullhorn sync role_id=%s",
            role.id,
        )
    db.flush()

    spec_changed = (role.job_spec_text or "") != prev_job_spec
    if (created or spec_changed) and (role.job_spec_text or "").strip():
        _store_job_spec_attachment(role)
        _sync_role_criteria(db, role, created=created, spec_changed=spec_changed)
        from ....platform.config import settings

        if getattr(settings, "AUTO_GENERATE_ASSESSMENT_TASKS", False):
            from ....services.task_provisioning_service import (
                request_assessment_task_provisioning,
            )

            # The enclosing Bullhorn sync owns the commit. Beat is the durable
            # dispatcher, so this path needs no best-effort broker side effect.
            request_assessment_task_provisioning(
                role,
                reason="bullhorn_role_spec",
                supersede_generated_drafts=bool(spec_changed),
            )

    _record_bullhorn_role_change(
        db,
        role=role,
        before=audit_before,
        from_version=audit_from_version,
        job_id=job_id,
        force_audit=bool(adoption_audit.get("adopted")),
    )

    return role, created


def _store_job_spec_attachment(role: Role) -> None:
    """Upload the formatted spec as a .txt attachment (best-effort, never fatal)."""
    try:
        spec_content = (role.job_spec_text or "").strip().encode("utf-8")
        spec_filename = sanitize_text_for_storage(
            f"job-spec-{role.name or role.id}.txt"
        ).replace("/", "-")
        s3_key = generate_s3_key("job_spec", role.id, spec_filename)
        spec_url = upload_bytes_to_s3(spec_content, s3_key, content_type="text/plain")
        if spec_url:
            role.job_spec_file_url = spec_url
            role.job_spec_filename = spec_filename
            role.job_spec_uploaded_at = _now()
        else:
            logger.warning(
                "Skipping Bullhorn job-spec store for role_id=%s — object storage unavailable",
                role.id,
            )
    except Exception as exc:  # pragma: no cover — never break the sync on storage
        logger.error(
            "Failed saving Bullhorn job spec file role_id=%s error_type=%s",
            role.id,
            type(exc).__name__,
        )


def _sync_role_criteria(db: Session, role: Role, *, created: bool, spec_changed: bool) -> None:
    """Snapshot org criteria on create; on a real spec change, re-derive safely.

    Mirrors Workable's gating: an agent-on role routes a spec change through the
    material-change assessment (which protects pending decisions from a blind
    re-derive + forced paid re-evaluation); an agent-off role re-derives
    directly. No paid scoring is triggered here either way.
    """
    if created:
        from ....services.role_criteria_service import sync_all_criteria

        sync_all_criteria(db, role)
    elif spec_changed:
        if getattr(role, "agentic_mode_enabled", False):
            from ....services.material_change import handle_spec_change

            handle_spec_change(db, role)
        else:
            from ....services.role_criteria_service import sync_derived_criteria

            sync_derived_criteria(db, role)
