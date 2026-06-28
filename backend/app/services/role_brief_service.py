"""Requisition: hiring-brief service (create / update / submit / materialize).

The intake agent and the recruiter both edit a RoleBrief through ``update_brief_fields``;
``materialize_brief_to_role`` turns a finished brief into a real role (name +
description now; criteria + knockouts in the follow-up). Mutators flush but do
NOT commit — the caller owns the transaction.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.job_page import JOB_PAGE_STATUS_OPEN, JobPage
from ..models.org_criterion import BUCKET_CONSTRAINT, BUCKET_MUST, BUCKET_PREFERRED
from ..models.role import JOB_STATUS_DRAFT, Role
from ..models.role_criterion import CRITERION_SOURCE_RECRUITER, RoleCriterion
from ..models.role_brief import (
    BRIEF_SOURCES,
    BRIEF_STATUS_APPLIED,
    BRIEF_STATUS_SUBMITTED,
    RoleBrief,
)

# Fields the agent / recruiter may set on a brief.
_EDITABLE_FIELDS = frozenset(
    {
        "source_kind",
        "title",
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
        "messages",
        "raw_input",
        "agent_state",
        "completeness",
    }
)


# --------------------------------------------------------------------------- #
# Requisition ref code — the Workable bridge match key
# --------------------------------------------------------------------------- #
# Short, human-friendly, unambiguous (Crockford-ish alphabet: no 0/1/O/I/L/U).
# The recruiter pastes the rendered spec — which carries a ``Taali ref: TAL-XXXXX``
# line — into the Workable job description; the read-sync scans the imported
# description for this pattern to link the synced role back to its requisition.
REF_CODE_PREFIX = "TAL-"
_REF_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
_REF_CODE_LEN = 5
# Matches a stamped code anywhere in free text (e.g. a pasted JD). Anchored on the
# prefix + the exact alphabet so it never trips on unrelated "TAL-" strings.
REF_CODE_RE = re.compile(
    r"\b" + re.escape(REF_CODE_PREFIX) + f"[{_REF_CODE_ALPHABET}]{{{_REF_CODE_LEN}}}" + r"\b"
)


def _new_ref_code() -> str:
    body = "".join(secrets.choice(_REF_CODE_ALPHABET) for _ in range(_REF_CODE_LEN))
    return f"{REF_CODE_PREFIX}{body}"


def generate_ref_code(db: Session) -> str:
    """A globally-unique requisition ref code (retries on the rare collision)."""
    for _ in range(12):
        code = _new_ref_code()
        exists = db.query(RoleBrief.id).filter(RoleBrief.ref_code == code).first()
        if not exists:
            return code
    # Astronomically unlikely; widen with a 6th char rather than fail a publish.
    return _new_ref_code() + secrets.choice(_REF_CODE_ALPHABET)


def ensure_ref_code(db: Session, brief: RoleBrief) -> str:
    """Mint-once the brief's ref code and return it. Idempotent."""
    if not brief.ref_code:
        brief.ref_code = generate_ref_code(db)
        db.flush()
    return brief.ref_code


def find_ref_code(text: str | None) -> str | None:
    """Extract the first stamped Taali ref code from free text, or None."""
    if not text:
        return None
    m = REF_CODE_RE.search(text)
    return m.group(0) if m else None


def create_brief(
    db: Session,
    *,
    organization_id: int,
    created_by_user_id: int | None = None,
    source_kind: str | None = None,
) -> RoleBrief:
    if source_kind is not None and source_kind not in BRIEF_SOURCES:
        raise HTTPException(status_code=422, detail=f"Unsupported source_kind={source_kind!r}")
    brief = RoleBrief(
        organization_id=organization_id,
        created_by_user_id=created_by_user_id,
        source_kind=source_kind,
    )
    db.add(brief)
    db.flush()
    return brief


def update_brief_fields(db: Session, brief: RoleBrief, **fields) -> RoleBrief:
    """Set whitelisted brief fields (ignores unknown keys). Used by the intake
    agent's incremental fills and by recruiter edits."""
    if brief.status == BRIEF_STATUS_APPLIED:
        raise HTTPException(status_code=409, detail="Brief already applied to a role")
    if "source_kind" in fields and fields["source_kind"] not in (None, *BRIEF_SOURCES):
        raise HTTPException(
            status_code=422, detail=f"Unsupported source_kind={fields['source_kind']!r}"
        )
    for key, value in fields.items():
        if key in _EDITABLE_FIELDS:
            setattr(brief, key, value)
    db.flush()
    return brief


def submit_brief(db: Session, brief: RoleBrief) -> RoleBrief:
    """Hiring manager finished the intake; ready for recruiter review."""
    if brief.status != BRIEF_STATUS_APPLIED:
        brief.status = BRIEF_STATUS_SUBMITTED
    db.flush()
    return brief


def _criterion_text(item) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("text") or item.get("label") or "").strip()
    return str(item).strip()


