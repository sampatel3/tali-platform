"""High-level fit / apply / save / load API for calibrators.

Selection rule (per RALPH 3.1): N < 1000 → Platt, N >= 1000 → Isotonic.

Persistence layout:

    backend/app/cv_matching/calibrators/snapshots/
        {role_family}_{dimension}_{ts}.json
        {role_family}_{dimension}_latest.json     # symlink-style copy

The "latest" copy means the runtime always reads a stable filename.
``apply_calibrator`` returns None when no snapshot exists for the
requested (role_family, dimension) — caller falls back to the raw
score.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .isotonic import IsotonicCalibrator
from .platt import PlattCalibrator

logger = logging.getLogger("taali.cv_match.calibrators")

_SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"
_PLATT_THRESHOLD = 1000  # N < this → Platt; otherwise Isotonic


def _calibrator_path(
    role_family: str,
    dimension: str,
    *,
    timestamp: str | None = None,
) -> Path:
    if timestamp is None:
        return _SNAPSHOT_DIR / f"{role_family}_{dimension}_latest.json"
    return _SNAPSHOT_DIR / f"{role_family}_{dimension}_{timestamp}.json"


def fit_calibrator(
    *,
    role_family: str,
    dimension: str,
    X: Sequence[float],
    y: Sequence[bool],
):
    """Fit a calibrator. Strategy auto-selected by sample size.

    Raises ``ValueError`` on empty input. Returns the fitted
    calibrator object (also written to disk under
    ``snapshots/{role_family}_{dimension}_{ts}.json`` and copied to
    ``..._latest.json``).
    """
    if len(X) != len(y):
        raise ValueError(f"X and y length mismatch: {len(X)} vs {len(y)}")
    if not X:
        raise ValueError("Cannot fit on empty training data")

    if len(X) < _PLATT_THRESHOLD:
        cal = PlattCalibrator().fit(X, y)
    else:
        cal = IsotonicCalibrator().fit(X, y)

    save_calibrator(role_family, dimension, cal)
    return cal


def save_calibrator(role_family: str, dimension: str, cal) -> Path:
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    timestamped = _calibrator_path(role_family, dimension, timestamp=timestamp)
    latest = _calibrator_path(role_family, dimension)

    payload = cal.to_dict()
    body = json.dumps(payload, indent=2)
    timestamped.write_text(body, encoding="utf-8")
    latest.write_text(body, encoding="utf-8")
    logger.info(
        "Saved calibrator role_family=%s dim=%s -> %s",
        role_family,
        dimension,
        timestamped.name,
    )
    return latest


def load_calibrator(role_family: str, dimension: str):
    """Load the latest calibrator for (role_family, dimension), or None."""
    path = _calibrator_path(role_family, dimension)
    if not path.exists():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Failed to read %s: %s", path, exc)
        return None
    kind = blob.get("kind")
    if kind == "platt":
        return PlattCalibrator.from_dict(blob)
    if kind == "isotonic":
        return IsotonicCalibrator.from_dict(blob)
    logger.warning("Unknown calibrator kind=%r in %s", kind, path)
    return None


def apply_calibrator(
    role_family: str, dimension: str, raw_score: float
) -> float | None:
    """Apply the calibrated mapping. None when no snapshot exists.

    The calibrators expect raw scores on whatever scale they were
    trained on. Most callers pass 0-100 ``role_fit_score`` /
    dimension scores; that's fine because Platt standardises and
    Isotonic is scale-equivariant.
    """
    cal = load_calibrator(role_family, dimension)
    if cal is None:
        return None
    return float(cal.predict(raw_score))
