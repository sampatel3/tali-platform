"""High-level fit / apply / save / load API for calibrators.

Selection rule (per RALPH 3.1): N < 1000 → Platt, N >= 1000 → Isotonic.

Persistence layout:

    backend/app/cv_matching/calibrators/snapshots/
        v2-{pair_sha256}_{ts}.json
        v2-{pair_sha256}_latest.json     # atomic latest copy

The "latest" copy means the runtime always reads a stable filename.
``apply_calibrator`` returns None when no snapshot exists for the
requested (role_family, dimension) — caller falls back to the raw
score.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ...services.provider_error_evidence import safe_provider_error_code
from .isotonic import IsotonicCalibrator
from .platt import PlattCalibrator

logger = logging.getLogger("taali.cv_match.calibrators")

_SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"
_PLATT_THRESHOLD = 1000  # N < this → Platt; otherwise Isotonic
_REMOTE_REFRESH_SECONDS = 300.0
MAX_CALIBRATOR_SNAPSHOT_BYTES = 64 * 1024 * 1024
_remote_checked_at: dict[tuple[str, str], float] = {}
_LEGACY_DIMENSIONS = frozenset(
    {
        "role_fit",
        "cv_fit",
        "requirements_match",
        "skills_coverage",
        "skills_depth",
        "title_trajectory",
        "seniority_alignment",
        "industry_match",
        "tenure_pattern",
    }
)


def _storage_component(value: str) -> str:
    """Map an external calibrator identity to one bounded path component.

    Existing ordinary identifiers retain their exact storage names.  Unsafe or
    overlong values use a reserved ``~`` SHA-256 namespace, which prevents path
    traversal without introducing the collisions caused by lossy slugging.
    """

    if not isinstance(value, str) or not value:
        raise ValueError("Calibrator storage identities must be non-empty strings")
    if (
        value not in {".", ".."}
        and len(value) <= 128
        and re.fullmatch(r"[A-Za-z0-9._-]+", value)
    ):
        return value
    return "~" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _remote_key(role_family: str, dimension: str) -> str:
    return (
        f"calibrators/{_storage_component(role_family)}/"
        f"{_storage_component(dimension)}/latest.json"
    )


def _storage_identity(role_family: str, dimension: str) -> dict[str, str]:
    return {"role_family": role_family, "dimension": dimension}


def _pair_digest(role_family: str, dimension: str) -> str:
    """Hash the ordered pair so delimiter-bearing components cannot collide."""

    # Validate both values through the same contract used by remote storage.
    _storage_component(role_family)
    _storage_component(dimension)
    encoded = json.dumps(
        [role_family, dimension],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _replace_snapshot(path: Path, body: bytes) -> None:
    """Atomically replace a snapshot without following a target symlink."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(body)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


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

        body = download_from_s3(
            _remote_key(role_family, dimension),
            max_bytes=MAX_CALIBRATOR_SNAPSHOT_BYTES,
        )
        if body:
            # Validate both schema and pair identity before replacing the local
            # cache so syntactically-valid corrupt data cannot take a working
            # calibrator offline.
            validated = _validated_snapshot_body(
                body,
                role_family=role_family,
                dimension=dimension,
            )
            _replace_snapshot(latest, validated)
    except Exception as exc:  # pragma: no cover - remote store is best effort
        logger.warning(
            "Calibrator remote refresh failed error_code=%s",
            safe_provider_error_code(exc, operation="calibrator_remote_refresh"),
        )


def _calibrator_path(
    role_family: str,
    dimension: str,
    *,
    timestamp: str | None = None,
) -> Path:
    pair = _pair_digest(role_family, dimension)
    if timestamp is None:
        return _SNAPSHOT_DIR / f"v2-{pair}_latest.json"
    snapshot_time = _storage_component(timestamp)
    return _SNAPSHOT_DIR / f"v2-{pair}_{snapshot_time}.json"


def _legacy_calibrator_path(role_family: str, dimension: str) -> Path:
    """Return the pre-v2 latest path for a supported compatibility read."""

    family = _storage_component(role_family)
    score_dimension = _storage_component(dimension)
    return _SNAPSHOT_DIR / f"{family}_{score_dimension}_latest.json"


def _read_snapshot(path: Path) -> bytes:
    """Read at most one byte beyond the accepted local snapshot ceiling."""

    with path.open("rb") as handle:
        body = handle.read(MAX_CALIBRATOR_SNAPSHOT_BYTES + 1)
    if len(body) > MAX_CALIBRATOR_SNAPSHOT_BYTES:
        raise ValueError("Calibrator snapshot exceeds the byte ceiling")
    return body


