"""P0: configurable pipeline stages + per-org sync_mode + application stage_kind.

ATS foundation, EXPAND step (additive, behaviour-preserving, inert unless the
ATS_CONFIGURABLE_STAGES_ENABLED flag is on):
- ``organizations.sync_mode`` — who owns the funnel (standalone / workable_primary
  / taali_primary). Backfilled from ``workable_connected``.
- ``pipeline_stages`` — per-org configurable funnel stages, replacing the
  hard-coded ``pipeline_service.PIPELINE_STAGES`` tuple. Seeded with the exact
  legacy 5 stages per org so the reader switch-over (behind the flag) is a no-op.
- ``candidate_applications.stage_kind`` — denormalized coarse category of the
  current stage. Backfilled from the legacy stage->kind mapping.

Revision ID: 151_add_pipeline_stages_sync_mode
Revises: 150_workable_writeback_flag
Create Date: 2026-07-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "151_add_pipeline_stages_sync_mode"
down_revision = "150_workable_writeback_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Per-org sync_mode (default standalone; existing Workable-connected orgs
    #    are workable_primary).
    op.add_column(
        "organizations",
        sa.Column(
            "sync_mode",
            sa.String(),
            nullable=False,
            server_default="standalone",
        ),
    )
    op.execute(
        "UPDATE organizations SET sync_mode = 'workable_primary' "
        "WHERE workable_connected IS TRUE"
    )

    # 2. Per-org configurable pipeline stages.
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
    # index=True on the PK id mirrors the codebase convention; create_table does
    # not emit it, so add it explicitly to match what create_all produces on
    # fresh DBs (keeps migrated and create_all'd schemas identical).
    op.create_index("ix_pipeline_stages_id", "pipeline_stages", ["id"])
    op.create_index(
        "ix_pipeline_stages_organization_id", "pipeline_stages", ["organization_id"]
    )
    op.create_index(
        "ix_pipeline_stages_org_position",
        "pipeline_stages",
        ["organization_id", "position"],
    )

    # 3. Seed the canonical 5 stages for every existing org (one statement;
    #    mirrors pipeline_service.PIPELINE_STAGES exactly). Idempotent against the
    #    unique (organization_id, slug) constraint via ON CONFLICT DO NOTHING.
    if op.get_bind().dialect.name == "sqlite":
        # SQLite does not accept PostgreSQL's VALUES-table column alias syntax.
        # INSERT OR IGNORE preserves the same idempotence as ON CONFLICT.
        op.execute(
            """
            INSERT OR IGNORE INTO pipeline_stages
                (organization_id, slug, name, kind, position, is_default, is_active, created_at)
            SELECT o.id, s.slug, s.name, s.kind, s.position, TRUE, TRUE, CURRENT_TIMESTAMP
            FROM organizations o
            CROSS JOIN (
                SELECT 'applied' AS slug, 'Applied' AS name, 'applied' AS kind, 0 AS position
                UNION ALL SELECT 'invited', 'Invited', 'assessment', 1
                UNION ALL SELECT 'in_assessment', 'In assessment', 'assessment', 2
                UNION ALL SELECT 'review', 'Review', 'review', 3
                UNION ALL SELECT 'advanced', 'Advanced', 'interview', 4
            ) AS s
            """
        )
    else:
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

    # 4. Denormalized stage_kind on applications, backfilled from the legacy
    #    stage->kind mapping.
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


def downgrade() -> None:
    op.drop_column("candidate_applications", "stage_kind")
    op.drop_index("ix_pipeline_stages_org_position", table_name="pipeline_stages")
    op.drop_index(
        "ix_pipeline_stages_organization_id", table_name="pipeline_stages"
    )
    op.drop_index("ix_pipeline_stages_id", table_name="pipeline_stages")
    op.drop_table("pipeline_stages")
    op.drop_column("organizations", "sync_mode")