def _materialize_criteria(db: Session, brief: RoleBrief, role: Role) -> None:
    """Create role_criterion rows from the brief's must_haves / preferred /
    dealbreakers (-> must / preferred / constraint buckets) so the published role
    is immediately scoreable. Idempotent: skips if the role already has criteria,
    so re-publishing never duplicates. (Dealbreakers also become knockout
    questions once screening_questions reaches prod.)"""
    has_any = (
        db.query(RoleCriterion.id)
        .filter(RoleCriterion.role_id == role.id, RoleCriterion.deleted_at.is_(None))
        .first()
    )
    if has_any:
        return
    ordering = 0
    for items, bucket, must in (
        (brief.must_haves, BUCKET_MUST, True),
        (brief.preferred, BUCKET_PREFERRED, False),
        (brief.dealbreakers, BUCKET_CONSTRAINT, False),
    ):
        for item in items or []:
            text = _criterion_text(item)
            if not text:
                continue
            db.add(
                RoleCriterion(
                    role_id=role.id,
                    text=text,
                    bucket=bucket,
                    must_have=must,
                    source=CRITERION_SOURCE_RECRUITER,
                    ordering=ordering,
                )
            )
            ordering += 1
    db.flush()


def materialize_brief_to_role(
    db: Session,
    brief: RoleBrief,
    *,
    mark_applied: bool = True,
    job_status: str | None = None,
) -> Role:
    """Create (or update) the role this brief describes. Name + description +
    criteria.

    ``mark_applied`` (default True) flips the brief to APPLIED, locking it from
    further edits — the "commit fully to a role" path. Publish passes
    ``mark_applied=False`` so the brief stays editable for a re-publish while
    still standing up an INACTIVE job (``job_status=draft``).

    ``job_status`` is set on a freshly-created role, and on an existing one only
    while it has no Workable link and isn't already live/closed — so a re-publish
    never demotes a job the Workable bridge has since flipped to ``open`` or that
    the recruiter has marked filled.
    """
    if brief.role_id:
        role = (
            db.query(Role)
            .filter(Role.id == brief.role_id, Role.organization_id == brief.organization_id)
            .first()
        )
        if role is None:
            raise HTTPException(status_code=404, detail="Linked role not found")
        if (
            job_status is not None
            and not role.workable_job_id
            and role.job_status in (None, JOB_STATUS_DRAFT)
        ):
            role.job_status = job_status
    else:
        role = Role(
            organization_id=brief.organization_id,
            name=(brief.title or "Untitled role"),
            source="requisition",
            job_status=job_status,
        )
        db.add(role)
        db.flush()
        brief.role_id = role.id
    if brief.title:
        role.name = brief.title
    if brief.summary:
        role.description = brief.summary
    _materialize_criteria(db, brief, role)
    if mark_applied:
        brief.status = BRIEF_STATUS_APPLIED
    db.flush()
    return role


def requisition_spec_for_role(
    db: Session, *, organization_id: int, role_id: int
) -> dict | None:
    """The linked requisition's structured spec for a role's Job Spec tab, or
    None when the role didn't originate from / wasn't linked to a requisition.

    Read-only display data: the rich hiring brief (must-haves / dealbreakers /
    success profile / weighted priorities / comp / location / client /
    responsibilities) that the raw ``job_spec_text`` blob doesn't capture. This
    is the authenticated recruiter Jobs surface, so ``client_name`` is included
    for context; the consultancy ``client_rate`` / margin are deliberately NOT.
    """
    brief = (
        db.query(RoleBrief)
        .filter(
            RoleBrief.organization_id == organization_id,
            RoleBrief.role_id == role_id,
        )
        .order_by(RoleBrief.id.desc())
        .first()
    )
    if brief is None:
        return None
    custom = brief.custom_fields if isinstance(brief.custom_fields, dict) else {}
    return {
        "brief_id": brief.id,
        "ref_code": brief.ref_code,
        "status": brief.status,
        "completeness": int(brief.completeness or 0),
        "title": brief.title,
        "summary": brief.summary,
        "department": brief.department,
        "location_city": brief.location_city,
        "location_country": brief.location_country,
        "workplace_type": brief.workplace_type,
        "employment_type": brief.employment_type,
        "seniority": brief.seniority,
        "salary_min": brief.salary_min,
        "salary_max": brief.salary_max,
        "salary_currency": brief.salary_currency,
        "salary_period": brief.salary_period,
        "target_start": brief.target_start,
        "must_haves": brief.must_haves or [],
        "preferred": brief.preferred or [],
        "dealbreakers": brief.dealbreakers or [],
        "responsibilities": custom.get("responsibilities") or [],
        "success_profile": brief.success_profile,
        "priorities": brief.priorities or [],
        "tradeoffs": brief.tradeoffs or [],
        "sourcing_signals": brief.sourcing_signals or [],
        "assessment_focus": brief.assessment_focus or [],
        "process": brief.process,
        "evp": brief.evp,
        "client_name": brief.client.name if brief.client else None,
    }


