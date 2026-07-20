"""Manage workspace + role criteria — the structured chip-based intent model.

Chips (rows in ``role_criteria`` and ``org_criteria``) are the source of
truth. The legacy text columns (``Role.additional_requirements``,
``Organization.default_role_requirements``,
``Organization.default_additional_requirements``) were dropped in
alembic 067 + 068; downstream readers consume
:func:`render_role_intent_block` / :func:`render_role_intent_lines` /
:func:`render_org_intent_block` / :func:`render_org_intent_lines`.

Sync model
----------

- **Workspace** authors a list of ``OrganizationCriterion`` rows on Settings →
  AI agent. Each carries a stable ``id`` and a ``bucket``.
- **Role create / Workable import** snapshots all current workspace criteria
  into ``role_criteria`` rows with ``org_criterion_id`` set. Recruiters can
  edit, remove, or add chips on the role page.
- **Sync workspace** (per-role action) re-applies workspace text + bucket on
  any role chip whose ``customized_at`` is null and whose ``org_criterion_id``
  still exists in workspace. Suppressed and customized chips are left alone.
  New workspace chips are added; chips whose workspace counterpart was deleted
  drop their ``org_criterion_id`` (become role-only).
- **Reset to defaults** soft-deletes every existing role chip and re-snapshots
  workspace.
- ``derived_from_spec`` chips (parsed from the job spec) are independent of
  the workspace flow; they keep being managed by :func:`sync_derived_criteria`
  on spec upload.
"""

from __future__ import annotations

from typing import Iterable

from sqlalchemy.orm import Session

from ..models.org_criterion import (
    BUCKET_CONSTRAINT,
    BUCKET_MUST,
    BUCKET_PREFERRED,
    OrganizationCriterion,
)
from ..models.role import Role
from ..models.role_criterion import (
    CRITERION_SOURCE_DERIVED,
    CRITERION_SOURCE_RECRUITER,
    RoleCriterion,
)
from .spec_normalizer import DerivedCriterion, derive_criteria, normalize_spec


# ---------------------------------------------------------------------------
# Snapshot from workspace
# ---------------------------------------------------------------------------


def _active_org_criteria(db: Session, organization_id: int) -> list[OrganizationCriterion]:
    return (
        db.query(OrganizationCriterion)
        .filter(
            OrganizationCriterion.organization_id == organization_id,
            OrganizationCriterion.deleted_at.is_(None),
        )
        .order_by(OrganizationCriterion.ordering, OrganizationCriterion.id)
        .all()
    )


def _active_recruiter_role_criteria(db: Session, role: Role) -> list[RoleCriterion]:
    """Query directly so we don't depend on relationship freshness within a
    session that just added/deleted rows."""
    if role.id is None:
        return []
    return (
        db.query(RoleCriterion)
        .filter(
            RoleCriterion.role_id == role.id,
            RoleCriterion.deleted_at.is_(None),
            RoleCriterion.source != CRITERION_SOURCE_DERIVED,
        )
        .order_by(RoleCriterion.ordering, RoleCriterion.id)
        .all()
    )


def snapshot_workspace_criteria(db: Session, role: Role) -> None:
    """Copy every active workspace criterion into ``role_criteria`` for the
    given role. Idempotent: rows already linked via ``org_criterion_id`` are
    overwritten. Used at role create + Workable import time and as the seed
    for "Reset to defaults".

    This does NOT touch ``derived_from_spec`` rows or role-only additions.
    """
    if role.organization_id is None:
        return
    org_criteria = _active_org_criteria(db, role.organization_id)
    existing_by_org = {
        c.org_criterion_id: c
        for c in _active_recruiter_role_criteria(db, role)
        if c.org_criterion_id is not None
    }
    for ordering, oc in enumerate(org_criteria):
        existing = existing_by_org.get(oc.id)
        if existing is None:
            db.add(
                RoleCriterion(
                    role_id=role.id,
                    source=CRITERION_SOURCE_RECRUITER,
                    ordering=ordering,
                    weight=float(oc.weight or 1.0),
                    must_have=(oc.bucket == BUCKET_MUST),
                    bucket=oc.bucket,
                    org_criterion_id=oc.id,
                    customized_at=None,
                    text=oc.text,
                )
            )
        else:
            # Preserve recruiter customizations; otherwise re-sync.
            if existing.customized_at is None:
                existing.text = oc.text
                existing.bucket = oc.bucket
                existing.must_have = oc.bucket == BUCKET_MUST
                existing.weight = float(oc.weight or 1.0)
                existing.ordering = ordering


