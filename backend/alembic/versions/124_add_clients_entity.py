"""Consultancy CLIENTS entity + per-requisition economics.

Adds:
  * ``clients`` table — an org-scoped consultancy client account
    (id, organization_id [FK organizations.id, indexed], name, contact_name,
    contact_email, status [server_default "active"], created_at).
  * ``role_briefs.client_id`` — FK clients.id (nullable, indexed): the client a
    requisition is opened for.
  * ``role_briefs.client_rate`` — Integer (nullable): the annual rate billed to
    the client, in the brief's currency. Margin is computed, never stored.

Indexes are emitted explicitly via op.create_index (the autogenerate-style
add_column does NOT create the index implied by ``index=True`` on the model).

Revision ID: 124_add_clients_entity
Revises: 123_add_requisition_chat_fields
Create Date: 2026-06-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "124_add_clients_entity"
down_revision = "123_add_requisition_chat_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    current_timestamp = (
        sa.text("CURRENT_TIMESTAMP")
        if bind.dialect.name == "sqlite"
        else sa.text("now()")
    )
    op.create_table(
        "clients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("contact_name", sa.String(), nullable=True),
        sa.Column("contact_email", sa.String(), nullable=True),
        sa.Column(
            "status", sa.String(), nullable=False, server_default="active"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=current_timestamp,
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_clients_id", "clients", ["id"], unique=False
    )
    op.create_index(
        "ix_clients_organization_id", "clients", ["organization_id"], unique=False
    )

    op.add_column(
        "role_briefs",
        sa.Column("client_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "role_briefs",
        sa.Column("client_rate", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_role_briefs_client_id", "role_briefs", ["client_id"], unique=False
    )
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("role_briefs") as batch_op:
            batch_op.create_foreign_key(
                "fk_role_briefs_client_id_clients",
                "clients",
                ["client_id"],
                ["id"],
            )
    else:
        op.create_foreign_key(
            "fk_role_briefs_client_id_clients",
            "role_briefs",
            "clients",
            ["client_id"],
            ["id"],
        )


def downgrade() -> None:
    op.drop_constraint(
        "fk_role_briefs_client_id_clients", "role_briefs", type_="foreignkey"
    )
    op.drop_index("ix_role_briefs_client_id", table_name="role_briefs")
    op.drop_column("role_briefs", "client_rate")
    op.drop_column("role_briefs", "client_id")

    op.drop_index("ix_clients_organization_id", table_name="clients")
    op.drop_index("ix_clients_id", table_name="clients")
    op.drop_table("clients")
