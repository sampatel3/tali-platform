"""Role-scope chat conversations + merge heads.

Merges the two outstanding migration heads on main —
``062_add_role_agent_next_run_at`` (agent v1 chain) and
``060_add_settings_redesign_fields`` (settings redesign branch) — and
adds the new ``role_id`` column on ``taali_chat_conversations``.

When ``role_id`` is set, the chat conversation is scoped to a single
role: the system prompt mentions the role + recent agent activity, and
the agent-aware chat tools (get_recent_agent_decisions etc.) default
to that role_id without the recruiter having to specify it. Null =
existing global cross-role chat behaviour.

Revision ID: 063_chat_role_scope_merge_heads
Revises: 062_add_role_agent_next_run_at, 060_add_settings_redesign_fields
Create Date: 2026-05-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "063_chat_role_scope_merge_heads"
# Two-tuple = merge migration. Alembic's `alembic upgrade head` follows
# both ancestors before applying this revision's upgrade body.
down_revision = (
    "062_add_role_agent_next_run_at",
    "060_add_settings_redesign_fields",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "taali_chat_conversations",
        sa.Column("role_id", sa.Integer(), nullable=True),
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("taali_chat_conversations") as batch_op:
            batch_op.create_foreign_key(
                "fk_taali_chat_conversations_role_id_roles",
                "roles",
                ["role_id"],
                ["id"],
                ondelete="SET NULL",
            )
    else:
        op.create_foreign_key(
            "fk_taali_chat_conversations_role_id_roles",
            source_table="taali_chat_conversations",
            referent_table="roles",
            local_cols=["role_id"],
            remote_cols=["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_taali_chat_conversations_role_id",
        "taali_chat_conversations",
        ["role_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_taali_chat_conversations_role_id",
        table_name="taali_chat_conversations",
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("taali_chat_conversations") as batch_op:
            batch_op.drop_constraint(
                "fk_taali_chat_conversations_role_id_roles",
                type_="foreignkey",
            )
    else:
        op.drop_constraint(
            "fk_taali_chat_conversations_role_id_roles",
            "taali_chat_conversations",
            type_="foreignkey",
        )
    op.drop_column("taali_chat_conversations", "role_id")
