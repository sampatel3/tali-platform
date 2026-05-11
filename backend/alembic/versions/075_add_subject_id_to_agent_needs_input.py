"""Add ``subject_id`` to ``agent_needs_input``.

The cohort-planner agent calls ``ask_recruiter.open`` once per
candidate when ``auto_promote`` is off. The original idempotency key
``(organization_id, role_id, kind)`` collapses all those calls into
one row — so multiple pending ``send_assessment_approval`` requests
end up overwriting each other and only the last candidate's prompt
survives on the recruiter's card.

This migration adds a nullable ``subject_id`` column so the
idempotency key can include the application id (or any other
per-subject discriminator). NULL is the legacy "role-wide" semantic
for kinds like ``monthly_budget_missing`` where there's no subject.

Revision ID: 075_add_subject_id_to_agent_needs_input
Revises: 074_add_auto_reject_threshold_mode
Create Date: 2026-05-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "075_add_subject_id_to_agent_needs_input"
down_revision = "074_add_auto_reject_threshold_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_needs_input",
        sa.Column("subject_id", sa.BigInteger(), nullable=True),
    )
    # Lookup by (role, kind, subject) is the hot path now.
    op.create_index(
        "ix_agent_needs_input_role_kind_subject",
        "agent_needs_input",
        ["role_id", "kind", "subject_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_needs_input_role_kind_subject",
        table_name="agent_needs_input",
    )
    op.drop_column("agent_needs_input", "subject_id")
