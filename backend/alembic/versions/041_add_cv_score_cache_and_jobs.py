"""Add cv_score_cache and cv_score_jobs tables.

Backs the async + cached CV scoring path. Existing CV match results stay on
``candidate_applications.cv_match_score`` / ``cv_match_details``; the new
tables add a content-hash cache to short-circuit repeat Claude calls and a
per-application job log to surface ``score_status`` in the listing API.

Revision ID: 041_add_cv_score_cache_and_jobs
Revises: 040_add_role_criteria
Create Date: 2026-04-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "041_add_cv_score_cache_and_jobs"
down_revision = "040_add_role_criteria"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cv_score_cache",
        sa.Column("cache_key", sa.String, primary_key=True),
        sa.Column("prompt_version", sa.String, nullable=False),
        sa.Column("model", sa.String, nullable=False),
        sa.Column("score_100", sa.Float, nullable=True),
        sa.Column("result", sa.JSON, nullable=False),
        sa.Column("hit_count", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column(
            "last_hit_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_cv_score_cache_prompt_version", "cv_score_cache", ["prompt_version"]
    )

    op.create_table(
        "cv_score_jobs",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column(
            "application_id",
            sa.Integer,
            sa.ForeignKey("candidate_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            sa.Integer,
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String,
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("cache_key", sa.String, nullable=True),
        sa.Column("prompt_version", sa.String, nullable=True),
        sa.Column("model", sa.String, nullable=True),
        sa.Column("cache_hit", sa.String, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("celery_task_id", sa.String, nullable=True),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_cv_score_jobs_application_id", "cv_score_jobs", ["application_id"])
    op.create_index("ix_cv_score_jobs_role_id", "cv_score_jobs", ["role_id"])
    op.create_index("ix_cv_score_jobs_status", "cv_score_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_cv_score_jobs_status", table_name="cv_score_jobs")
    op.drop_index("ix_cv_score_jobs_role_id", table_name="cv_score_jobs")
    op.drop_index("ix_cv_score_jobs_application_id", table_name="cv_score_jobs")
    op.drop_table("cv_score_jobs")
    op.drop_index("ix_cv_score_cache_prompt_version", table_name="cv_score_cache")
    op.drop_table("cv_score_cache")
