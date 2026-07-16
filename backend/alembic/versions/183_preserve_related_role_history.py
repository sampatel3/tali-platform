"""Prevent related-role relationships and evaluations from cascade deletion.

Revision ID: 183_preserve_related_role_history
Revises: 182_workspace_pause_compat_audit
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "183_preserve_related_role_history"
down_revision = "182_workspace_pause_compat_audit"
branch_labels = None
depends_on = None

_SUPPORTED_DIALECTS = frozenset({"postgresql", "sqlite"})


def _postgres_replace_foreign_keys() -> None:
    # Runtime mutations use role -> evaluation order. Take the same order and
    # keep each CASCADE constraint active until its validated RESTRICT
    # replacement is ready, so no row is ever exposed to an unchecked gap.
    op.execute(
        "LOCK TABLE roles, sister_role_evaluations "
        "IN SHARE ROW EXCLUSIVE MODE"
    )
    replacements = (
        (
            "roles",
            "roles_ats_owner_role_id_fkey",
            "roles_ats_owner_role_id_restrict_v183",
            "ats_owner_role_id",
            "roles",
        ),
        (
            "sister_role_evaluations",
            "sister_role_evaluations_role_id_fkey",
            "sister_role_evaluations_role_id_restrict_v183",
            "role_id",
            "roles",
        ),
    )
    for table, canonical, replacement, column, referred_table in replacements:
        op.execute(
            sa.text(
                f"ALTER TABLE {table} ADD CONSTRAINT {replacement} "
                f"FOREIGN KEY ({column}) REFERENCES {referred_table} (id) "
                "ON DELETE RESTRICT NOT VALID"
            )
        )
        op.execute(
            sa.text(
                f"ALTER TABLE {table} VALIDATE CONSTRAINT {replacement}"
            )
        )
        op.drop_constraint(canonical, table, type_="foreignkey")
        op.execute(
            sa.text(
                f"ALTER TABLE {table} RENAME CONSTRAINT "
                f"{replacement} TO {canonical}"
            )
        )


def _sqlite_replace_foreign_keys() -> None:
    # SQLite cannot replace a referenced/self-referential FK in place while
    # foreign-key enforcement is active, and disabling it would require an
    # unsafe transaction boundary. Add equivalent BEFORE DELETE guards for
    # migrated local/test databases. Fresh SQLite schemas created from the ORM
    # receive the canonical RESTRICT FKs directly; production PostgreSQL uses
    # the validated constraint replacement above.
    op.execute(
        """
        CREATE TRIGGER preserve_owner_role_related_history
        BEFORE DELETE ON roles
        FOR EACH ROW
        WHEN EXISTS (
            SELECT 1 FROM roles AS related
            WHERE related.ats_owner_role_id = OLD.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'owner role has preserved related-role history');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER preserve_related_role_evaluations
        BEFORE DELETE ON roles
        FOR EACH ROW
        WHEN EXISTS (
            SELECT 1 FROM sister_role_evaluations AS evaluation
            WHERE evaluation.role_id = OLD.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'related role has preserved evaluation history');
        END
        """
    )


def upgrade() -> None:
    dialect = str(op.get_bind().dialect.name)
    if dialect not in _SUPPORTED_DIALECTS:
        raise RuntimeError(
            "Revision 183 supports only PostgreSQL and SQLite; refusing to "
            f"replace history-preservation constraints on {dialect!r}."
        )
    if dialect == "postgresql":
        _postgres_replace_foreign_keys()
        return
    _sqlite_replace_foreign_keys()


def downgrade() -> None:
    raise RuntimeError(
        "Revision 183 is intentionally irreversible: restoring CASCADE would "
        "make preserved related-role history destructible again."
    )
