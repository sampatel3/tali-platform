"""Apply a CV parse to ORM rows.

``parse_cv`` (runner.py) is model-agnostic — it takes raw text and returns
a ``ParsedCV``. This module bridges that to the ``CandidateApplication`` /
``Candidate`` rows: it reads ``cv_text``, parses it, and writes the result
into the ``cv_sections`` JSON column on the application and its candidate.

Used by:
- ``_try_fetch_cv_from_workable`` (on-demand Workable CV fetch)
- ``parse_application_cv_sections`` Celery task (async, post-sync — the
  Workable bulk sync stores raw ``cv_text`` but, by design, makes no
  synchronous Claude call in the sync loop)
- ``scripts/backfill_cv_sections`` (one-off drain of historical rows that
  have ``cv_text`` but a null ``cv_sections``)

Best-effort and never raises: a parse failure leaves ``cv_sections``
untouched (null), so the candidate page keeps falling back to raw text and
a later trigger can retry. The caller owns the transaction — this only
mutates ORM attributes on the passed objects; it never commits.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("taali.cv_parsing.apply")


def parse_and_store_cv_sections(
    app: Any,
    *,
    db: Any = None,
    skip_cache: bool = False,
    force: bool = False,
) -> bool:
    """Parse ``app.cv_text`` into ``app.cv_sections`` (and the candidate's).

    Args:
        app: a ``CandidateApplication`` (duck-typed: needs ``cv_text``,
            ``cv_sections``, and optionally ``candidate``/``organization_id``/
            ``role_id``/``id``).
        db: session forwarded to the metering context so the usage event is
            attributed; optional (parse still records without per-org attr).
        skip_cache: bypass the content-hash parse cache.
        force: re-parse even when ``cv_sections`` is already populated.

    Returns:
        True when a *successful* parse was written; False otherwise (no
        text to parse, already parsed, or the parse failed). Mutates ORM
        attributes in place — the caller commits.
    """
    if app is None:
        return False

    if not force and getattr(app, "cv_sections", None) is not None:
        return False

    candidate = getattr(app, "candidate", None)
    cv_text = (getattr(app, "cv_text", "") or "").strip()
    if not cv_text and candidate is not None:
        cv_text = (getattr(candidate, "cv_text", "") or "").strip()
    if not cv_text:
        return False

    app_id = getattr(app, "id", None)
    metering: dict[str, Any] = {"feature": "cv_parse"}
    org_id = getattr(app, "organization_id", None)
    if org_id is not None:
        metering["organization_id"] = org_id
    role_id = getattr(app, "role_id", None)
    if role_id is not None:
        metering["role_id"] = role_id
    if app_id:
        metering["entity_id"] = f"application:{app_id}"
    if db is not None:
        metering["db"] = db

    try:
        from .runner import parse_cv

        parsed = parse_cv(cv_text, skip_cache=skip_cache, metering=metering)
    except Exception:  # pragma: no cover — parse_cv is itself best-effort
        logger.exception("parse_cv raised for application_id=%s", app_id)
        return False

    if parsed.parse_failed:
        # Leave cv_sections null so the page keeps its raw-text fallback and
        # a later trigger (re-sync, backfill) can retry. Storing the failed
        # blob would pin the row to the fallback forever.
        logger.info(
            "CV parse produced no sections for application_id=%s (reason=%s)",
            app_id,
            parsed.error_reason,
        )
        return False

    blob = parsed.model_dump(mode="json")
    app.cv_sections = blob
    if candidate is not None:
        candidate.cv_sections = blob
    return True
