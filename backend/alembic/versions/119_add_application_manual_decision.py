"""Recruiter-recorded manual decision on the application.

``candidate_applications.manual_decision`` stores a recruiter's manually
recorded decision (advance/hold/reject + rationale, confidence, next steps) with
a draft/submitted lifecycle, version, author stamp and change history. It lets a
recruiter record/update a decision for a candidate that has no assessment linked
(e.g. rejected at CV stage); the assessment-backed equivalent already lives on
``assessments.manual_evaluation`` (revision 011).

Revision ID: 119_add_application_manual_decision
Revises: 118_drop_candidate_feedback
Create Date: 2026-06-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "119_add_application_manual_decision"
down_revision = "118_drop_candidate_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_applications",
        sa.Column("manual_decision", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("candidate_applications", "manual_decision")
