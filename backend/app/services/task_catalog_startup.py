"""Fail-closed startup publication of canonical assessment task specs."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from ..platform.database import SessionLocal
from .task_catalog import canonical_task_catalog_dir, sync_template_task_specs
from .task_spec_loader import (
    TaskSpecValidationMode,
    load_task_specs,
)


def sync_canonical_task_specs_on_startup(database_url: str) -> dict[str, Any] | None:
    """Publish canonical task deploy artifacts before candidate traffic.

    SQLite test databases own isolated fixtures and deliberately skip catalog
    publication. PostgreSQL deployments serialize concurrent rolling starts
    with a transaction-scoped advisory lock.
    """
    if str(database_url or "").startswith("sqlite"):
        return None

    specs = load_task_specs(
        canonical_task_catalog_dir(),
        validation_mode=TaskSpecValidationMode.PUBLICATION,
    )
    with SessionLocal() as task_db:
        if task_db.bind is not None and task_db.bind.dialect.name == "postgresql":
            task_db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": 831_774_201},
            )
        return sync_template_task_specs(task_db, specs)


__all__ = ["sync_canonical_task_specs_on_startup"]
