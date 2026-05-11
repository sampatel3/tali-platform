"""Drop legacy per-role auto-reject columns.

Four columns on ``roles`` predated the agent-native HITL toggles and the
``score_threshold`` column. They've duplicated the canonical fields
(``auto_reject`` + ``score_threshold``) and the org-level
``workable_config`` defaults ever since, and the only UI that wrote them
(the Edit Role sheet's auto-reject card and additional-requirements
textarea) has been removed.

Dropped columns:
- ``auto_reject_enabled``        — replaced by ``org.workable_config["auto_reject_enabled"]`` as the workspace gate
- ``auto_reject_threshold_100``  — replaced by ``role.score_threshold`` (canonical); values are copied across before drop
- ``auto_reject_note_template``  — replaced by ``org.workable_config["auto_reject_note_template"]`` (org-level only)
- ``workable_disqualify_reason_id`` — replaced by ``org.workable_config["workable_disqualify_reason_id"]`` (org-level only)

The HITL autonomy gate (``role.auto_reject``) and the threshold mode
(``role.auto_reject_threshold_mode``) are unchanged — both still drive
agent decision execution per role.

Revision ID: 076_drop_legacy_role_auto_reject_columns
Revises: 075_add_subject_id_to_agent_needs_input
Create Date: 2026-05-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "076_drop_legacy_role_auto_reject_columns"
down_revision = "075_add_subject_id_to_agent_needs_input"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Backfill: any role whose canonical ``score_threshold`` is still
    # null but had a legacy ``auto_reject_threshold_100`` set adopts
    # that legacy value. Pre-pilot data is sparse, so this is mostly
    # a no-op, but it preserves recruiter intent where set.
    op.execute(
        "UPDATE roles "
        "SET score_threshold = auto_reject_threshold_100 "
        "WHERE score_threshold IS NULL "
        "AND auto_reject_threshold_100 IS NOT NULL"
    )
    op.drop_column("roles", "auto_reject_enabled")
    op.drop_column("roles", "auto_reject_threshold_100")
    op.drop_column("roles", "auto_reject_note_template")
    op.drop_column("roles", "workable_disqualify_reason_id")


def downgrade() -> None:
    op.add_column(
        "roles",
        sa.Column("auto_reject_enabled", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "roles",
        sa.Column("auto_reject_threshold_100", sa.Integer(), nullable=True),
    )
    op.add_column(
        "roles",
        sa.Column("auto_reject_note_template", sa.Text(), nullable=True),
    )
    op.add_column(
        "roles",
        sa.Column("workable_disqualify_reason_id", sa.String(), nullable=True),
    )
