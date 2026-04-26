"""Drop the per-org recruiter_workflow_v2_enabled flag.

Workflow v2 is the only recruiter workflow now. The legacy v1 candidate
pages (CandidatesPageContent and friends) and the per-org rollout switch
were both deleted; git history covers rollback if ever needed.

Revision ID: 042_drop_recruiter_workflow_v2_enabled
Revises: 041_add_cv_score_cache_and_jobs
Create Date: 2026-04-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "042_drop_recruiter_workflow_v2_enabled"
down_revision = "041_add_cv_score_cache_and_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("recruiter_workflow_v2_enabled")


def downgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(
            sa.Column(
                "recruiter_workflow_v2_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )
