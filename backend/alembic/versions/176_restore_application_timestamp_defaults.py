"""Restore server defaults for required application transition timestamps.

Revision ID: 176_restore_application_timestamp_defaults
Revises: 175_prescreen_adverse_impact_audits
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "176_restore_application_timestamp_defaults"
down_revision = "175_prescreen_adverse_impact_audits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 032 backfilled both columns and made them NOT NULL, but removed
    # the temporary defaults. The ORM contract has always declared
    # ``server_default=now()`` and therefore omits both values on an ordinary
    # insert. PostgreSQL correctly rejected that insert while SQLite supplied
    # its test-side defaults, hiding the schema/model mismatch.
    op.alter_column(
        "candidate_applications",
        "pipeline_stage_updated_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        server_default=sa.func.now(),
    )
    op.alter_column(
        "candidate_applications",
        "application_outcome_updated_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        server_default=sa.func.now(),
    )


def downgrade() -> None:
    op.alter_column(
        "candidate_applications",
        "application_outcome_updated_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        server_default=None,
    )
    op.alter_column(
        "candidate_applications",
        "pipeline_stage_updated_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        server_default=None,
    )
