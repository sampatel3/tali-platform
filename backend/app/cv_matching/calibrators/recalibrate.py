"""Recalibration job (RALPH 3.3).

Refit per-(role_family, dimension) calibrators from the latest
override data. Designed to run weekly via a Celery beat schedule
(or any cron equivalent). Emits Brier and ECE per fit; alerts when
ECE > 0.05.

Entry points:

    python -m app.cv_matching.calibrators.recalibrate          # one-shot run
    recalibrate_all()                                          # programmatic

The job:
1. Extract every override since (now - lookback_days).
2. Group by role_family.
3. For each role_family + each dimension present in the data, fit
   a calibrator and persist a snapshot.
4. Compute Brier score and Expected Calibration Error per fit.
5. Log alerts when ECE > ECE_ALERT_THRESHOLD.

When the override table is empty (early life of the system) or the
DB is unavailable, the job exits cleanly with no fits.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from .api import fit_calibrator
from .extractor import (
    CalibrationRecord,
    extract_records,
    group_by_role_family,
)

logger = logging.getLogger("taali.cv_match.calibrators.recalibrate")

DEFAULT_LOOKBACK_DAYS = 90
ECE_ALERT_THRESHOLD = 0.05
_MIN_RECORDS_FOR_FIT = 10  # below this, the fit is meaningless


@dataclass
class RecalibrationReport:
    role_family: str
    dimension: str
    n_records: int
    brier_score: float
    ece: float
    alerted: bool


def _brier_score(predictions: Sequence[float], labels: Sequence[bool]) -> float:
    """Mean squared error between predicted P and {0, 1} label."""
    if not predictions:
        return 0.0
    return sum(
        (p - (1.0 if y else 0.0)) ** 2 for p, y in zip(predictions, labels)
    ) / len(predictions)


def _expected_calibration_error(
    predictions: Sequence[float],
    labels: Sequence[bool],
    *,
    n_bins: int = 10,
) -> float:
    """Standard ECE: weighted mean of |bin_acc - bin_conf| across bins."""
    if not predictions:
        return 0.0
    n = len(predictions)
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for p, y in zip(predictions, labels):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append((p, y))
    ece = 0.0
    for b in bins:
        if not b:
            continue
        bin_conf = sum(p for p, _ in b) / len(b)
        bin_acc = sum(1.0 for _, y in b if y) / len(b)
        ece += (len(b) / n) * abs(bin_acc - bin_conf)
    return ece


def _fit_one(
    role_family: str,
    dimension: str,
    records: list[CalibrationRecord],
) -> RecalibrationReport | None:
    pairs = [
        (r.raw_scores[dimension], r.advanced)
        for r in records
        if dimension in r.raw_scores
    ]
    if len(pairs) < _MIN_RECORDS_FOR_FIT:
        logger.info(
            "Skipping fit role_family=%s dim=%s — only %d records",
            role_family,
            dimension,
            len(pairs),
        )
        return None

    X = [p[0] for p in pairs]
    y = [p[1] for p in pairs]
    cal = fit_calibrator(role_family=role_family, dimension=dimension, X=X, y=y)

    preds = [cal.predict(x) for x in X]
    brier = _brier_score(preds, y)
    ece = _expected_calibration_error(preds, y)
    alerted = ece > ECE_ALERT_THRESHOLD
    if alerted:
        logger.warning(
            "ECE alert role_family=%s dim=%s ece=%.4f > threshold=%.4f",
            role_family,
            dimension,
            ece,
            ECE_ALERT_THRESHOLD,
        )

    return RecalibrationReport(
        role_family=role_family,
        dimension=dimension,
        n_records=len(pairs),
        brier_score=brier,
        ece=ece,
        alerted=alerted,
    )


_DIMENSIONS_TO_FIT = (
    "role_fit",
    "cv_fit",
    "requirements_match",
    "skills_coverage",
    "skills_depth",
    "title_trajectory",
    "seniority_alignment",
    "industry_match",
    "tenure_pattern",
)


def recalibrate_all(
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[RecalibrationReport]:
    """Run the recalibration job. Returns a per-(family, dim) report list.

    Empty list when the DB is empty or unavailable.
    """
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    records = extract_records(since=since)
    if not records:
        logger.info("No calibration records to fit (lookback=%d days)", lookback_days)
        return []
    grouped = group_by_role_family(records)
    reports: list[RecalibrationReport] = []
    for role_family, group in grouped.items():
        for dim in _DIMENSIONS_TO_FIT:
            report = _fit_one(role_family, dim, group)
            if report is not None:
                reports.append(report)
    return reports


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    reports = recalibrate_all()
    print(f"Fit {len(reports)} (role_family, dimension) calibrators.")
    for r in reports:
        marker = "ALERT" if r.alerted else "ok"
        print(
            f"  {marker:<5} {r.role_family}/{r.dimension} "
            f"n={r.n_records} brier={r.brier_score:.4f} ece={r.ece:.4f}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())
