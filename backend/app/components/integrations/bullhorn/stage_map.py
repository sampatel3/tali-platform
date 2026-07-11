"""AtsStageMap resolution for Bullhorn — remote free-text status → Taali stage.

Bullhorn ``JobSubmission.status`` values are per-org free text, so the mapping
to a Taali pipeline stage (and whether that status means "rejected") CANNOT be
hardcoded. Each row of :class:`AtsStageMap` maps one ``remote_status`` for
``ats="bullhorn"`` in one org to a ``taali_stage`` + ``is_reject`` flag.

Policy (from the build plan §6 and the fact sheet):
* A status WITH a mapping row resolves to that stage/outcome.
* A status WITHOUT a mapping row is **needs-mapping** — surfaced, NEVER guessed.
  The caller stores the raw ``bullhorn_status`` on the application so the row is
  still visible, and parks the application at the top of the Taali funnel
  (``applied``) until a recruiter maps the status.

``seed_stage_map_from_categorization`` runs once at connect time: it pre-creates
rows for the three Bullhorn *categorization settings*
(``interviewScheduledJobResponseStatus`` / ``confirmedJobResponseStatus`` /
``rejectedJobResponseStatus``) whose values Bullhorn itself designates as the
interview / placed / rejected statuses, so the common cases are mapped out of
the box. Everything else stays needs-mapping until a recruiter maps it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ....models.ats_stage_map import AtsStageMap
from ....models.organization import Organization

logger = logging.getLogger(__name__)

ATS_BULLHORN = "bullhorn"

# The three Bullhorn categorization settings and the Taali stage/outcome each
# maps to. Bullhorn designates one free-text status per setting as the
# interview / confirmed(placed) / rejected status for the org, so these give us
# a safe, non-guessed default mapping for the most consequential statuses.
_CATEGORIZATION_DEFAULTS: tuple[tuple[str, str, bool], ...] = (
    # setting name, taali_stage, is_reject
    ("interviewScheduledJobResponseStatus", "advanced", False),
    ("confirmedJobResponseStatus", "advanced", False),
    ("rejectedJobResponseStatus", "review", True),
)


# The categorization setting whose value Bullhorn designates as the org's
# placed/hired status. Persisted onto ``org.bullhorn_config`` at seed time so the
# advance write-back can exclude it (see write_back._remote_status_for_advance).
_CONFIRMED_PLACED_SETTING = "confirmedJobResponseStatus"


@dataclass(frozen=True)
class StageMapping:
    """A resolved mapping for one remote status."""

    taali_stage: str
    is_reject: bool


def _remember_confirmed_placed_status(
    org: Organization, categorization: dict[str, str | None]
) -> None:
    """Persist the org's confirmed/placed status onto ``org.bullhorn_config``.

    Reassigns the JSON dict (SQLAlchemy tracks JSON mutation by identity) and
    preserves any other config keys. No-op when the setting is absent. Idempotent:
    re-seeding just re-writes the same value.
    """
    status = (categorization.get(_CONFIRMED_PLACED_SETTING) or "").strip()
    if not status:
        return
    config = dict(org.bullhorn_config) if isinstance(org.bullhorn_config, dict) else {}
    if config.get(_CONFIRMED_PLACED_SETTING) == status:
        return
    config[_CONFIRMED_PLACED_SETTING] = status
    org.bullhorn_config = config


def resolve_stage(db: Session, org: Organization, remote_status: str | None) -> StageMapping | None:
    """Resolve a Bullhorn status to a Taali stage, or ``None`` if unmapped.

    ``None`` means **needs-mapping** — the caller must NOT guess a stage; it
    keeps the raw status visible and leaves the application at the funnel top.
    An empty/blank status is treated as unmapped (there's nothing to map).
    """
    status = (remote_status or "").strip()
    if not status:
        return None
    row = (
        db.query(AtsStageMap)
        .filter(
            AtsStageMap.org_id == org.id,
            AtsStageMap.ats == ATS_BULLHORN,
            AtsStageMap.remote_status == status,
        )
        .first()
    )
    if row is None:
        return None
    return StageMapping(taali_stage=row.taali_stage, is_reject=bool(row.is_reject))


def is_needs_mapping(db: Session, org: Organization, remote_status: str | None) -> bool:
    """True when this Bullhorn status has no mapping row (surface, don't guess)."""
    status = (remote_status or "").strip()
    if not status:
        return False
    return resolve_stage(db, org, status) is None


def unmapped_statuses(db: Session, org: Organization) -> list[str]:
    """Distinct Bullhorn statuses seen on this org's applications with no map row.

    Drives the connect/status surface's "needs mapping" list. Reads the raw
    ``bullhorn_status`` the importer stored on each application and subtracts the
    statuses that already have a mapping row.
    """
    from ....models.candidate_application import CandidateApplication

    seen = {
        (s or "").strip()
        for (s,) in db.query(CandidateApplication.bullhorn_status)
        .filter(
            CandidateApplication.organization_id == org.id,
            CandidateApplication.bullhorn_status.isnot(None),
        )
        .distinct()
        if (s or "").strip()
    }
    if not seen:
        return []
    mapped = {
        (r or "").strip()
        for (r,) in db.query(AtsStageMap.remote_status).filter(
            AtsStageMap.org_id == org.id,
            AtsStageMap.ats == ATS_BULLHORN,
        )
    }
    return sorted(seen - mapped)


def seed_stage_map_from_categorization(
    db: Session,
    org: Organization,
    *,
    categorization: dict[str, str | None],
) -> int:
    """Pre-create AtsStageMap rows for the categorization-setting statuses.

    Idempotent: skips a status that already has a row (the unique constraint is
    ``(org_id, ats, remote_status)``), so re-connecting never duplicates or
    overwrites a recruiter's manual mapping. Returns the number of rows created.
    Does NOT commit — the caller owns the transaction.

    Also records the org's confirmed/placed status on ``org.bullhorn_config`` so
    write-back can EXCLUDE it as an advance target: both the interviewScheduled
    AND the confirmed status seed to ``advanced`` (correct for reads — a placed
    candidate is past hand-off), but a mere advance must never write the placed
    status back to Bullhorn (that fires placement/billing workflows). The stored
    value is the durable discriminator between the two otherwise-identical rows.
    """
    _remember_confirmed_placed_status(org, categorization)
    created = 0
    for setting, taali_stage, is_reject in _CATEGORIZATION_DEFAULTS:
        status = (categorization.get(setting) or "").strip()
        if not status:
            continue
        exists = (
            db.query(AtsStageMap.id)
            .filter(
                AtsStageMap.org_id == org.id,
                AtsStageMap.ats == ATS_BULLHORN,
                AtsStageMap.remote_status == status,
            )
            .first()
        )
        if exists:
            continue
        db.add(
            AtsStageMap(
                org_id=org.id,
                ats=ATS_BULLHORN,
                remote_status=status,
                taali_stage=taali_stage,
                is_reject=is_reject,
            )
        )
        created += 1
    if created:
        logger.info(
            "Seeded %d Bullhorn stage-map rows from categorization settings for org_id=%s",
            created,
            org.id,
        )
    return created
