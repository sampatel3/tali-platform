"""Calibration training-data extractor (RALPH 3.2).

Reads ``cv_match_overrides`` joined with ``candidate_applications`` and
emits one labelled record per override event. The recalibration job
(RALPH 3.3) groups records by role family and feeds them to
``fit_calibrator`` for each (role_family, dimension) pair.

We avoid pandas to keep the cv_matching package numpy-free; records
are plain dataclasses. A caller that wants pandas can convert the
list themselves with ``pandas.DataFrame([asdict(r) for r in rows])``.

Role-family resolution: the override table doesn't directly carry
role_family, so the extractor delegates that mapping to a callable
the caller supplies. The default mapper slugifies the role title.
That's a reasonable starting point until role-archetype tagging is
formalised — see the v4 roadmap for the formalisation plan.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

logger = logging.getLogger("taali.cv_match.calibrators.extractor")


_RecruiterAction = str  # "advance" | "reject" | "override_advance" | "override_reject"


@dataclass
class CalibrationRecord:
    """One labelled (raw, advance) record for calibrator training.

    ``raw_scores`` keys are dimension names: ``role_fit``,
    ``cv_fit``, ``skills_coverage``, ``skills_depth``, ...

    ``recruiter_action`` is a categorical with four values; we collapse
    to a binary "advanced" label for calibrator training in
    ``RecruiterAction.advanced(...)``.
    """

    application_id: int
    role_family: str
    raw_scores: dict[str, float]
    original_recommendation: str  # the LLM's recommendation
    recruiter_action: _RecruiterAction
    created_at: datetime
    notes: str = ""

    @property
    def advanced(self) -> bool:
        """Binary advance label for calibrator training.

        ``advance`` and ``override_advance`` count as advanced.
        ``reject`` and ``override_reject`` count as not advanced.
        """
        return self.recruiter_action in ("advance", "override_advance")


def _default_role_family_mapper(role_title: str | None) -> str:
    """Slugify a role title into a stable role_family identifier.

    Lowercase, alphanumeric + underscore, collapses runs of separators.
    """
    if not role_title:
        return "unknown"
    s = role_title.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "unknown"


def _classify_recruiter_action(
    original_recommendation: str | None,
    override_recommendation: str | None,
) -> _RecruiterAction:
    """Map (original, override) → categorical recruiter_action.

    The override row only exists when a recruiter overrode the LLM,
    so override_recommendation is always non-null at the SQL level.
    """
    orig = (original_recommendation or "").lower()
    ovr = (override_recommendation or "").lower()
    advance_set = {"advance", "yes", "strong_yes"}
    if ovr in advance_set:
        return "override_advance" if orig not in advance_set else "advance"
    return "override_reject" if orig in advance_set else "reject"


def extract_records(
    *,
    role_family_mapper: Callable[[str | None], str] | None = None,
    role_family_filter: str | None = None,
    since: datetime | None = None,
) -> list[CalibrationRecord]:
    """Read overrides from the DB and emit calibration records.

    ``role_family_mapper`` maps a role title to a stable role_family.
    Defaults to slugify. ``role_family_filter`` (when set) restricts
    output to that role_family only. ``since`` filters by override
    ``created_at``.

    Returns ``[]`` when the DB is unavailable (lightweight test mode).
    """
    try:
        from ..models.candidate_application import CandidateApplication
        from ..models.cv_match_override import CvMatchOverride
        from ..models.role import Role
        from ..platform.database import SessionLocal
    except Exception as exc:
        logger.debug("Calibration extractor: DB unavailable, returning []: %s", exc)
        return []

    mapper = role_family_mapper or _default_role_family_mapper

    session = SessionLocal()
    try:
        q = (
            session.query(CvMatchOverride, CandidateApplication, Role)
            .join(
                CandidateApplication,
                CandidateApplication.id == CvMatchOverride.application_id,
            )
            .outerjoin(Role, Role.id == CandidateApplication.role_id)
        )
        if since is not None:
            q = q.filter(CvMatchOverride.created_at >= since)

        records: list[CalibrationRecord] = []
        for override, app, role in q.all():
            role_family = mapper(getattr(role, "title", None))
            if role_family_filter and role_family != role_family_filter:
                continue

            details = getattr(app, "cv_match_details", {}) or {}
            raw_scores = _extract_raw_scores(details, override)
            if not raw_scores:
                continue

            action = _classify_recruiter_action(
                override.original_recommendation,
                override.override_recommendation,
            )
            records.append(
                CalibrationRecord(
                    application_id=int(override.application_id),
                    role_family=role_family,
                    raw_scores=raw_scores,
                    original_recommendation=override.original_recommendation or "",
                    recruiter_action=action,
                    created_at=override.created_at,
                    notes=override.recruiter_notes or "",
                )
            )
        return records
    finally:
        session.close()


def _extract_raw_scores(details: dict, override) -> dict[str, float]:
    """Pull raw dimension scores out of cv_match_details + override row."""
    scores: dict[str, float] = {}
    role_fit = details.get("role_fit_score")
    if role_fit is not None:
        scores["role_fit"] = float(role_fit)
    elif override.original_score is not None:
        scores["role_fit"] = float(override.original_score)

    if details.get("cv_fit_score") is not None:
        scores["cv_fit"] = float(details["cv_fit_score"])
    if details.get("requirements_match_score") is not None:
        scores["requirements_match"] = float(details["requirements_match_score"])

    dim_scores = details.get("dimension_scores") or {}
    for dim_name in (
        "skills_coverage",
        "skills_depth",
        "title_trajectory",
        "seniority_alignment",
        "industry_match",
        "tenure_pattern",
    ):
        if dim_scores.get(dim_name) is not None:
            scores[dim_name] = float(dim_scores[dim_name])

    return scores


def group_by_role_family(
    records: list[CalibrationRecord],
) -> dict[str, list[CalibrationRecord]]:
    out: dict[str, list[CalibrationRecord]] = {}
    for r in records:
        out.setdefault(r.role_family, []).append(r)
    return out


__all__ = [
    "CalibrationRecord",
    "extract_records",
    "group_by_role_family",
]
