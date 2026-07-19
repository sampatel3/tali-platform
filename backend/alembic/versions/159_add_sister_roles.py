"""Add coupled sister roles and persistent alternate evaluations.

Revision ID: 159_add_sister_roles
Revises: 158_drop_pipeline_stages_and_dispositions
Create Date: 2026-07-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "159_add_sister_roles"
down_revision = "158_drop_pipeline_stages_and_dispositions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column("role_kind", sa.String(length=16), nullable=False, server_default="standard"),
    )
    if op.get_bind().dialect.name == "sqlite":
        op.add_column(
            "roles", sa.Column("ats_owner_role_id", sa.Integer(), nullable=True)
        )
        with op.batch_alter_table("roles") as batch_op:
            batch_op.create_foreign_key(
                "fk_roles_ats_owner_role_id_roles",
                "roles",
                ["ats_owner_role_id"],
                ["id"],
                ondelete="CASCADE",
            )
    else:
        op.add_column(
            "roles",
            sa.Column(
                "ats_owner_role_id",
                sa.Integer(),
                sa.ForeignKey("roles.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
    op.create_index("ix_roles_role_kind", "roles", ["role_kind"])
    op.create_index("ix_roles_ats_owner_role_id", "roles", ["ats_owner_role_id"])

    op.create_table(
        "sister_role_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id", sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "role_id", sa.Integer(),
            sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "source_application_id", sa.Integer(),
            sa.ForeignKey("candidate_applications.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("spec_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("cv_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("role_fit_score", sa.Float(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("history", sa.JSON(), nullable=True),
        sa.Column("model_version", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("trace_id", sa.String(length=100), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "role_id", "source_application_id",
            name="uq_sister_evaluations_role_application",
        ),
    )
    op.create_index("ix_sister_role_evaluations_id", "sister_role_evaluations", ["id"])
    op.create_index(
        "ix_sister_role_evaluations_organization_id",
        "sister_role_evaluations", ["organization_id"],
    )
    op.create_index("ix_sister_role_evaluations_role_id", "sister_role_evaluations", ["role_id"])
    op.create_index(
        "ix_sister_role_evaluations_source_application_id",
        "sister_role_evaluations", ["source_application_id"],
    )
    op.create_index(
        "ix_sister_evaluations_role_status",
        "sister_role_evaluations", ["role_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("sister_role_evaluations")
    op.drop_index("ix_roles_ats_owner_role_id", table_name="roles")
    op.drop_index("ix_roles_role_kind", table_name="roles")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("roles") as batch_op:
            batch_op.drop_constraint(
                "fk_roles_ats_owner_role_id_roles", type_="foreignkey"
            )
            batch_op.drop_column("ats_owner_role_id")
    else:
        op.drop_column("roles", "ats_owner_role_id")
    op.drop_column("roles", "role_kind")
