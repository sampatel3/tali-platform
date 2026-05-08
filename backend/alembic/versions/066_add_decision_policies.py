"""``decision_policies`` — versioned, deterministic verdict surface.

Adds the table the orchestrator agent calls for every reject / advance /
send-assessment verdict. One row per (organization, role) — null role_id
is the org-default. Each row is pinned to a ``rubric_revisions`` parent
so old decisions are traceable to the exact policy that produced them
(see ``RubricRevision`` for the spine).

The bootstrap data backfill itself (one ``rubric_revisions`` + one
org-default ``decision_policies`` row per existing organization) lives
in ``app.decision_policy.bootstrap`` and is invoked from this migration
so fresh installs ship with an evaluable default.

Revision ID: 066_add_decision_policies
Revises: 065_merge_chat_scope_and_hub_feedback
Create Date: 2026-05-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "066_add_decision_policies"
down_revision = "065_merge_chat_scope_and_hub_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_policies",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id"),
            nullable=True,  # null = org-default
        ),
        sa.Column(
            "revision_id",
            sa.BigInteger(),
            sa.ForeignKey("rubric_revisions.id"),
            nullable=False,
        ),
        sa.Column("policy_json", sa.JSON(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Hot lookup: "find the active policy for (org, role)". Includes
    # deactivated_at so the partial-style filter (deactivated_at IS NULL)
    # can use the index. Two indexes — one for org-default lookups, one
    # for role-specific lookups — keeps the queries straightforward.
    op.create_index(
        "ix_decision_policies_org_role_active",
        "decision_policies",
        ["organization_id", "role_id", "deactivated_at"],
    )
    op.create_index(
        "ix_decision_policies_revision",
        "decision_policies",
        ["revision_id"],
    )

    # Backfill: bootstrap an org-default policy for every existing org so
    # the engine never crashes on "no active policy" in a populated DB.
    # The bootstrap helper is idempotent — re-running the migration is
    # safe (won't double-insert).
    from app.decision_policy.bootstrap import bootstrap_all_orgs_via_connection

    bootstrap_all_orgs_via_connection(op.get_bind())


def downgrade() -> None:
    op.drop_index(
        "ix_decision_policies_revision",
        table_name="decision_policies",
    )
    op.drop_index(
        "ix_decision_policies_org_role_active",
        table_name="decision_policies",
    )
    op.drop_table("decision_policies")
