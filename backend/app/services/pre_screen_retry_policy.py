"""Retry policy shared by pre-screen Python and SQL selectors.

Only unmistakably transient provider failures get one shorter retry. Repeated
transient failures and every deterministic/unknown failure retain the longer
cost guard, so a provider outage cannot recreate the historical retry storm.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import and_, func, not_, or_


PreScreenErrorRetryClass = Literal["transient", "deterministic"]

PRE_SCREEN_TRANSIENT_ERROR_BACKOFF = timedelta(minutes=30)
PRE_SCREEN_DETERMINISTIC_ERROR_BACKOFF = timedelta(hours=6)

# One canonical marker set drives both new-row classification and the legacy
# SQL fallback. Splitting these lists silently gives identical errors different
# retry windows depending on which selector encounters them.
_TRANSIENT_TEXT_MARKERS = (
    "rate limit",
    "rate_limit",
    "rate-limit",
    "timeout",
    "timed out",
    "api connection",
    "connection error",
    "connection reset",
    "connection refused",
    "network error",
    "network",
    "overload",
    "internal server",
    "server error",
    "server_error",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "error code: 500",
    "error code: 429",
    "error code: 502",
    "error code: 503",
    "error code: 504",
    "error code: 529",
    "status code: 500",
    "status code: 429",
    "status code: 502",
    "status code: 503",
    "status code: 504",
    "status code: 529",
    "http 500",
    "http 429",
    "http 502",
    "http 503",
    "http 504",
    "http 529",
    "(429)",
    "(529)",
)

_DETERMINISTIC_TEXT_PREFIXES = (
    "json_parse_failed",
    "budget_admission_failed",
    "missing cv",
    "missing_inputs",
    "validation",
    "schema",
)


def classify_pre_screen_error(reason: object) -> PreScreenErrorRetryClass:
    """Classify only explicit transient signals; unknowns fail to long backoff."""

    normalized = str(reason or "").strip().lower()
    if normalized.startswith(_DETERMINISTIC_TEXT_PREFIXES):
        return "deterministic"
    if any(marker in normalized for marker in _TRANSIENT_TEXT_MARKERS):
        return "transient"
    return "deterministic"


def pre_screen_error_retry_class(application: Any) -> PreScreenErrorRetryClass:
    evidence = getattr(application, "pre_screen_evidence", None)
    if isinstance(evidence, dict):
        stored = str(evidence.get("error_retry_class") or "").strip().lower()
        if stored in {"transient", "deterministic"}:
            return stored  # type: ignore[return-value]
    return classify_pre_screen_error(
        getattr(application, "pre_screen_error_reason", None)
    )


def pre_screen_transient_error_streak(application: Any) -> int:
    evidence = getattr(application, "pre_screen_evidence", None)
    if isinstance(evidence, dict):
        try:
            stored = int(evidence.get("transient_error_streak") or 0)
        except (TypeError, ValueError):
            stored = 0
        if stored > 0:
            return stored
    # A classified legacy error has already consumed its initial attempt. It
    # gets one short retry; if that retry fails, persistence records streak=2.
    if (
        getattr(application, "pre_screen_error_reason", None)
        and pre_screen_error_retry_class(application) == "transient"
    ):
        return 1
    return 0


def _as_utc_datetime(value: object) -> datetime | None:
    """Normalize SQLAlchemy/legacy timestamps before ordering them."""

    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_pre_screen_error_retry_metadata(
    application: Any, *, reason: object
) -> dict[str, object]:
    retry_class = classify_pre_screen_error(reason)
    if retry_class != "transient":
        return {"error_retry_class": "deterministic"}

    previous_run = _as_utc_datetime(
        getattr(application, "pre_screen_run_at", None)
    )
    cv_uploaded = _as_utc_datetime(getattr(application, "cv_uploaded_at", None))
    fresh_cv = bool(
        previous_run is not None
        and cv_uploaded is not None
        and cv_uploaded > previous_run
    )
    previous_class = pre_screen_error_retry_class(application)
    previous_streak = pre_screen_transient_error_streak(application)
    streak = (
        previous_streak + 1
        if previous_class == "transient" and not fresh_cv
        else 1
    )
    return {
        "error_retry_class": "transient",
        "transient_error_streak": streak,
    }


def pre_screen_error_backoff(application: Any) -> timedelta:
    if (
        pre_screen_error_retry_class(application) == "transient"
        and pre_screen_transient_error_streak(application) <= 1
    ):
        return PRE_SCREEN_TRANSIENT_ERROR_BACKOFF
    return PRE_SCREEN_DETERMINISTIC_ERROR_BACKOFF


def pre_screen_error_retry_due(
    application: Any, *, now: datetime | None = None
) -> bool:
    last_run = getattr(application, "pre_screen_run_at", None)
    if last_run is None:
        return True
    checked_at = now or datetime.now(timezone.utc)
    return bool(last_run <= checked_at - pre_screen_error_backoff(application))


def pre_screen_error_retry_due_clause(application_model: Any, *, now=None):
    """Portable SQL mirror of :func:`pre_screen_error_retry_due`."""

    checked_at = now or datetime.now(timezone.utc)
    evidence = application_model.pre_screen_evidence
    stored_class = evidence["error_retry_class"].as_string()
    stored_streak = evidence["transient_error_streak"].as_integer()
    normalized_reason = func.lower(
        func.coalesce(application_model.pre_screen_error_reason, "")
    )
    legacy_transient = and_(
        not_(
            or_(
                *(
                    normalized_reason.startswith(prefix)
                    for prefix in _DETERMINISTIC_TEXT_PREFIXES
                )
            )
        ),
        or_(
            *(
                normalized_reason.contains(marker)
                for marker in _TRANSIENT_TEXT_MARKERS
            )
        ),
    )
    transient = or_(
        stored_class == "transient",
        and_(stored_class.is_(None), legacy_transient),
    )
    short_retry = and_(
        transient,
        or_(stored_streak.is_(None), stored_streak <= 1),
        application_model.pre_screen_run_at
        <= checked_at - PRE_SCREEN_TRANSIENT_ERROR_BACKOFF,
    )
    long_retry = (
        application_model.pre_screen_run_at
        <= checked_at - PRE_SCREEN_DETERMINISTIC_ERROR_BACKOFF
    )
    return and_(
        application_model.pre_screen_error_reason.isnot(None),
        application_model.pre_screen_run_at.isnot(None),
        or_(short_retry, long_retry),
    )


__all__ = [
    "PRE_SCREEN_DETERMINISTIC_ERROR_BACKOFF",
    "PRE_SCREEN_TRANSIENT_ERROR_BACKOFF",
    "build_pre_screen_error_retry_metadata",
    "classify_pre_screen_error",
    "pre_screen_error_backoff",
    "pre_screen_error_retry_due",
    "pre_screen_error_retry_due_clause",
]
