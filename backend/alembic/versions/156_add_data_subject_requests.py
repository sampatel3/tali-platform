"""GDPR-style data-subject requests (access / erasure) log.

Additive: ``data_subject_requests`` — the durable compliance record of who asked
to access or erase their data, and when it was fulfilled. It outlives an erased
candidate row (the log is the evidence that the erasure happened).

Revision ID: 156_add_data_subject_requests
Revises: 155_add_eeo_responses
Create Date: 2026-07-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "156_add_data_subject_requests"
down_revision = "155_add_eeo_responses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_subject_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id", sa.Integer(), sa.ForeignKey("candidates.id"), nullable=True
        ),
        sa.Column("subject_email", sa.String(), nullable=True),
        sa.Column("request_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_data_subject_requests_org", "data_subject_requests", ["organization_id"]
    )
    op.create_index(
        "ix_data_subject_requests_email", "data_subject_requests", ["subject_email"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_data_subject_requests_email", table_name="data_subject_requests"
    )
    op.drop_index(
        "ix_data_subject_requests_org", table_name="data_subject_requests"
    )
    op.drop_table("data_subject_requests")