def sync_role_with_workspace(db: Session, role: Role) -> None:
    """Re-apply workspace text + bucket to non-customized, non-suppressed role
    chips, add any newly-introduced workspace chips, and clear
    ``org_criterion_id`` on chips whose workspace counterpart is gone.
    """
    if role.organization_id is None:
        return
    org_criteria = _active_org_criteria(db, role.organization_id)
    org_by_id = {oc.id: oc for oc in org_criteria}
    suppressed = set(int(x) for x in (role.suppressed_org_criterion_ids or []))

    # 1. Update or detach existing workspace-derived role chips.
    for c in _active_recruiter_role_criteria(db, role):
        if c.org_criterion_id is None:
            continue  # role-only addition
        oc = org_by_id.get(c.org_criterion_id)
        if oc is None:
            # Workspace deleted this chip — keep the role's copy as a
            # role-only addition.
            c.org_criterion_id = None
            continue
        if c.customized_at is not None:
            continue  # recruiter edited; don't overwrite
        c.text = oc.text
        c.bucket = oc.bucket
        c.must_have = oc.bucket == BUCKET_MUST
        c.weight = float(oc.weight or 1.0)

    # 2. Add any workspace chips not yet linked + not suppressed.
    linked_ids = {
        c.org_criterion_id
        for c in _active_recruiter_role_criteria(db, role)
        if c.org_criterion_id is not None
    }
    next_ordering = max(
        (c.ordering for c in _active_recruiter_role_criteria(db, role)),
        default=-1,
    ) + 1
    for oc in org_criteria:
        if oc.id in linked_ids or oc.id in suppressed:
            continue
        db.add(
            RoleCriterion(
                role_id=role.id,
                source=CRITERION_SOURCE_RECRUITER,
                ordering=next_ordering,
                weight=float(oc.weight or 1.0),
                must_have=(oc.bucket == BUCKET_MUST),
                bucket=oc.bucket,
                org_criterion_id=oc.id,
                customized_at=None,
                text=oc.text,
            )
        )
        next_ordering += 1


def reset_role_to_workspace(db: Session, role: Role) -> None:
    """Hard-delete every recruiter-source role chip and re-snapshot workspace.
    Suppressions are cleared so all workspace chips return.

    ``derived_from_spec`` chips are untouched.
    """
    for c in _active_recruiter_role_criteria(db, role):
        db.delete(c)
    role.suppressed_org_criterion_ids = []
    db.flush()
    snapshot_workspace_criteria(db, role)


# ---------------------------------------------------------------------------
# Render helpers — used everywhere the agent prompts / scoring / interview
# helpers / MCP payloads need a text view of the chip state.
# ---------------------------------------------------------------------------

_BUCKET_LABEL = {
    BUCKET_MUST: "MUST HAVE",
    BUCKET_PREFERRED: "PREFERRED",
    BUCKET_CONSTRAINT: "CONSTRAINTS",
}


def _bucketed_text(items: Iterable[tuple[str, str]]) -> str:
    """``items`` = sequence of ``(bucket, text)`` tuples. Returns a stable
    multi-line string: one section per bucket, only when populated, in the
    canonical order ``must -> preferred -> constraint``."""
    by_bucket: dict[str, list[str]] = {b: [] for b in (BUCKET_MUST, BUCKET_PREFERRED, BUCKET_CONSTRAINT)}
    for bucket, text in items:
        text = (text or "").strip()
        if not text:
            continue
        by_bucket.setdefault(bucket, []).append(text)
    sections: list[str] = []
    for bucket in (BUCKET_MUST, BUCKET_PREFERRED, BUCKET_CONSTRAINT):
        rows = by_bucket.get(bucket) or []
        if not rows:
            continue
        sections.append(_BUCKET_LABEL[bucket] + ":\n" + "\n".join(f"- {r}" for r in rows))
    return "\n\n".join(sections)


