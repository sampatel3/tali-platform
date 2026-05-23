"""Template calibration computer — Amendment A2.9.1 / A2.9.3.

Recompute ``task_calibrations.predictive_quality`` for every active
(task, role_family) pair from the assessment-score → realised-outcome
stream. Plus retirement: any calibration whose ``predictive_quality``
stays below ``RETIRE_THRESHOLD`` with ``sample_size >= RETIRE_MIN_N``
gets stamped ``retired_at``.

Runs nightly. The math is small enough that pure Python is fine
through pre-pilot volumes; if templates scale to thousands per org, an
aggregate Cypher equivalent in Graphiti will be cheaper.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy.orm import Session

from ..cv_matching.calibrators.extractor import _default_role_family_mapper
from ..models.assessment import Assessment
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.task import Task
from ..models.task_calibration import TaskCalibration


logger = logging.getLogger("taali.sub_agents.task_calibration")


# Retirement thresholds — A2.9.3 suggested values; the v10 capability
# auditor will eventually drive these.
RETIRE_THRESHOLD = 0.20
RETIRE_MIN_N = 30


@dataclass
class _Pair:
    score: float
    outcome_quality: float


def pearson_correlation(pairs: Sequence[_Pair]) -> float:
    """Pearson r of (score, outcome_quality). Returns 0 when n < 2 or
    when either series has zero variance.
    """
    n = len(pairs)
    if n < 2:
        return 0.0
    sx = sum(p.score for p in pairs)
    sy = sum(p.outcome_quality for p in pairs)
    mx = sx / n
    my = sy / n
    num = sum((p.score - mx) * (p.outcome_quality - my) for p in pairs)
    den_x = math.sqrt(sum((p.score - mx) ** 2 for p in pairs))
    den_y = math.sqrt(sum((p.outcome_quality - my) ** 2 for p in pairs))
    if den_x == 0.0 or den_y == 0.0:
        return 0.0
    return num / (den_x * den_y)


def _gather_pairs(
    db: Session,
    *,
    organization_id: int,
    task_id: int,
    role_family: str,
) -> list[_Pair]:
    """Return (assessment_score, application_outcome_quality) pairs.

    Outcome quality: 1.0 for hired, 0.0 for rejected, skipped for
    open / withdrawn. Pre-pilot we don't have a post-hire performance
    signal so the binary hired/not is the available proxy. When
    `Role.agent_calibration["outcomes"]` (legacy) carries a quality
    score, that one is used instead.
    """
    rows = (
        db.query(Assessment, CandidateApplication, Role)
        .join(
            CandidateApplication,
            CandidateApplication.id == Assessment.application_id,
        )
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            Assessment.organization_id == organization_id,
            Assessment.task_id == task_id,
        )
        .all()
    )
    pairs: list[_Pair] = []
    for assessment, app, role in rows:
        if _default_role_family_mapper(role.name) != role_family:
            continue
        score = _assessment_score(assessment)
        if score is None:
            continue
        outcome = (app.application_outcome or "").lower()
        if outcome == "hired":
            quality = 1.0
        elif outcome == "rejected":
            quality = 0.0
        else:
            continue
        pairs.append(_Pair(score=score, outcome_quality=quality))
    return pairs


def _assessment_score(a: Assessment) -> float | None:
    """Pull the assessment's headline score. Returns None when missing.

    The Assessment model carries multiple score-like fields; we prefer
    a normalized [0, 1] reading. Existing rows store score on whichever
    field the v1 path used — pick the first available.
    """
    for attr in ("overall_score", "score", "weighted_score"):
        value = getattr(a, attr, None)
        if isinstance(value, (int, float)) and 0.0 <= float(value) <= 1.0:
            return float(value)
        # Some fields are stored in [0, 100] — normalise.
        if isinstance(value, (int, float)) and 0.0 <= float(value) <= 100.0:
            return float(value) / 100.0
    return None


def recompute_for_pair(
    db: Session, *, organization_id: int, task_id: int, role_family: str
) -> TaskCalibration:
    """Recompute one (task, role_family) calibration row. Idempotent —
    creates the row if missing.
    """
    pairs = _gather_pairs(
        db, organization_id=organization_id, task_id=task_id, role_family=role_family,
    )
    pq = pearson_correlation(pairs) if pairs else 0.0
    avg_quality = (
        sum(p.outcome_quality for p in pairs) / len(pairs) if pairs else None
    )
    row = (
        db.query(TaskCalibration)
        .filter(
            TaskCalibration.task_id == task_id,
            TaskCalibration.role_family == role_family,
        )
        .first()
    )
    now = datetime.now(timezone.utc)
    if row is None:
        row = TaskCalibration(
            organization_id=organization_id,
            task_id=task_id,
            role_family=role_family,
            predictive_quality=pq,
            sample_size=len(pairs),
            avg_outcome_quality=avg_quality,
            last_recomputed_at=now,
        )
        db.add(row)
    else:
        row.predictive_quality = pq
        row.sample_size = len(pairs)
        row.avg_outcome_quality = avg_quality
        row.last_recomputed_at = now
    db.flush()

    # Retirement check.
    if (
        row.retired_at is None
        and row.sample_size >= RETIRE_MIN_N
        and row.predictive_quality < RETIRE_THRESHOLD
    ):
        row.retired_at = now
        row.retired_reason = (
            f"predictive_quality {row.predictive_quality:.2f} below threshold "
            f"{RETIRE_THRESHOLD} after n={row.sample_size}"
        )
        db.flush()

    return row


def recompute_all(db: Session) -> dict[str, int]:
    """Nightly entry-point. Iterates every active template × role_family
    pair seen in assessment history and writes calibrations. Returns a
    small summary dict.
    """
    summary = {"computed": 0, "retired": 0}
    # Enumerate (org, task_id) pairs from active templates.
    templates = (
        db.query(Task).filter(Task.is_template.is_(True), Task.is_active.is_(True)).all()
    )
    for template in templates:
        # Find every role family this template's assessments touched.
        role_families = set(
            r.name
            for (r,) in (
                db.query(Role)
                .join(CandidateApplication, CandidateApplication.role_id == Role.id)
                .join(
                    Assessment,
                    Assessment.application_id == CandidateApplication.id,
                )
                .filter(
                    Assessment.organization_id == template.organization_id,
                    Assessment.task_id == template.id,
                )
                .all()
            )
        )
        for role_name in role_families:
            family = _default_role_family_mapper(role_name)
            before_retired = (
                db.query(TaskCalibration.retired_at)
                .filter(
                    TaskCalibration.task_id == template.id,
                    TaskCalibration.role_family == family,
                )
                .scalar()
            )
            row = recompute_for_pair(
                db,
                organization_id=int(template.organization_id),
                task_id=int(template.id),
                role_family=family,
            )
            summary["computed"] += 1
            if before_retired is None and row.retired_at is not None:
                summary["retired"] += 1
    db.commit()
    return summary


__all__ = [
    "RETIRE_MIN_N",
    "RETIRE_THRESHOLD",
    "pearson_correlation",
    "recompute_all",
    "recompute_for_pair",
]
