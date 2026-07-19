"""Calibration training-data extractor (RALPH 3.2).

Reads ``cv_match_overrides`` joined with ``candidate_applications`` and
emits one labelled record per override event. The recalibration job
(RALPH 3.3) groups records by role family and feeds them to
``fit_calibrator`` for each (role_family, dimension) pair.

We avoid pandas to keep the cv_matching package numpy-free; records
are plain dataclasses. A caller that wants pandas can convert the
list themselves with ``pandas.DataFrame([asdict(r) for r in rows])``.

Role-family resolution prefers the exact ``archetype_id`` persisted in the
score details, matching the key runtime calibration uses. Legacy rows fall
back to a slug of ``Role.name``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
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


def _role_family_for(details: dict, role, mapper: Callable[[str | None], str]) -> str:
    """Use the persisted runtime key; fall back to the legacy role-name slug."""
    return str(details.get("archetype_id") or "").strip() or mapper(
        getattr(role, "name", None)
    )


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
    include_outcomes: bool = True,
) -> list[CalibrationRecord]:
    """Read training records from the DB and emit calibration records.

    Two label sources:
      * recruiter **overrides** of the LLM recommendation (``cv_match_overrides``)
      * realized **outcomes** (``include_outcomes``): ``advanced`` candidates
        with a score whose Workable result is decided — offer/hired = positive,
        reject/disqualify = negative. This is the stronger ground-truth label,
        so when a candidate has both, the outcome record wins.

    ``role_family_mapper`` maps a role title to a stable role_family.
    Defaults to slugify. ``role_family_filter`` (when set) restricts
    output to that role_family only. ``since`` filters by event time.

    Returns ``[]`` when the DB is unavailable (lightweight test mode).
    """
    try:
        from ...models.candidate_application import CandidateApplication
        from ...models.cv_match_override import CvMatchOverride
        from ...models.role import Role
        from ...platform.database import SessionLocal
    except Exception as exc:
        logger.debug(
            "Calibration extractor: DB unavailable; returning empty result "
            "error_type=%s",
            type(exc).__name__,
        )
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

        override_records: list[CalibrationRecord] = []
        for override, app, role in q.all():
            details = getattr(app, "cv_match_details", {}) or {}
            role_family = _role_family_for(details, role, mapper)
            if role_family_filter and role_family != role_family_filter:
                continue

            raw_scores = _extract_raw_scores(details, override)
            if not raw_scores:
                continue

            action = _classify_recruiter_action(
                override.original_recommendation,
                override.override_recommendation,
            )
            override_records.append(
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

        if not include_outcomes:
            return override_records

        # Realized-outcome records are the stronger label: when a candidate has
        # both an override and a decided outcome, keep the outcome and drop the
        # override to avoid double-counting.
        outcome_records = _extract_outcome_records(
            session,
            candidate_application_cls=CandidateApplication,
            role_cls=Role,
            mapper=mapper,
            role_family_filter=role_family_filter,
            since=since,
        )
        covered = {r.application_id for r in outcome_records}
        deduped_overrides = [r for r in override_records if r.application_id not in covered]
        return outcome_records + deduped_overrides
    finally:
        session.close()


# Workable terminal stages that mean "chosen" (positive label), independent of
# application_outcome (offer keeps outcome=open until the candidate accepts).
_POSITIVE_TERMINAL_STAGES = {"offer", "offer_extended", "offer_accepted", "hired"}


def _norm_stage(value: str | None) -> str:
    return (value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _outcome_action(app) -> _RecruiterAction | None:
    """Derive the binary advance/reject label from a candidate's realized
    outcome. Returns None when the outcome isn't a usable label yet (e.g. an
    advanced-by-handback candidate still awaiting a downstream result)."""
    outcome = (getattr(app, "application_outcome", None) or "").lower()
    if outcome == "hired":
        return "advance"
    if outcome == "rejected":
        return "reject"
    if _norm_stage(getattr(app, "workable_stage", None)) in _POSITIVE_TERMINAL_STAGES:
        return "advance"
    # ``workable_disqualified`` is only a reject signal once the clearer
    # outcome/stage checks above have ruled out an advance — otherwise a
    # candidate disqualified for a non-reject reason (e.g. role filled, or an
    # administrative close after reaching offer/hired) is mislabeled a reject,
    # poisoning the calibration ground truth.
    if getattr(app, "workable_disqualified", None) is True:
        return "reject"
    return None


def _extract_outcome_records(
    session,
    *,
    candidate_application_cls,
    role_cls,
    mapper: Callable[[str | None], str],
    role_family_filter: str | None,
    since: datetime | None,
) -> list[CalibrationRecord]:
    """Emit calibration records from realized hiring outcomes — the ground truth
    the loop is meant to learn from. Only `advanced` (decided) candidates that
    already have a score (cv_match_details → raw_scores) qualify."""
    from sqlalchemy import or_

    q = (
        session.query(candidate_application_cls, role_cls)
        .outerjoin(role_cls, role_cls.id == candidate_application_cls.role_id)
        .filter(
            candidate_application_cls.pipeline_stage == "advanced",
            candidate_application_cls.cv_match_score.isnot(None),
            candidate_application_cls.deleted_at.is_(None),
        )
    )
    if since is not None:
        q = q.filter(
            or_(
                candidate_application_cls.application_outcome_updated_at >= since,
                candidate_application_cls.pipeline_stage_updated_at >= since,
            )
        )

    records: list[CalibrationRecord] = []
    for app, role in q.all():
        action = _outcome_action(app)
        if action is None:
            continue
        details = getattr(app, "cv_match_details", {}) or {}
        role_family = _role_family_for(details, role, mapper)
        if role_family_filter and role_family != role_family_filter:
            continue
        raw_scores = _extract_raw_scores(details, None)
        if not raw_scores:
            continue
        records.append(
            CalibrationRecord(
                application_id=int(app.id),
                role_family=role_family,
                raw_scores=raw_scores,
                original_recommendation="",
                recruiter_action=action,
                created_at=(
                    app.application_outcome_updated_at
                    or app.pipeline_stage_updated_at
                    or app.created_at
                ),
                notes="realized_outcome",
            )
        )
    return records


def _extract_raw_scores(details: dict, override) -> dict[str, float]:
    """Pull raw dimension scores out of cv_match_details (+ optional override row)."""
    scores: dict[str, float] = {}
    role_fit = details.get("role_fit_score")
    if role_fit is not None:
        scores["role_fit"] = float(role_fit)
    elif override is not None and override.original_score is not None:
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
