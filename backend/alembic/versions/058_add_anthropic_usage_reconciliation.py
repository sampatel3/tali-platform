"""Add Anthropic usage reconciliation table.

Powers the daily reconciliation of internal ``usage_events`` against
Anthropic's authoritative usage + cost reports. One row per
(usage_date, anthropic_workspace_id, model). The Celery beat task
``reconcile_anthropic_usage`` populates these rows daily.

Revision ID: 058_add_anthropic_usage_reconciliation
Revises: 057_unify_role_monthly_budget
Create Date: 2026-05-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "058_add_anthropic_usage_reconciliation"
down_revision = "057_unify_role_monthly_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "anthropic_usage_reconciliations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("anthropic_workspace_id", sa.String(), nullable=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=True,
        ),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column(
            "anthropic_input_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "anthropic_output_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "anthropic_cache_read_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "anthropic_cache_creation_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "anthropic_cost_usd_micro",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "internal_input_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "internal_output_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "internal_cache_read_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "internal_cache_creation_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "internal_cost_usd_micro",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "internal_event_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("tokens_drift_pct", sa.Numeric(7, 3), nullable=True),
        sa.Column("cost_drift_pct", sa.Numeric(7, 3), nullable=True),
        sa.Column(
            "reconciled_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("metadata", sa.JSON(), nullable=True),
    )
    # Unique by (date, workspace, model) so daily reruns upsert in place.
    # ``anthropic_workspace_id`` may be NULL (default workspace) — Postgres
    # treats NULL as distinct, but we want NULL-default-workspace dedup, so
    # the upsert path uses an explicit existence check rather than ON CONFLICT.
    op.create_index(
        "ix_anthropic_recon_date_workspace_model",
        "anthropic_usage_reconciliations",
        ["usage_date", "anthropic_workspace_id", "model"],
        unique=True,
    )
    op.create_index(
        "ix_anthropic_recon_org_date",
        "anthropic_usage_reconciliations",
        ["organization_id", "usage_date"],
        unique=False,
    )
    op.create_index(
        "ix_anthropic_recon_date",
        "anthropic_usage_reconciliations",
        ["usage_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_anthropic_recon_date",
        table_name="anthropic_usage_reconciliations",
    )
    op.drop_index(
        "ix_anthropic_recon_org_date",
        table_name="anthropic_usage_reconciliations",
    )
    op.drop_index(
        "ix_anthropic_recon_date_workspace_model",
        table_name="anthropic_usage_reconciliations",
    )
    op.drop_table("anthropic_usage_reconciliations")
