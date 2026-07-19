"""Add durable, secret-free Workable assessment-result delivery receipts.

Revision ID: 184_assessment_result_delivery
Revises: 183_preserve_related_role_history
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "184_assessment_result_delivery"
down_revision = "183_preserve_related_role_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assessments",
        sa.Column("workable_result_delivery_status", sa.String(), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column("workable_result_delivery_receipt", sa.JSON(), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column(
            "workable_result_delivery_next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "assessments",
        sa.Column(
            "workable_result_delivery_claimed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_assessments_workable_result_delivery_recovery",
        "assessments",
        [
            "workable_result_delivery_status",
            "workable_result_delivery_next_attempt_at",
        ],
        unique=False,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Revision 184 is intentionally irreversible: assessment delivery "
        "receipts are operational evidence and must not be deleted."
    )
