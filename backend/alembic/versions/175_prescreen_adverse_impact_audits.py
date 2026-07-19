"""Persist pre-screen aggregate audits and durable batch fan-out items.

Revision ID: 175_prescreen_adverse_impact_audits
Revises: 174_async_dispatch_recovery
Create Date: 2026-07-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "175_prescreen_adverse_impact_audits"
down_revision = "174_async_dispatch_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prescreen_adverse_impact_audits",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comparisons", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="voluntary_eeo",
        ),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("violations_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "window_start",
            "window_end",
            name="uq_prescreen_impact_audit_org_window",
        ),
    )
    op.create_index(
        "ix_prescreen_adverse_impact_audits_organization_id",
        "prescreen_adverse_impact_audits",
        ["organization_id"],
    )
    op.create_index(
        "ix_prescreen_impact_audit_org_created",
        "prescreen_adverse_impact_audits",
        ["organization_id", "created_at"],
    )
    op.create_table(
        "prescreen_batch_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="queued"
        ),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_token", sa.String(length=36), nullable=True),
        sa.Column(
            "dispatch_lease_until", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "dispatch_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_attempt_token", sa.String(length=36), nullable=True),
        sa.Column(
            "provider_attempt_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["application_id"], ["candidate_applications.id"]
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"]
        ),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.ForeignKeyConstraint(
            ["run_id"], ["background_job_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "application_id", name="uq_prescreen_batch_item_run_app"
        ),
    )
    op.create_index(
        "ix_prescreen_batch_items_application_id",
        "prescreen_batch_items",
        ["application_id"],
    )
    op.create_index(
        "ix_prescreen_batch_items_organization_id",
        "prescreen_batch_items",
        ["organization_id"],
    )
    op.create_index(
        "ix_prescreen_batch_items_role_id",
        "prescreen_batch_items",
        ["role_id"],
    )
    op.create_index(
        "ix_prescreen_batch_items_run_status",
        "prescreen_batch_items",
        ["run_id", "status"],
    )
    op.create_index(
        "ix_prescreen_batch_items_recovery",
        "prescreen_batch_items",
        ["status", "dispatch_lease_until"],
    )
    op.create_index(
        "ix_prescreen_batch_items_attempt_recovery",
        "prescreen_batch_items",
        ["status", "provider_attempt_started_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_prescreen_batch_items_attempt_recovery",
        table_name="prescreen_batch_items",
    )
    op.drop_index(
        "ix_prescreen_batch_items_recovery", table_name="prescreen_batch_items"
    )
    op.drop_index(
        "ix_prescreen_batch_items_run_status", table_name="prescreen_batch_items"
    )
    op.drop_index(
        "ix_prescreen_batch_items_role_id", table_name="prescreen_batch_items"
    )
    op.drop_index(
        "ix_prescreen_batch_items_organization_id",
        table_name="prescreen_batch_items",
    )
    op.drop_index(
        "ix_prescreen_batch_items_application_id",
        table_name="prescreen_batch_items",
    )
    op.drop_table("prescreen_batch_items")
    op.drop_index(
        "ix_prescreen_impact_audit_org_created",
        table_name="prescreen_adverse_impact_audits",
    )
    op.drop_index(
        "ix_prescreen_adverse_impact_audits_organization_id",
        table_name="prescreen_adverse_impact_audits",
    )
    op.drop_table("prescreen_adverse_impact_audits")
