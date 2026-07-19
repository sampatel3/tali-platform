"""Bounded retention for transport-level Anthropic diagnostic rows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models.anthropic_wire_log import AnthropicWireLog


def prune_wire_logs(
    db: Session,
    *,
    retention_days: int,
    batch_size: int = 10_000,
    max_batches: int = 20,
    now: datetime | None = None,
) -> dict:
    """Delete old rows in bounded commits so pruning cannot hold a huge lock."""
    days = max(1, int(retention_days))
    size = max(1, min(int(batch_size), 50_000))
    batches = max(1, min(int(max_batches), 100))
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    deleted = 0
    for _ in range(batches):
        ids = [
            int(row_id)
            for (row_id,) in (
                db.query(AnthropicWireLog.id)
                .filter(AnthropicWireLog.created_at < cutoff)
                .order_by(AnthropicWireLog.id.asc())
                .limit(size)
                .all()
            )
        ]
        if not ids:
            break
        deleted += (
            db.query(AnthropicWireLog)
            .filter(AnthropicWireLog.id.in_(ids))
            .delete(synchronize_session=False)
        )
        db.commit()
        if len(ids) < size:
            break
    return {"status": "ok", "deleted": deleted, "retention_days": days}


__all__ = ["prune_wire_logs"]
