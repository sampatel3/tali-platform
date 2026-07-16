"""Gate stale CV-score dispatch on durable recruiter approval.

Revision ID: 178_cv_score_dispatch_approval
Revises: 177_agent_chat_turn_role_version
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "178_cv_score_dispatch_approval"
down_revision = "177_agent_chat_turn_role_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cv_score_jobs",
        sa.Column(
            "dispatch_approved",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("cv_score_jobs", "dispatch_approved")
