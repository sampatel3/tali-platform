"""Add durable ATS application-created outbox.

Revision ID: 166_application_ingest_outbox
Revises: 165_score_job_authority
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "166_application_ingest_outbox"
down_revision = "165_score_job_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_created_outbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "score_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "paid_work_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "requires_active_agent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("parse_origin", sa.String(length=32), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "auto_reject_dispatched_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("cv_parse_dispatch_status", sa.String(length=32), nullable=True),
        sa.Column("cv_parse_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cv_parse_dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cv_parse_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cv_parse_next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cv_parse_last_error", sa.Text(), nullable=True),
        sa.Column("score_dispatch_status", sa.String(length=32), nullable=True),
        sa.Column("score_job_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["application_id"], ["candidate_applications.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["score_job_id"], ["cv_score_jobs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id"),
    )
    op.create_index(
        op.f("ix_application_created_outbox_id"),
        "application_created_outbox",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_application_created_outbox_organization_id"),
        "application_created_outbox",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_application_created_outbox_application_id"),
        "application_created_outbox",
        ["application_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_application_created_outbox_score_job_id"),
        "application_created_outbox",
        ["score_job_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_application_created_outbox_status"),
        "application_created_outbox",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_application_created_outbox_status"),
        table_name="application_created_outbox",
    )
    op.drop_index(
        op.f("ix_application_created_outbox_score_job_id"),
        table_name="application_created_outbox",
    )
    op.drop_index(
        op.f("ix_application_created_outbox_application_id"),
        table_name="application_created_outbox",
    )
    op.drop_index(
        op.f("ix_application_created_outbox_organization_id"),
        table_name="application_created_outbox",
    )
    op.drop_index(
        op.f("ix_application_created_outbox_id"),
        table_name="application_created_outbox",
    )
    op.drop_table("application_created_outbox")
