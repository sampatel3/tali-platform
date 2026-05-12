"""``capability_flags`` + ``agent_decisions.active_capabilities`` — v10 capability flag substrate.

Companion to ``capability_flags_addendum.md``.

Two changes:

1. Create ``capability_flags`` — hot-reloaded (~30s) source of truth for
   which v10 capabilities are active for which orgs / roles / cohorts /
   percentage rollouts. PK is (capability, org_id) where org_id NULL is
   the global default.

2. Add ``agent_decisions.active_capabilities`` — a JSON snapshot of
   which capabilities were active for that specific decision context.
   The audit query ``WHERE active_capabilities @> '{"federated_graph":
   true}'`` answers "which decisions did the federated graph touch?"
   without reaching for git history.

Revision ID: 081_add_capability_flags
Revises: 080_add_graph_writeback_queue
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "081_add_capability_flags"
down_revision = "080_add_graph_writeback_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capability_flags",
        sa.Column("capability", sa.String(length=64), nullable=False),
        # NULL = global default for capability. Org-scoped overrides
        # come in as additional rows with the same capability + non-null
        # org id. The flag client resolves by preferring the org row.
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        # The scope blob is matched at flag-eval time. Shape (mirrors
        # ``FlagScope`` in app.capabilities.flags):
        #   role_ids: list[int] | None
        #   role_families: list[str] | None
        #   percentage: float (0..100)
        #   cohort_tags: list[str] | None
        #   starts_at / ends_at: ISO datetimes | None
        sa.Column("scope_json", sa.JSON(), nullable=False),
        # ``requires`` is a list of other capabilities that must ALSO
        # be active in the same context. Stored as JSON for portability
        # (Postgres TEXT[] is fine in prod, but the SQLite test DB
        # doesn't support arrays).
        sa.Column("requires_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("rolled_out_by", sa.String(length=128), nullable=False),
        sa.Column(
            "rolled_out_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("rollback_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("capability", "organization_id", name="pk_capability_flags"),
    )
    op.create_index(
        "ix_capability_flags_by_org",
        "capability_flags",
        ["organization_id"],
    )

    # The audit snapshot — what was active when this decision was made.
    # Default '{}' so every existing row reads as "v1 era, no v10
    # capabilities". We deliberately don't backfill historical rows.
    op.add_column(
        "agent_decisions",
        sa.Column(
            "active_capabilities",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_decisions", "active_capabilities")
    op.drop_index("ix_capability_flags_by_org", table_name="capability_flags")
    op.drop_table("capability_flags")
