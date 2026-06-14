"""Assessment invite email-delivery tracking (Resend webhook).

Adds columns so we can correlate Resend delivery events back to the
assessment and surface delivered/opened/bounced state in the recruiter's
invited-candidate tracker:

- ``invite_email_id``      — Resend message id captured at send time (indexed
                             so the webhook can look the row up by it).
- ``invite_email_status``  — latest lifecycle state (sent/delivered/opened/
                             clicked/bounced/complained).
- ``invite_delivered_at`` / ``invite_opened_at`` / ``invite_bounced_at`` —
  event timestamps.

Revision ID: 117_add_assessment_invite_email_tracking
Revises: 116_add_workable_stage_local_write_at
Create Date: 2026-06-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "117_add_assessment_invite_email_tracking"
down_revision = "116_add_workable_stage_local_write_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assessments", sa.Column("invite_email_id", sa.String(), nullable=True))
    op.add_column("assessments", sa.Column("invite_email_status", sa.String(), nullable=True))
    op.add_column(
        "assessments", sa.Column("invite_delivered_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "assessments", sa.Column("invite_opened_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "assessments", sa.Column("invite_bounced_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_assessments_invite_email_id", "assessments", ["invite_email_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_assessments_invite_email_id", table_name="assessments")
    op.drop_column("assessments", "invite_bounced_at")
    op.drop_column("assessments", "invite_opened_at")
    op.drop_column("assessments", "invite_delivered_at")
    op.drop_column("assessments", "invite_email_status")
    op.drop_column("assessments", "invite_email_id")
