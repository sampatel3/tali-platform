"""``role_intents`` — Amendment A1: recruiter intent as first-class.

One row per (role, version). The latest non-superseded row is the active
intent. New versions ``SUPERSEDED_BY`` the prior one, preserving the
chain so bi-temporal audit queries can reconstruct what intent any
historical decision was made under.

Postgres is the source of truth; the graph mirrors the entity + edges
via the standard episode writer.

Note: this is distinct from the existing per-cycle ``intent_parser``
sub-agent. ``intent_parser`` extracts intent slots from Workable notes
on every cycle; ``role_intents`` holds the *manually authored* intent
the recruiter shapes deliberately. Both signal sources are visible to
the sub-agents at score time.

Revision ID: 082_add_role_intents
Revises: 081_add_capability_flags
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "082_add_role_intents"
down_revision = "081_add_capability_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "role_intents",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        # The structured fields from A1.3 — soft_signals, deal_breakers,
        # growth_expectations, context_for_opening, weighting_notes,
        # must_haves_missing_from_spec. JSON for forward-compat.
        sa.Column("structured_fields", sa.JSON(), nullable=False),
        sa.Column("free_text", sa.Text(), nullable=True),
        # Version chain — the row this one supersedes. NULL on v1.
        sa.Column(
            "superseded_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "role_intents.id",
                use_alter=True,
                name="fk_role_intents_superseded_id",
            ),
            nullable=True,
        ),
        # Bi-temporal: valid_from/valid_to anchor "what intent was active
        # at time t". When a new version is written, the prior version's
        # valid_to becomes the new version's valid_from.
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        # Authorship (graph mirrors this via AUTHORED_BY edge).
        sa.Column(
            "authored_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("authored_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("role_id", "version", name="uq_role_intents_role_version"),
    )
    op.create_index(
        "ix_role_intents_active_lookup",
        "role_intents",
        ["organization_id", "role_id", "valid_to"],
    )


def downgrade() -> None:
    op.drop_index("ix_role_intents_active_lookup", table_name="role_intents")
    op.drop_table("role_intents")