def render_role_intent_items(items: Iterable[tuple[str, str]]) -> str:
    """Render a directly queried role-criterion snapshot canonically."""
    return _bucketed_text(items)


def render_role_intent_block(role: Role) -> str:
    """Bucketed text view of a role's recruiter chips. Empty string when
    the role has none. Used by every reader that previously read
    ``role.additional_requirements`` directly."""
    if role is None:
        return ""
    chips = [
        c for c in (role.criteria or [])
        if c.deleted_at is None and c.source != CRITERION_SOURCE_DERIVED
    ]
    items = [
        (c.bucket, c.text)
        for c in sorted(chips, key=lambda c: c.ordering)
    ]
    return render_role_intent_items(items)


def render_role_intent_lines(role: Role) -> list[str]:
    """Flat list of recruiter chip texts (one per chip) preserving order.
    Used by readers that want individual bullets rather than the bucketed
    text block."""
    if role is None:
        return []
    chips = [
        c for c in (role.criteria or [])
        if c.deleted_at is None and c.source != CRITERION_SOURCE_DERIVED
    ]
    return [c.text.strip() for c in sorted(chips, key=lambda c: c.ordering) if (c.text or "").strip()]


def render_org_intent_block(db: Session, organization) -> str:
    """Bucketed text view of a workspace's chips. Empty when the workspace
    has none. Used by readers that previously joined the legacy
    ``organization.default_role_requirements`` JSON list into a text blob."""
    if organization is None or organization.id is None:
        return ""
    chips = _active_org_criteria(db, organization.id)
    items = [(c.bucket, c.text) for c in chips]
    return _bucketed_text(items)


def render_org_intent_lines(db: Session, organization) -> list[str]:
    """Flat list of workspace chip texts. Replaces direct reads of the
    legacy ``organization.default_role_requirements`` JSON column."""
    if organization is None or organization.id is None:
        return []
    chips = _active_org_criteria(db, organization.id)
    return [c.text.strip() for c in chips if (c.text or "").strip()]


# ---------------------------------------------------------------------------
# Spec-derived sync (unchanged, plus bucket default)
# ---------------------------------------------------------------------------


def _replace_derived_criteria(
    db: Session,
    role: Role,
    *,
    criteria: list[DerivedCriterion],
) -> None:
    existing = [
        c for c in (role.criteria or [])
        if c.source == CRITERION_SOURCE_DERIVED and c.deleted_at is None
    ]
    for criterion in existing:
        db.delete(criterion)
    for ordering, item in enumerate(criteria):
        db.add(
            RoleCriterion(
                role_id=role.id,
                source=CRITERION_SOURCE_DERIVED,
                ordering=ordering,
                weight=1.0,
                # The deriver classifies each line; must_have stays in lockstep
                # with bucket=="must" (the model keeps the two synced too).
                must_have=item.must_have,
                bucket=item.bucket,
                text=item.text,
            )
        )


def sync_derived_criteria(db: Session, role: Role) -> None:
    """Re-derive ``derived_from_spec`` criteria from the Requirements section
    of the uploaded job spec. Falls back to no derived criteria when the spec
    has no recognizable Requirements heading.

    Each derived criterion is bucketed (must / preferred / constraint) by the
    spec_normalizer heuristics rather than blindly defaulting to preferred.
    """
    spec = normalize_spec(role.job_spec_text)
    criteria = derive_criteria(spec.requirements)
    _replace_derived_criteria(db, role, criteria=criteria)


def sync_all_criteria(db: Session, role: Role) -> None:
    """On role create / Workable import: snapshot the workspace chip set
    into ``role_criteria`` (with ``org_criterion_id`` provenance) and
    refresh spec-derived chips from the job spec text."""
    snapshot_workspace_criteria(db, role)
    sync_derived_criteria(db, role)
    db.flush()
