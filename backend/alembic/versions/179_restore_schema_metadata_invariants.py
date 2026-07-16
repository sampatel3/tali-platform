"""Restore schema invariants exposed by metadata parity checks.

Revision ID: 179_restore_schema_metadata_invariants
Revises: 178_cv_score_dispatch_approval
Create Date: 2026-07-16
"""

from __future__ import annotations

import sys

import sqlalchemy as sa
from alembic import op


revision = "179_restore_schema_metadata_invariants"
down_revision = "178_cv_score_dispatch_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fail before any write if historic rows would violate the intended
    # self-reference.  The migration transaction leaves every row untouched
    # and reports an actionable count for manual data repair.
    bind = op.get_bind()
    orphan_count = int(
        bind.execute(
            sa.text(
                """
                SELECT COUNT(*)
                FROM role_intents AS child
                LEFT JOIN role_intents AS parent
                  ON parent.id = child.superseded_id
                WHERE child.superseded_id IS NOT NULL
                  AND parent.id IS NULL
                """
            )
        ).scalar_one()
    )
    if orphan_count:
        message = (
            "Cannot restore fk_role_intents_superseded_id: "
            f"found {orphan_count} dangling role_intents.superseded_id value(s)."
        )
        # The canonical wrapper deliberately redacts arbitrary driver error
        # strings.  This message contains only a count and schema identifiers,
        # so emit it explicitly before failing the migration transaction.
        print(f"[schema-invariant] ERROR: {message}", file=sys.stderr, flush=True)
        raise RuntimeError(message)

    # Preserve every non-NULL value.  Legacy NULL booleans become fail-closed
    # values matching current access semantics before constraints tighten.
    op.execute(sa.text("UPDATE users SET is_active = false WHERE is_active IS NULL"))
    op.execute(
        sa.text("UPDATE users SET is_superuser = false WHERE is_superuser IS NULL")
    )
    op.alter_column("users", "is_active", existing_type=sa.Boolean(), nullable=False)
    op.alter_column(
        "users", "is_superuser", existing_type=sa.Boolean(), nullable=False
    )
    # Revision 082 declared this use_alter self-reference inside create_table,
    # but never emitted the deferred constraint.  Add the intended invariant
    # now; existing dangling references fail the transaction rather than being
    # silently discarded.
    op.create_foreign_key(
        "fk_role_intents_superseded_id",
        "role_intents",
        "role_intents",
        ["superseded_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_role_intents_superseded_id", "role_intents", type_="foreignkey"
    )
    op.alter_column(
        "users", "is_superuser", existing_type=sa.Boolean(), nullable=True
    )
    op.alter_column("users", "is_active", existing_type=sa.Boolean(), nullable=True)