def _calibrator_from_blob(blob: object):
    if not isinstance(blob, dict):
        raise ValueError("Calibrator snapshot must be a JSON object")
    kind = blob.get("kind")
    if kind == "platt":
        calibrator = PlattCalibrator.from_dict(blob)
        parameters = (
            calibrator.a,
            calibrator.b,
            calibrator.feature_scale,
            calibrator.feature_shift,
        )
        if not all(math.isfinite(value) for value in parameters):
            raise ValueError("Platt calibrator parameters must be finite")
        if calibrator.feature_scale <= 0:
            raise ValueError("Platt calibrator feature scale must be positive")
        return calibrator
    if kind == "isotonic":
        calibrator = IsotonicCalibrator.from_dict(blob)
        if not calibrator.breakpoints:
            raise ValueError("Isotonic calibrator must contain breakpoints")
        previous_x: float | None = None
        previous_y: float | None = None
        for x_value, y_value in calibrator.breakpoints:
            if not math.isfinite(x_value) or not math.isfinite(y_value):
                raise ValueError("Isotonic calibrator breakpoints must be finite")
            if not 0.0 <= y_value <= 1.0:
                raise ValueError("Isotonic calibrator probabilities must be in [0, 1]")
            if previous_x is not None and x_value < previous_x:
                raise ValueError("Isotonic calibrator x breakpoints must be ordered")
            if previous_y is not None and y_value < previous_y:
                raise ValueError("Isotonic calibrator probabilities must be monotonic")
            previous_x = x_value
            previous_y = y_value
        return calibrator
    raise ValueError("Calibrator snapshot has an unknown kind")


def _identity_matches(
    blob: dict,
    *,
    role_family: str,
    dimension: str,
) -> bool | None:
    stored = blob.get("_storage_identity")
    if stored is None:
        return None
    return stored == _storage_identity(role_family, dimension)


def _validated_snapshot_body(
    body: bytes,
    *,
    role_family: str,
    dimension: str,
) -> bytes:
    blob = json.loads(body.decode("utf-8"))
    _calibrator_from_blob(blob)
    if not isinstance(blob, dict):  # narrowed by _calibrator_from_blob
        raise ValueError("Calibrator snapshot must be a JSON object")
    if _identity_matches(
        blob,
        role_family=role_family,
        dimension=dimension,
    ) is False:
        raise ValueError("Calibrator snapshot identity does not match its storage key")
    blob["_storage_identity"] = _storage_identity(role_family, dimension)
    encoded = json.dumps(blob, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > MAX_CALIBRATOR_SNAPSHOT_BYTES:
        raise ValueError("Calibrator snapshot exceeds the byte ceiling")
    return encoded


def _migrate_legacy_snapshot(
    role_family: str,
    dimension: str,
    destination: Path,
) -> bool:
    """Copy one attributable legacy snapshot into collision-safe v2 storage.

    Historical snapshots did not carry identity metadata.  Only the fixed
    production dimensions have an unambiguous legacy suffix contract; custom
    dimensions require an explicit identity marker to prevent one colliding
    pair from being loaded as another.
    """

    legacy = _legacy_calibrator_path(role_family, dimension)
    if not legacy.exists():
        return False
    try:
        body = _read_snapshot(legacy)
        blob = json.loads(body.decode("utf-8"))
        _calibrator_from_blob(blob)
        if not isinstance(blob, dict):  # narrowed by _calibrator_from_blob
            return False
        identity_match = _identity_matches(
            blob,
            role_family=role_family,
            dimension=dimension,
        )
        if identity_match is False or (
            identity_match is None and dimension not in _LEGACY_DIMENSIONS
        ):
            return False
        _replace_snapshot(
            destination,
            _validated_snapshot_body(
                body,
                role_family=role_family,
                dimension=dimension,
            ),
        )
        return True
    except Exception as exc:
        logger.warning(
            "Calibrator legacy snapshot migration failed error_code=%s",
            safe_provider_error_code(exc, operation="calibrator_legacy_migration"),
        )
        return False


def fit_calibrator(
    *,
    role_family: str,
    dimension: str,
    X: Sequence[float],
    y: Sequence[bool],
):
    """Fit a calibrator. Strategy auto-selected by sample size.

    Raises ``ValueError`` on empty input. Returns the fitted
    calibrator object (also written to collision-safe, pair-digested history
    and latest paths).
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

    payload = {
        **cal.to_dict(),
        "_storage_identity": _storage_identity(role_family, dimension),
    }
    encoded = _validated_snapshot_body(
        json.dumps(payload, indent=2).encode("utf-8"),
        role_family=role_family,
        dimension=dimension,
    )
    _replace_snapshot(timestamped, encoded)
    _replace_snapshot(latest, encoded)
    if _remote_enabled():
        try:
            from ...services.s3_service import upload_bytes_to_s3

            uploaded = upload_bytes_to_s3(
                encoded,
                _remote_key(role_family, dimension),
                content_type="application/json",
            )
            if not uploaded:
                logger.warning("Calibrator saved locally but durable upload was unavailable")
        except Exception as exc:  # pragma: no cover - local fallback remains valid
            logger.warning(
                "Calibrator durable upload failed error_code=%s",
                safe_provider_error_code(exc, operation="calibrator_durable_upload"),
            )
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
    if not path.exists() and not _migrate_legacy_snapshot(
        role_family,
        dimension,
        path,
    ):
        return None
    try:
        blob = json.loads(_read_snapshot(path).decode("utf-8"))
        if not isinstance(blob, dict):
            raise ValueError("Calibrator snapshot must be a JSON object")
        if _identity_matches(
            blob,
            role_family=role_family,
            dimension=dimension,
        ) is False:
            raise ValueError("Calibrator snapshot identity mismatch")
        return _calibrator_from_blob(blob)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "Calibrator snapshot read failed error_code=%s",
            safe_provider_error_code(exc, operation="calibrator_snapshot_read"),
        )
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
