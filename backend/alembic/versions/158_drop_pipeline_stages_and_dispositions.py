"""Remove configurable pipeline stages + disqualification-reason catalog.

The ATS owns the pipeline stages and the disposition (reject/withdraw) reasons.
Tali maintaining editable copies created two systems of record, so the
complementary layer is removed. Both features were flag-off / dark in prod
(ATS_CONFIGURABLE_STAGES_ENABLED never flipped), so the seeded per-org rows are
disposable seed data and the dropped application columns are all-NULL — a clean
forward drop, no data migration.

Forward DROP:
- ``candidate_applications.disposition_reason_id`` (FK -> disqualification_reasons)
- ``candidate_applications.disposition_category``
- ``candidate_applications.stage_kind`` (only the flag-off config path ever
  computed a kind; nothing live reads this column)
- ``disqualification_reasons`` table
- ``pipeline_stages`` table

KEPT (added alongside these in migrations 151/152, NOT part of this removal):
``organizations.sync_mode`` and ``candidate_applications.source_strategy /
source_name / credited_to_user_id``.

Revision ID: 158_drop_pipeline_stages_and_dispositions
Revises: 157_drop_offers
Create Date: 2026-07-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "158_drop_pipeline_stages_and_dispositions"
down_revision = "157_drop_offers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop the disposition columns on applications (disposition_reason_id
    #    carries the FK to disqualification_reasons, so it must go before the
    #    table). All-NULL in prod (only the flag-off apply path wrote them).
    if op.get_bind().dialect.name == "sqlite":
        # SQLite cannot drop a column that still participates in a foreign key.
        # Rebuild once, explicitly removing the FK and all three retired fields.
        with op.batch_alter_table("candidate_applications") as batch_op:
            batch_op.drop_constraint(
                "fk_candidate_applications_disposition_reason_id_disqualification_reasons",
                type_="foreignkey",
            )
            batch_op.drop_column("disposition_category")
            batch_op.drop_column("disposition_reason_id")
            batch_op.drop_column("stage_kind")
    else:
        op.drop_column("candidate_applications", "disposition_category")
        op.drop_column("candidate_applications", "disposition_reason_id")
        # 2. Drop the denormalized stage_kind column (only the flag-off
        #    configurable path computed a kind; nothing live reads the column).
        op.drop_column("candidate_applications", "stage_kind")

    # 3. Drop the disqualification-reason catalog.
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

    # 4. Drop the per-org configurable pipeline stages.
    op.drop_index("ix_pipeline_stages_org_position", table_name="pipeline_stages")
    op.drop_index(
        "ix_pipeline_stages_organization_id", table_name="pipeline_stages"
    )
    op.drop_index("ix_pipeline_stages_id", table_name="pipeline_stages")
    op.drop_table("pipeline_stages")


def downgrade() -> None:
    # Recreate everything dropped above (schema mirrors migrations 151 + 152,
    # minus sync_mode and the source_* columns which were never dropped). The
    # per-org seed rows and column backfills are reinstated so the schema is
    # identical to pre-drop.

    # 1. pipeline_stages table + indexes + seed.
    op.create_table(
        "pipeline_stages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "organization_id", "slug", name="uq_pipeline_stage_org_slug"
        ),
    )
    op.create_index("ix_pipeline_stages_id", "pipeline_stages", ["id"])
    op.create_index(
        "ix_pipeline_stages_organization_id", "pipeline_stages", ["organization_id"]
    )
    op.create_index(
        "ix_pipeline_stages_org_position",
        "pipeline_stages",
        ["organization_id", "position"],
    )
    op.execute(
        """
        INSERT INTO pipeline_stages
            (organization_id, slug, name, kind, position, is_default, is_active, created_at)
        SELECT o.id, s.slug, s.name, s.kind, s.position, TRUE, TRUE, now()
        FROM organizations o
        CROSS JOIN (VALUES
            ('applied',       'Applied',       'applied',    0),
            ('invited',       'Invited',       'assessment', 1),
            ('in_assessment', 'In assessment', 'assessment', 2),
            ('review',        'Review',        'review',     3),
            ('advanced',      'Advanced',      'interview',  4)
        ) AS s(slug, name, kind, position)
        ON CONFLICT (organization_id, slug) DO NOTHING
        """
    )

    # 2. disqualification_reasons table + indexes + seed.
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

    # 3. Re-add the application columns (stage_kind, then the disposition columns
    #    whose FK now has a target again) + their backfills.
    op.add_column(
        "candidate_applications",
        sa.Column("stage_kind", sa.String(), nullable=True),
    )
    op.execute(
        """
        UPDATE candidate_applications SET stage_kind = CASE pipeline_stage
            WHEN 'applied'       THEN 'applied'
            WHEN 'invited'       THEN 'assessment'
            WHEN 'in_assessment' THEN 'assessment'
            WHEN 'review'        THEN 'review'
            WHEN 'advanced'      THEN 'interview'
            ELSE NULL
        END
        WHERE stage_kind IS NULL
        """
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
