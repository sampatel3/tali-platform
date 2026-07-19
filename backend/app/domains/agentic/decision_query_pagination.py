"""Stable keyset pagination for the agent-decision list."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import and_, or_

from ...models.agent_decision import AgentDecision


def apply_before_cursor(
    query: Any,
    *,
    before_created_at: Optional[datetime],
    before_id: Optional[int],
    status: str,
) -> Any:
    """Apply the list route's ``created_at DESC, id DESC`` cursor.

    Both values are required because bulk-created decisions commonly share one
    transaction timestamp.  The candidate-report ``current`` lens has a
    different live-first ordering and deliberately remains a one-row lookup.
    """

    if (before_created_at is None) != (before_id is None):
        raise HTTPException(
            status_code=422,
            detail="before_created_at and before_id must be supplied together",
        )
    if before_created_at is None:
        return query
    if status == "current":
        raise HTTPException(
            status_code=422,
            detail="decision cursors are not supported for status='current'",
        )
    return query.filter(
        or_(
            AgentDecision.created_at < before_created_at,
            and_(
                AgentDecision.created_at == before_created_at,
                AgentDecision.id < int(before_id),
            ),
        )
    )


__all__ = ["apply_before_cursor"]
