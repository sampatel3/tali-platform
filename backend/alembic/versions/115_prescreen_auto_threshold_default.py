"""Default roles.auto_reject_threshold_mode to 'auto' (new roles dynamic).

The per-role reject threshold should be data-driven, not a recruiter-pinned
number. Flip the column default manual -> auto so NEWLY created roles use the
dynamic ``auto_threshold_service`` recommendation by default. EXISTING rows are
deliberately left untouched (they keep whatever mode they already have) — a
separate, explicit migration would be needed to move existing roles to auto.

Revision ID: 115_prescreen_auto_threshold_default
Revises: 114_add_threshold_calibrations
Create Date: 2026-06-14
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "115_prescreen_auto_threshold_default"
down_revision = "114_add_threshold_calibrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Change the server-side default only; existing rows are not rewritten.
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("roles") as batch_op:
            batch_op.alter_column(
                "auto_reject_threshold_mode",
                existing_type=sa.String(),
                server_default="auto",
            )
    else:
        op.alter_column(
            "roles",
            "auto_reject_threshold_mode",
            server_default="auto",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("roles") as batch_op:
            batch_op.alter_column(
                "auto_reject_threshold_mode",
                existing_type=sa.String(),
                server_default="manual",
            )
    else:
        op.alter_column(
            "roles",
            "auto_reject_threshold_mode",
            server_default="manual",
        )
