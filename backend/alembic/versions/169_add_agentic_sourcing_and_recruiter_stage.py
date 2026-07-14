"""Add agent-prepared outreach provenance and recruiter hiring stages.

``candidate_applications.pipeline_stage`` remains the Tali evaluation axis.
The nullable recruiter-stage fields provide a separate, provider-neutral axis
for downstream screening, interviewing, offer, and hired milestones.

Outreach campaign provenance records which campaigns were prepared by an agent
and which user approved the outbound HITL gate.  The idempotency key prevents
duplicate autonomous campaign preparation.

Revision ID: 169_agentic_sourcing_hiring
Revises: 168_bh_cred_generation
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "169_agentic_sourcing_hiring"
down_revision = "168_bh_cred_generation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_applications",
        sa.Column("recruiter_stage", sa.String(), nullable=True),
    )
    op.add_column(
        "candidate_applications",
        sa.Column("recruiter_stage_source", sa.String(), nullable=True),
    )
    op.add_column(
        "candidate_applications",
        sa.Column(
            "recruiter_stage_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_candidate_applications_recruiter_stage",
        "candidate_applications",
        ["recruiter_stage"],
        unique=False,
    )

    # Existing Advanced applications have already crossed the Tali evaluation
    # handoff. Preserve recognizable downstream provider milestones first; use
    # screening only as the conservative fallback for rows with no such signal.
    op.execute(
        sa.text(
            "WITH classified AS ("
            "  SELECT id, "
            "    lower(replace(replace(trim(coalesce("
            "      external_stage_raw, workable_stage, bullhorn_status, "
            "      external_stage_normalized, ''"
            "    )), '-', '_'), ' ', '_')) AS stage_key "
            "  FROM candidate_applications "
            "  WHERE pipeline_stage = 'advanced'"
            ") "
            "UPDATE candidate_applications AS app "
            "SET recruiter_stage = CASE "
            "      WHEN classified.stage_key IN ("
            "        'hired', 'placed', 'placement', 'confirmed'"
            "      ) THEN 'hired' "
            "      WHEN classified.stage_key IN ("
            "        'offer', 'offer_extended', 'offer_accepted'"
            "      ) THEN 'offer' "
            "      WHEN classified.stage_key IN ("
            "        'interview', 'interviewing', 'technical', "
            "        'technical_interview', 'final_interview', 'onsite', "
            "        'presentation', 'assessment', 'interview_scheduled', "
            "        'interviewscheduled'"
            "      ) THEN 'interviewing' "
            "      ELSE 'screening' "
            "    END, "
            "    recruiter_stage_source = CASE "
            "      WHEN classified.stage_key IN ("
            "        'screening', 'screen', 'phone_screen', 'phone_interview', "
            "        'first_stage', 'interview', 'interviewing', 'technical', "
            "        'technical_interview', 'final_interview', 'onsite', "
            "        'presentation', 'assessment', 'interview_scheduled', "
            "        'interviewscheduled', 'offer', 'offer_extended', "
            "        'offer_accepted', 'hired', 'placed', 'placement', 'confirmed'"
            "      ) THEN 'sync' ELSE 'migration' END, "
            "    recruiter_stage_updated_at = "
            "      COALESCE(app.pipeline_stage_updated_at, CURRENT_TIMESTAMP) "
            "FROM classified WHERE app.id = classified.id"
        )
    )

    op.add_column(
        "outreach_campaigns",
        sa.Column(
            "origin",
            sa.String(),
            nullable=False,
            server_default="manual",
        ),
    )
    op.add_column(
        "outreach_campaigns",
        sa.Column("prepared_by_agent_run_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "outreach_campaigns",
        sa.Column("idempotency_key", sa.String(), nullable=True),
    )
    op.add_column(
        "outreach_campaigns",
        sa.Column("approved_by_user_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "outreach_campaigns",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "outreach_campaigns",
        sa.Column("destination_url", sa.String(), nullable=True),
    )
    op.add_column(
        "outreach_campaigns",
        sa.Column("destination_provider", sa.String(), nullable=True),
    )
    op.add_column(
        "outreach_campaigns",
        sa.Column(
            "draft_generation_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "outreach_campaigns",
        sa.Column(
            "review_revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_foreign_key(
        "fk_outreach_campaigns_prepared_by_agent_run_id_agent_runs",
        "outreach_campaigns",
        "agent_runs",
        ["prepared_by_agent_run_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_outreach_campaigns_approved_by_user_id_users",
        "outreach_campaigns",
        "users",
        ["approved_by_user_id"],
        ["id"],
    )
    op.create_index(
        "ix_outreach_campaigns_prepared_by_agent_run_id",
        "outreach_campaigns",
        ["prepared_by_agent_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_outreach_campaigns_idempotency_key",
        "outreach_campaigns",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outreach_campaigns_idempotency_key",
        table_name="outreach_campaigns",
    )
    op.drop_index(
        "ix_outreach_campaigns_prepared_by_agent_run_id",
        table_name="outreach_campaigns",
    )
    op.drop_constraint(
        "fk_outreach_campaigns_approved_by_user_id_users",
        "outreach_campaigns",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_outreach_campaigns_prepared_by_agent_run_id_agent_runs",
        "outreach_campaigns",
        type_="foreignkey",
    )
    op.drop_column("outreach_campaigns", "approved_at")
    op.drop_column("outreach_campaigns", "review_revision")
    op.drop_column("outreach_campaigns", "draft_generation_attempts")
    op.drop_column("outreach_campaigns", "destination_provider")
    op.drop_column("outreach_campaigns", "destination_url")
    op.drop_column("outreach_campaigns", "approved_by_user_id")
    op.drop_column("outreach_campaigns", "idempotency_key")
    op.drop_column("outreach_campaigns", "prepared_by_agent_run_id")
    op.drop_column("outreach_campaigns", "origin")

    op.drop_index(
        "ix_candidate_applications_recruiter_stage",
        table_name="candidate_applications",
    )
    op.drop_column("candidate_applications", "recruiter_stage_updated_at")
    op.drop_column("candidate_applications", "recruiter_stage_source")
    op.drop_column("candidate_applications", "recruiter_stage")
