"""Compatibility shim for legacy production Alembic revision.

Revision ID: 034_add_application_score_cache_columns
Revises: 033_add_pipeline_query_indexes
Create Date: 2026-04-24

This revision previously existed in production and is still recorded in the
alembic_version table there. The current application no longer relies on those
cache columns, but Railway deploys must be able to traverse this revision ID
before applying newer migrations.
"""


revision = "034_add_application_score_cache_columns"
down_revision = "033_add_pipeline_query_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op compatibility migration."""


def downgrade() -> None:
    """No-op compatibility migration."""
