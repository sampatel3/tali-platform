"""Add ``prescreen_calibration_samples`` — training data for the pre-screen
score calibrator.

A backend-only job shadow-scores a random sample of pre-screen rejects with
full cv_match and records the (cheap pre-screen score → full score) pair here.
These pairs train a calibrator that corrects the cheap gate's systematic
mis-prediction (reject inference). The table is never read by recruiter-facing
views.

Revision ID: 098_prescreen_calibration_samples
Revises: 097_add_decision_type_index
Create Date: 2026-05-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "098_prescreen_calibration_samples"
down_revision = "097_add_decision_type_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prescreen_calibration_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("candidate_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pre_screen_score", sa.Float(), nullable=True),
        sa.Column("full_cv_match_score", sa.Float(), nullable=True),
        sa.Column("full_recommendation", sa.String(), nullable=True),
        sa.Column("scoring_status", sa.String(), nullable=True),
        sa.Column(
            "sampled_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_prescreen_calibration_samples_organization_id",
        "prescreen_calibration_samples",
        ["organization_id"],
    )
    op.create_index(
        "ix_prescreen_calibration_samples_role_id",
        "prescreen_calibration_samples",
        ["role_id"],
    )
    op.create_index(
        "ix_prescreen_calibration_samples_application_id",
        "prescreen_calibration_samples",
        ["application_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_prescreen_calibration_samples_application_id", table_name="prescreen_calibration_samples")
    op.drop_index("ix_prescreen_calibration_samples_role_id", table_name="prescreen_calibration_samples")
    op.drop_index("ix_prescreen_calibration_samples_organization_id", table_name="prescreen_calibration_samples")
    op.drop_table("prescreen_calibration_samples")
