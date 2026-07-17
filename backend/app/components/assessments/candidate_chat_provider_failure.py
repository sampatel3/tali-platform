"""Durable no-replay handling for ambiguous candidate-chat provider exits."""

from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session


def ambiguous_chat_failure_http(
    db: Session,
    *,
    prepared: Any,
    token: str,
    assessment_id: int,
    advance_claim: Callable[..., dict[str, Any]],
    latency_ms: int,
    last_error: str,
    status_code: int,
    message: str,
    logger: logging.Logger,
) -> HTTPException:
    """Persist terminal no-replay evidence and build the public HTTP error."""

    try:
        advance_claim(
            db,
            prepared,
            token,
            state="manual_reconciliation_required",
            updates={
                "provider_disposition": "manual_reconciliation_required",
                "reconciliation_disposition": "provider_outcome_not_replayed",
                "last_error": last_error,
            },
            timeline_error_ms=latency_ms,
        )
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to persist ambiguous chat outcome assessment_id=%s",
            assessment_id,
        )
    return HTTPException(status_code=status_code, detail={"message": message})


__all__ = ["ambiguous_chat_failure_http"]
