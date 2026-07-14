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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .isotonic import IsotonicCalibrator
from .platt import PlattCalibrator

logger = logging.getLogger("taali.cv_match.calibrators")

_SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"
_PLATT_THRESHOLD = 1000  # N < this → Platt; otherwise Isotonic
_REMOTE_REFRESH_SECONDS = 300.0
_remote_checked_at: dict[tuple[str, str], float] = {}


def _remote_key(role_family: str, dimension: str) -> str:
    return f"calibrators/{role_family}/{dimension}/latest.json"


def _remote_enabled() -> bool:
    from ...platform.config import settings

    return bool(
        not getattr(settings, "S3_DISABLED", False)
        and getattr(settings, "AWS_ACCESS_KEY_ID", None)
        and getattr(settings, "AWS_SECRET_ACCESS_KEY", None)
        and getattr(settings, "AWS_S3_BUCKET", None)
    )


def _refresh_from_remote(role_family: str, dimension: str, latest: Path) -> None:
    """Refresh a worker's local read-through cache at most every five minutes."""
    if not _remote_enabled():
        return
    key = (role_family, dimension)
    now = time.monotonic()
    if now - _remote_checked_at.get(key, 0.0) < _REMOTE_REFRESH_SECONDS:
        return
    _remote_checked_at[key] = now
    try:
        from ...services.s3_service import download_from_s3

        body = download_from_s3(_remote_key(role_family, dimension))
        if body:
            # Validate before replacing the local cache so corrupt remote data
            # cannot take a working calibrator offline.
            json.loads(body.decode("utf-8"))
            latest.parent.mkdir(parents=True, exist_ok=True)
            latest.write_bytes(body)
    except Exception as exc:  # pragma: no cover - remote store is best effort
        logger.warning("Calibrator remote refresh failed for %s/%s: %s", role_family, dimension, exc)


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
    if _remote_enabled():
        try:
            from ...services.s3_service import upload_bytes_to_s3

            uploaded = upload_bytes_to_s3(
                body.encode("utf-8"),
                _remote_key(role_family, dimension),
                content_type="application/json",
            )
            if not uploaded:
                logger.warning("Calibrator saved locally but durable upload was unavailable")
        except Exception as exc:  # pragma: no cover - local fallback remains valid
            logger.warning("Calibrator durable upload failed: %s", exc)
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
    _refresh_from_remote(role_family, dimension, path)
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
