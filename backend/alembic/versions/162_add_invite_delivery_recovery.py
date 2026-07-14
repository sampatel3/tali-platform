"""Add durable assessment-invite delivery recovery state.

Revision ID: 162_invite_delivery_recovery
Revises: 161_task_provisioning_state
Create Date: 2026-07-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "162_invite_delivery_recovery"
down_revision = "161_task_provisioning_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assessments",
        sa.Column(
            "invite_email_send_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "assessments",
        sa.Column(
            "invite_email_retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "assessments",
        sa.Column("invite_email_confirmed_generation", sa.Integer(), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column("invite_email_next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column("invite_email_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column("invite_email_last_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column("invite_email_reply_to", sa.String(), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column("invite_pipeline_transition", sa.JSON(), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column("invite_workable_handoff_status", sa.String(), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column("invite_workable_handoff_generation", sa.Integer(), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column("invite_workable_handoff_stage", sa.String(), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column(
            "invite_workable_handoff_retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "assessments",
        sa.Column(
            "invite_workable_handoff_next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "assessments",
        sa.Column(
            "invite_workable_handoff_claimed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "assessments",
        sa.Column("invite_workable_handoff_last_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column("invite_workable_stage_moved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column("invite_workable_note_posted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_assessments_invite_email_recovery",
        "assessments",
        ["invite_email_status", "invite_email_next_attempt_at"],
        unique=False,
    )
    op.create_index(
        "ix_assessments_invite_workable_handoff_recovery",
        "assessments",
        ["invite_workable_handoff_status", "invite_workable_handoff_next_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assessments_invite_workable_handoff_recovery",
        table_name="assessments",
    )
    op.drop_index("ix_assessments_invite_email_recovery", table_name="assessments")
    op.drop_column("assessments", "invite_workable_note_posted_at")
    op.drop_column("assessments", "invite_workable_stage_moved_at")
    op.drop_column("assessments", "invite_workable_handoff_last_error")
    op.drop_column("assessments", "invite_workable_handoff_claimed_at")
    op.drop_column("assessments", "invite_workable_handoff_next_attempt_at")
    op.drop_column("assessments", "invite_workable_handoff_retry_count")
    op.drop_column("assessments", "invite_workable_handoff_stage")
    op.drop_column("assessments", "invite_workable_handoff_generation")
    op.drop_column("assessments", "invite_workable_handoff_status")
    op.drop_column("assessments", "invite_pipeline_transition")
    op.drop_column("assessments", "invite_email_reply_to")
    op.drop_column("assessments", "invite_email_last_error")
    op.drop_column("assessments", "invite_email_claimed_at")
    op.drop_column("assessments", "invite_email_next_attempt_at")
    op.drop_column("assessments", "invite_email_retry_count")
    op.drop_column("assessments", "invite_email_confirmed_generation")
    op.drop_column("assessments", "invite_email_send_generation")
