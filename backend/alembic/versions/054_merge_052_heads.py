"""Merge concurrent migration heads (052_add_usage_based_pricing + 052_add_background_job_runs).

Two feature branches both based their 052 migration on 051. This merge
migration unifies them so alembic can resolve a single head. No schema
changes — pure merge marker.

Revision ID: 054_merge_052_heads
Revises: 053_drop_legacy_assessment_quota, 052_add_background_job_runs
Create Date: 2026-04-30
"""

from __future__ import annotations


revision = "054_merge_052_heads"
down_revision = ("053_drop_legacy_assessment_quota", "052_add_background_job_runs")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
