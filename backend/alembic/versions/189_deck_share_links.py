"""Per-prospect deck share links.

Backs ``DeckShareLink`` / ``DeckShareView``. Purely additive: two new tables,
no changes to existing ones, so this is safe to apply ahead of the code that
uses it.

Revision ID: 189_deck_share_links
Revises: 188_enforce_active_decision_slot
"""
from alembic import op
import sqlalchemy as sa


revision = "189_deck_share_links"
down_revision = "188_enforce_active_decision_slot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deck_share_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prospect_label", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_deck_share_links_id"), "deck_share_links", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_deck_share_links_token"),
        "deck_share_links",
        ["token"],
        unique=True,
    )

    op.create_table(
        "deck_share_views",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("deck_share_link_id", sa.Integer(), nullable=False),
        sa.Column(
            "viewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["deck_share_link_id"], ["deck_share_links.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_deck_share_views_id"), "deck_share_views", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_deck_share_views_deck_share_link_id"),
        "deck_share_views",
        ["deck_share_link_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_deck_share_views_deck_share_link_id"), table_name="deck_share_views"
    )
    op.drop_index(op.f("ix_deck_share_views_id"), table_name="deck_share_views")
    op.drop_table("deck_share_views")
    op.drop_index(op.f("ix_deck_share_links_token"), table_name="deck_share_links")
    op.drop_index(op.f("ix_deck_share_links_id"), table_name="deck_share_links")
    op.drop_table("deck_share_links")
