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
from ....models.role import Role
from ....services.document_service import (
    sanitize_json_for_storage,
    sanitize_text_for_storage,
)
from ....services.s3_service import generate_s3_key, upload_bytes_to_s3

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _job_order_id(job_order: dict) -> str:
    return str(job_order.get("id") or "").strip()


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

    role = (
        db.query(Role)
        .filter(Role.organization_id == org.id, Role.bullhorn_job_order_id == job_id)
        .first()
    )
    created = False
    if not role:
        # Seed budget + threshold from workspace defaults, exactly like the
        # Workable arm. Bullhorn supplies no scoring criteria, so a new role's
        # chip set is snapshotted from ``org_criteria`` below via
        # ``sync_all_criteria``.
        org_budget = getattr(org, "default_role_budget_cents", None)
        org_threshold = getattr(org, "default_score_threshold", None)
        role = Role(
            organization_id=org.id,
            source="bullhorn",
            bullhorn_job_order_id=job_id,
            name=title,
            monthly_usd_budget_cents=int(org_budget) if org_budget is not None else None,
            score_threshold=(
                max(0, min(100, int(org_threshold))) if org_threshold is not None else None
            ),
        )
        db.add(role)
        created = True

    role.deleted_at = None  # restore if soft-deleted
    role.source = "bullhorn"
    role.bullhorn_job_order_id = job_id
    role.bullhorn_job_data = sanitize_json_for_storage(job_order)
    role.name = title

    prev_job_spec = role.job_spec_text or ""
    formatted_spec = format_job_spec_from_job_order(job_order)
    if formatted_spec:
        role.job_spec_text = formatted_spec
        role.description = formatted_spec
    db.flush()

    spec_changed = (role.job_spec_text or "") != prev_job_spec
    if (created or spec_changed) and (role.job_spec_text or "").strip():
        _store_job_spec_attachment(role)
        _sync_role_criteria(db, role, created=created, spec_changed=spec_changed)

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
    except Exception:  # pragma: no cover — never break the sync on storage
        logger.exception("Failed saving Bullhorn job spec file for role_id=%s", role.id)


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
