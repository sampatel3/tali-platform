"""First-view timestamp for the candidate assessment preview page.

Closes the funnel blind spot between "opened the invite email" and
"started the assessment": the welcome/preview page was served without
recording anything, so opened-but-never-previewed and previewed-but-
never-started candidates were indistinguishable.

Adds:
  * ``assessments.preview_viewed_at`` — DateTime(tz), nullable; stamped
    on the FIRST hit of the token preview route only.

Revision ID: 136_add_assessment_preview_viewed_at
Revises: 135_add_anthropic_batch_jobs
Create Date: 2026-07-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "136_add_assessment_preview_viewed_at"
down_revision = "135_add_anthropic_batch_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assessments",
        sa.Column("preview_viewed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assessments", "preview_viewed_at")
