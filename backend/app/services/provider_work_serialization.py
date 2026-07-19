"""Database serialization for duplicate provider jobs."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text


def serialize_provider_work(db: Any, *, scope: str, entity_id: int) -> None:
    """Take a transaction advisory lock for one provider-owned entity."""

    bind = db.get_bind() if hasattr(db, "get_bind") else getattr(db, "bind", None)
    if bind is None or getattr(bind.dialect, "name", None) != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:scope), :entity_id)"),
        {"scope": str(scope), "entity_id": int(entity_id)},
    )


__all__ = ["serialize_provider_work"]
