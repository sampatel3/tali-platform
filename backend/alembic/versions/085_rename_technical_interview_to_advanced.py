"""Rename ``technical_interview`` pipeline_stage to ``advanced``.

Tali's 5th pipeline stage is now a single ``advanced`` bucket covering
all post-handover Workable stages (phone screen, interview, technical
interview, final interview, offer, hired). The precise Workable stage
stays on ``workable_stage`` — the Tali bucket just collapses the
post-handover set.

This migration renames the literal value in two columns:
  * ``candidate_applications.pipeline_stage``
  * ``candidate_application_events.from_stage`` / ``to_stage``

Revision ID: 085_rename_ti_to_advanced
Revises: 084_add_token_spend
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op


revision = "085_rename_ti_to_advanced"
down_revision = "084_add_token_spend"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE candidate_applications "
        "SET pipeline_stage = 'advanced' "
        "WHERE pipeline_stage = 'technical_interview'"
    )
    op.execute(
        "UPDATE candidate_application_events "
        "SET from_stage = 'advanced' "
        "WHERE from_stage = 'technical_interview'"
    )
    op.execute(
        "UPDATE candidate_application_events "
        "SET to_stage = 'advanced' "
        "WHERE to_stage = 'technical_interview'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE candidate_applications "
        "SET pipeline_stage = 'technical_interview' "
        "WHERE pipeline_stage = 'advanced'"
    )
    op.execute(
        "UPDATE candidate_application_events "
        "SET from_stage = 'technical_interview' "
        "WHERE from_stage = 'advanced'"
    )
    op.execute(
        "UPDATE candidate_application_events "
        "SET to_stage = 'technical_interview' "
        "WHERE to_stage = 'advanced'"
    )
