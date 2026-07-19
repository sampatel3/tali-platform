"""Persist a collision-free template repository identity for every task.

Revision ID: 191_task_repo_identity
Revises: 190_fireflies_org_index
Create Date: 2026-07-18
"""

from __future__ import annotations

import hashlib
import re

import sqlalchemy as sa
from alembic import op


revision = "191_task_repo_identity"
down_revision = "190_fireflies_org_index"
branch_labels = None
depends_on = None


_INDEX_NAME = "ix_tasks_template_repository_name"
_LOWERCASE_CONSTRAINT_NAME = "ck_tasks_template_repository_name_lowercase"
_MAX_REPOSITORY_NAME_LENGTH = 100


def _rolling_writer_default(dialect_name: str) -> sa.TextClause:
    """Generate a safe identity for pre-191 writers during a rolling deploy."""

    if dialect_name == "postgresql":
        return sa.text(
            "('task-auto-' || replace(gen_random_uuid()::text, '-', ''))"
        )
    if dialect_name == "sqlite":
        return sa.text("('task-auto-' || lower(hex(randomblob(16))))")
    raise RuntimeError(f"unsupported migration dialect: {dialect_name}")


def _legacy_repository_name(task_id: int, task_key: object) -> str:
    """Reproduce the pre-191 repository name whenever it is safe to retain.

    Existing GitHub repositories were created with this deliberately lossy
    slug.  Changing that calculation during backfill would strand the live
    repository.  Only names that could never have been addressed safely by the
    current service receive a deterministic replacement.
    """

    raw_text = str(task_key or task_id)
    name = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw_text).strip("-").lower() or "task"
    if (
        name not in {".", ".."}
        and name.casefold() != ".git"
        and len(name) <= _MAX_REPOSITORY_NAME_LENGTH
    ):
        return name

    digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]
    readable = (
        "task"
        if name in {".", ".."} or name.casefold() == ".git"
        else name.strip("-.")[:83].rstrip("-.") or "task"
    )
    return f"{readable}-{digest}"


def _collision_repository_name(
    base: str,
    *,
    organization_id: object,
    task_id: int,
    used: set[str],
) -> str:
    organization_part = (
        str(organization_id)
        if type(organization_id) is int and organization_id > 0
        else "0"
    )
    suffix = f"-o{organization_part}-t{task_id}"
    prefix = base[: _MAX_REPOSITORY_NAME_LENGTH - len(suffix)].rstrip("-.")
    candidate = f"{prefix or 'task'}{suffix}"
    if candidate.casefold() not in used:
        return candidate

    digest_suffix = hashlib.sha256(
        f"{organization_part}:{task_id}:{base}".encode("utf-8")
    ).hexdigest()[:16]
    suffix = f"-{digest_suffix}"
    prefix = base[: _MAX_REPOSITORY_NAME_LENGTH - len(suffix)].rstrip("-.")
    return f"{prefix or 'task'}{suffix}"


def _backfill_repository_names(bind: sa.engine.Connection) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT id, organization_id, task_key FROM tasks ORDER BY id ASC"
        )
    ).mappings()
    used: set[str] = set()
    for row in rows:
        task_id = int(row["id"])
        legacy = _legacy_repository_name(task_id, row["task_key"])
        repository_name = legacy
        if repository_name.casefold() in used:
            repository_name = _collision_repository_name(
                legacy,
                organization_id=row["organization_id"],
                task_id=task_id,
                used=used,
            )
        if repository_name.casefold() in used:
            raise RuntimeError("could not allocate a unique task repository identity")
        bind.execute(
            sa.text(
                "UPDATE tasks SET template_repository_name = :repository_name "
                "WHERE id = :task_id"
            ),
            {"repository_name": repository_name, "task_id": task_id},
        )
        used.add(repository_name.casefold())


def upgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError(
            "revision 191 requires an online migration to preserve legacy "
            "repository identities safely"
        )
    op.add_column(
        "tasks",
        sa.Column("template_repository_name", sa.String(length=100), nullable=True),
    )
    bind = op.get_bind()
    _backfill_repository_names(bind)
    rolling_writer_default = _rolling_writer_default(bind.dialect.name)
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("tasks") as batch_op:
            batch_op.alter_column(
                "template_repository_name",
                existing_type=sa.String(length=100),
                nullable=False,
                server_default=rolling_writer_default,
            )
            batch_op.create_check_constraint(
                _LOWERCASE_CONSTRAINT_NAME,
                "template_repository_name = lower(template_repository_name)",
            )
    else:
        op.alter_column(
            "tasks",
            "template_repository_name",
            existing_type=sa.String(length=100),
            nullable=False,
            server_default=rolling_writer_default,
        )
        op.create_check_constraint(
            _LOWERCASE_CONSTRAINT_NAME,
            "tasks",
            "template_repository_name = lower(template_repository_name)",
        )
    op.create_index(
        _INDEX_NAME,
        "tasks",
        ["template_repository_name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="tasks")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("tasks") as batch_op:
            batch_op.drop_constraint(
                _LOWERCASE_CONSTRAINT_NAME,
                type_="check",
            )
            batch_op.drop_column("template_repository_name")
    else:
        op.drop_constraint(
            _LOWERCASE_CONSTRAINT_NAME,
            "tasks",
            type_="check",
        )
        op.drop_column("tasks", "template_repository_name")
