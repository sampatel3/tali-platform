"""Execution authority carried by asynchronous CV-section parsing work.

The application source is useful attribution, but it is not sufficient
authority for a queued provider call: a recruiter may explicitly replace the
CV on an ATS/native application while its role agent is paused.  Producers
therefore stamp one of these origins into the Celery message or batch context.
Unknown/legacy values are intentionally not authorized.
"""

from __future__ import annotations

from typing import Any


CV_PARSE_ORIGIN_ATS_INGEST = "ats_ingest"
CV_PARSE_ORIGIN_NATIVE_APPLY = "native_apply"
CV_PARSE_ORIGIN_RECRUITER_UPLOAD = "recruiter_upload"

AUTONOMOUS_CV_PARSE_ORIGINS = frozenset(
    {
        CV_PARSE_ORIGIN_ATS_INGEST,
        CV_PARSE_ORIGIN_NATIVE_APPLY,
    }
)
EXPLICIT_CV_PARSE_ORIGINS = frozenset({CV_PARSE_ORIGIN_RECRUITER_UPLOAD})
AUTHORIZED_CV_PARSE_ORIGINS = (
    AUTONOMOUS_CV_PARSE_ORIGINS | EXPLICIT_CV_PARSE_ORIGINS
)


def normalize_cv_parse_origin(origin: Any) -> str | None:
    """Return a known origin, otherwise ``None`` (fail closed)."""

    value = str(origin or "").strip().lower()
    return value if value in AUTHORIZED_CV_PARSE_ORIGINS else None


def autonomous_origin_for_application(app: Any) -> str | None:
    """Classify persisted autonomous intake rows for the batch sweep.

    Only ATS and native careers applications are safe to infer from durable
    application/role attribution.  A ``manual`` row is not proof that a human
    just requested provider work, so it is deliberately left unclassified.
    """

    source = str(getattr(app, "source", "") or "").strip().lower()
    if source in {"workable", "bullhorn"}:
        return CV_PARSE_ORIGIN_ATS_INGEST
    if source == "careers":
        return CV_PARSE_ORIGIN_NATIVE_APPLY
    return None


__all__ = [
    "AUTHORIZED_CV_PARSE_ORIGINS",
    "AUTONOMOUS_CV_PARSE_ORIGINS",
    "CV_PARSE_ORIGIN_ATS_INGEST",
    "CV_PARSE_ORIGIN_NATIVE_APPLY",
    "CV_PARSE_ORIGIN_RECRUITER_UPLOAD",
    "EXPLICIT_CV_PARSE_ORIGINS",
    "autonomous_origin_for_application",
    "normalize_cv_parse_origin",
]
