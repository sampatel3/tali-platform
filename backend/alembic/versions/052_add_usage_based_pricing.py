"""Add usage-based pricing primitives.

Replaces the Lemon-Squeezy fixed-price model (25 AED per assessment) with
usage metering. Adds:

1. `usage_events` — append-only log of every billable Claude call
2. `usage_grants` — idempotent record of free/promo/topup credit grants
3. `organizations`:
   - Switch `credits_balance` Integer → BigInteger (micro-credits, $0.000001 each)
   - Add Anthropic Workspace key columns (lazy per-org provisioning)
4. `billing_credit_ledger`:
   - Switch `delta` and `balance_after` Integer → BigInteger (micro-credits)

Note: there are no existing paying customers, so no balance-conversion is
needed. Existing `credits_balance` rows (whole-credit Lemon counts) are
preserved as-is — but they're effectively zero/unused at this point.

Revision ID: 052_add_usage_based_pricing
Revises: 051_add_pre_screen_run_at
Create Date: 2026-04-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "052_add_usage_based_pricing"
down_revision = "051_add_pre_screen_run_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- usage_events ------------------------------------------------------
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("feature", sa.String(), nullable=False),
        sa.Column("entity_id", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd_micro", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("markup_multiplier", sa.Numeric(4, 2), nullable=False),
        sa.Column("credits_charged", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cache_hit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("metadata", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_usage_events_id", "usage_events", ["id"], unique=False
    )
    op.create_index(
        "ix_usage_events_org_created",
        "usage_events",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_usage_events_feature_created",
        "usage_events",
        ["feature", "created_at"],
        unique=False,
    )

    # --- usage_grants ------------------------------------------------------
    op.create_table(
        "usage_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("grant_type", sa.String(), nullable=False),
        sa.Column("credits_granted", sa.BigInteger(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("external_ref", sa.String(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_usage_grants_id", "usage_grants", ["id"], unique=False
    )
    op.create_index(
        "ix_usage_grants_organization_id",
        "usage_grants",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_usage_grants_external_ref",
        "usage_grants",
        ["external_ref"],
        unique=True,
    )

    # --- organizations: add anthropic workspace columns -------------------
    op.add_column(
        "organizations",
        sa.Column("anthropic_workspace_id", sa.String(), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("anthropic_workspace_key_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "anthropic_workspace_provisioning_failed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # --- widen integer columns to BigInteger ------------------------------
    # Postgres can ALTER TYPE in place; SQLite (used in tests) is permissive.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column(
            "organizations",
            "credits_balance",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
        )
        op.alter_column(
            "billing_credit_ledger",
            "delta",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )
        op.alter_column(
            "billing_credit_ledger",
            "balance_after",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column(
            "billing_credit_ledger",
            "balance_after",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
        )
        op.alter_column(
            "billing_credit_ledger",
            "delta",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
        )
        op.alter_column(
            "organizations",
            "credits_balance",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=True,
        )

    op.drop_column("organizations", "anthropic_workspace_provisioning_failed_at")
    op.drop_column("organizations", "anthropic_workspace_key_encrypted")
    op.drop_column("organizations", "anthropic_workspace_id")

    op.drop_index("ix_usage_grants_external_ref", table_name="usage_grants")
    op.drop_index("ix_usage_grants_organization_id", table_name="usage_grants")
    op.drop_index("ix_usage_grants_id", table_name="usage_grants")
    op.drop_table("usage_grants")

    op.drop_index("ix_usage_events_feature_created", table_name="usage_events")
    op.drop_index("ix_usage_events_org_created", table_name="usage_events")
    op.drop_index("ix_usage_events_id", table_name="usage_events")
    op.drop_table("usage_events")
