"""P0: source attribution + structured disposition (disqualification) reasons.

Additive ATS foundation:
- candidate_applications: source_strategy / source_name / credited_to_user_id
  (2-level source attribution) + disposition_reason_id / disposition_category
  (structured reject/withdraw reason, distinct from free-text auto_reject_reason).
- disqualification_reasons: per-org configurable reason catalog, seeded with a
  canonical default set per org.

All additive + nullable; no behaviour change.

Revision ID: 121_add_source_attribution_and_dispositions
Revises: 120_add_pipeline_stages_sync_mode
Create Date: 2026-06-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "121_add_source_attribution_and_dispositions"
down_revision = "120_add_pipeline_stages_sync_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Per-org disqualification reason catalog (created first so the FK column
    #    below can reference it).
    op.create_table(
        "disqualification_reasons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "organization_id", "label", name="uq_disqualification_reason_org_label"
        ),
    )
    op.create_index(
        "ix_disqualification_reasons_id", "disqualification_reasons", ["id"]
    )
    op.create_index(
        "ix_disqualification_reasons_organization_id",
        "disqualification_reasons",
        ["organization_id"],
    )
    op.create_index(
        "ix_disqualification_reasons_org_position",
        "disqualification_reasons",
        ["organization_id", "position"],
    )

    # 2. Source attribution + structured disposition columns on applications.
    op.add_column(
        "candidate_applications",
        sa.Column("source_strategy", sa.String(), nullable=True),
    )
    op.add_column(
        "candidate_applications",
        sa.Column("source_name", sa.String(), nullable=True),
    )
    op.add_column(
        "candidate_applications",
        sa.Column(
            "credited_to_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "candidate_applications",
        sa.Column(
            "disposition_reason_id",
            sa.Integer(),
            sa.ForeignKey("disqualification_reasons.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "candidate_applications",
        sa.Column("disposition_category", sa.String(), nullable=True),
    )

    # 3. Seed the canonical reason set for every existing org.
    op.execute(
        """
        INSERT INTO disqualification_reasons
            (organization_id, label, category, position, is_default, is_active, created_at)
        SELECT o.id, r.label, r.category, r.position, TRUE, TRUE, now()
        FROM organizations o
        CROSS JOIN (VALUES
            ('Underqualified',            'we_rejected',   0),
            ('Missing required skills',   'we_rejected',   1),
            ('Not enough experience',     'we_rejected',   2),
            ('Failed assessment',         'we_rejected',   3),
            ('Better candidate selected', 'we_rejected',   4),
            ('Position filled',           'we_rejected',   5),
            ('Candidate withdrew',        'they_withdrew', 6),
            ('Declined offer',            'they_withdrew', 7),
            ('Compensation expectations', 'they_withdrew', 8),
            ('Unresponsive',              'they_withdrew', 9),
            ('Other',                     'other',         10)
        ) AS r(label, category, position)
        ON CONFLICT (organization_id, label) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_column("candidate_applications", "disposition_category")
    op.drop_column("candidate_applications", "disposition_reason_id")
    op.drop_column("candidate_applications", "credited_to_user_id")
    op.drop_column("candidate_applications", "source_name")
    op.drop_column("candidate_applications", "source_strategy")
    op.drop_index(
        "ix_disqualification_reasons_org_position",
        table_name="disqualification_reasons",
    )
    op.drop_index(
        "ix_disqualification_reasons_organization_id",
        table_name="disqualification_reasons",
    )
    op.drop_index(
        "ix_disqualification_reasons_id", table_name="disqualification_reasons"
    )
    op.drop_table("disqualification_reasons")
