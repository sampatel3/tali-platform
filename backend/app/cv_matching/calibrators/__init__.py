"""Per-(role_family, dimension) calibrators (RALPH 3.1).

Two strategies, automatically selected by sample size:

- ``PlattCalibrator``    — logistic regression on (raw → advance).
                           Used when N < 1000 (small-data regime).
- ``IsotonicCalibrator`` — pool-adjacent-violators piecewise-constant.
                           Used when N >= 1000 (large-data regime).

Persistence is JSON (not pickle) because calibrator state is small
and JSON keeps the artefact human-inspectable. JSON also avoids the
arbitrary-code-execution risk of pickling something written by a
training pipeline and unpickled in production.

Public surface:

    from app.cv_matching.calibrators import (
        fit_calibrator, apply_calibrator,
    )

    cal = fit_calibrator(role_family="aws_glue", dimension="cv_fit",
                         X=raw_scores, y=advance_labels)
    p_advance = apply_calibrator(role_family, dimension, raw_score)
"""

from __future__ import annotations

from .api import (
    apply_calibrator,
    fit_calibrator,
    load_calibrator,
    save_calibrator,
)
from .isotonic import IsotonicCalibrator
from .platt import PlattCalibrator

__all__ = [
    "IsotonicCalibrator",
    "PlattCalibrator",
    "apply_calibrator",
    "fit_calibrator",
    "load_calibrator",
    "save_calibrator",
]