def role_client_map(
    db: Session, *, organization_id: int, role_ids: list[int]
) -> dict[int, dict]:
    """``{role_id: {"client_id", "client_name"}}`` for the given roles, in ONE
    query (no N+1) — for the Jobs list's Client column + filter. A role's client
    lives on its linked requisition brief; roles with no brief (or no client) are
    simply absent from the map. Imports ``Client`` lazily to avoid a cycle."""
    if not role_ids:
        return {}
    from ..models.client import Client

    rows = (
        db.query(RoleBrief.role_id, Client.id, Client.name)
        .join(Client, Client.id == RoleBrief.client_id)
        .filter(
            RoleBrief.organization_id == organization_id,
            RoleBrief.role_id.in_(role_ids),
        )
        .all()
    )
    # Newest brief wins if a role somehow has more than one (id asc → later
    # overwrites), matching requisition_spec_for_role's id-desc preference.
    out: dict[int, dict] = {}
    for role_id, client_id, client_name in sorted(rows, key=lambda r: r[0] or 0):
        if role_id is not None:
            out[int(role_id)] = {"client_id": client_id, "client_name": client_name}
    return out


def set_role_client(
    db: Session, *, organization_id: int, role_id: int, client_id: int | None
) -> dict | None:
    """Assign (or clear) the client a role belongs to, returning the new
    ``{"client_id", "client_name"}`` mapping (or None when cleared).

    A role's client lives on its requisition brief. Roles that came from a
    requisition already have one; Workable-imported / legacy roles created before
    client tagging existed have no brief, so we lazily stand up a minimal stub
    brief (status ``applied`` — it's already bound to a live role, not an
    in-flight intake) carrying just the client link. The brief's other fields
    stay empty; this is purely the consultancy attribution that the Jobs Client
    column / filter and per-client rollups read via ``role_client_map``.

    Flushes but does not commit — the caller owns the transaction.
    """
    from ..models.client import Client

    client = None
    if client_id is not None:
        client = (
            db.query(Client)
            .filter(Client.id == client_id, Client.organization_id == organization_id)
            .first()
        )
        if client is None:
            raise HTTPException(status_code=404, detail="Client not found")

    # Newest brief wins (mirrors requisition_spec_for_role / role_client_map).
    brief = (
        db.query(RoleBrief)
        .filter(
            RoleBrief.organization_id == organization_id,
            RoleBrief.role_id == role_id,
        )
        .order_by(RoleBrief.id.desc())
        .first()
    )
    if brief is None:
        if client_id is None:
            return None  # nothing to clear, nothing to create
        brief = RoleBrief(
            organization_id=organization_id,
            role_id=role_id,
            status=BRIEF_STATUS_APPLIED,
            source_kind="manual_attribution",
        )
        db.add(brief)

    brief.client_id = client_id
    db.flush()
    if client_id is None:
        return None
    return {"client_id": client_id, "client_name": client.name if client else None}


def _brief_location(brief: RoleBrief) -> str | None:
    """Public location string: "City, Country" from the brief's parts. Omits a
    missing half (so just a city or just a country still renders), returns None
    when neither is set."""
    parts = [
        (brief.location_city or "").strip(),
        (brief.location_country or "").strip(),
    ]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def publish_job_page(db: Session, brief: RoleBrief, *, jd_markdown: str) -> JobPage:
    """Create or refresh the PUBLIC job page for this brief and return it.

    Idempotent — one JobPage per ``brief_id``. The first publish mints an
    unguessable ``token`` (the public address); a re-publish reuses it and just
    refreshes the snapshot. Only PUBLIC-safe fields are copied — NEVER the
    consultancy ``client_id`` / ``client_rate`` / margin. The brief's own
    ``status`` is deliberately left untouched so it stays editable for re-publish
    (unlike ``materialize_brief_to_role``). Flushes but does not commit.
    """
    page = (
        db.query(JobPage)
        .filter(
            JobPage.brief_id == brief.id,
            JobPage.organization_id == brief.organization_id,
        )
        .first()
    )
    if page is None:
        page = JobPage(
            organization_id=brief.organization_id,
            brief_id=brief.id,
            token=secrets.token_urlsafe(8),
        )
        db.add(page)

    page.jd_markdown = jd_markdown
    page.title = brief.title
    page.location = _brief_location(brief)
    page.workplace_type = brief.workplace_type
    page.employment_type = brief.employment_type
    page.seniority = brief.seniority
    page.salary_min = brief.salary_min
    page.salary_max = brief.salary_max
    page.salary_currency = brief.salary_currency
    page.status = JOB_PAGE_STATUS_OPEN
    page.published_at = datetime.now(timezone.utc)
    db.flush()
    return page
