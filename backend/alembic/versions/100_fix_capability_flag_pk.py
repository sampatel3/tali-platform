"""Give ``capability_flags`` a surrogate PK so global rows are insertable.

The table was created (081) with a composite PK on
``(capability, organization_id)`` and ``organization_id`` declared
``nullable=True``. On Postgres a PK column is implicitly NOT NULL, so the
intended global rows (``organization_id IS NULL``) could never be
inserted — every global capability default was unusable in production.

This migration switches to a surrogate integer ``id`` PK and enforces the
old uniqueness via two partial unique indexes:

* ``uq_capability_flags_global`` — unique ``capability`` where
  ``organization_id IS NULL`` (one global default per capability).
* ``uq_capability_flags_org`` — unique ``(capability, organization_id)``
  where ``organization_id IS NOT NULL`` (one override per org).

Revision ID: 100_fix_capability_flag_pk
Revises: 099_merge_098_heads
Create Date: 2026-05-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "100_fix_capability_flag_pk"
down_revision = "099_merge_098_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE capability_flags DROP CONSTRAINT IF EXISTS pk_capability_flags"
        )
        op.add_column("capability_flags", sa.Column("id", sa.Integer(), nullable=True))
        op.execute("CREATE SEQUENCE IF NOT EXISTS capability_flags_id_seq")
        op.execute(
            "UPDATE capability_flags SET id = nextval('capability_flags_id_seq') "
            "WHERE id IS NULL"
        )
        op.alter_column("capability_flags", "id", nullable=False)
        op.execute(
            "ALTER TABLE capability_flags "
            "ALTER COLUMN id SET DEFAULT nextval('capability_flags_id_seq')"
        )
        op.execute("ALTER SEQUENCE capability_flags_id_seq OWNED BY capability_flags.id")
        op.create_primary_key("pk_capability_flags", "capability_flags", ["id"])
        # The old composite PK forced organization_id NOT NULL; allow NULL
        # now so global rows can exist.
        op.alter_column(
            "capability_flags",
            "organization_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        op.create_index(
            "uq_capability_flags_global",
            "capability_flags",
            ["capability"],
            unique=True,
            postgresql_where=sa.text("organization_id IS NULL"),
        )
        op.create_index(
            "uq_capability_flags_org",
            "capability_flags",
            ["capability", "organization_id"],
            unique=True,
            postgresql_where=sa.text("organization_id IS NOT NULL"),
        )
    else:
        # SQLite (dev/test only — prod is Postgres). batch recreate swaps
        # the composite PK for an autoincrement id; the new INTEGER PRIMARY
        # KEY aliases rowid, so copied rows get ids auto-assigned.
        with op.batch_alter_table(
            "capability_flags",
            recreate="always",
            table_args=(sa.PrimaryKeyConstraint("id"),),
        ) as batch:
            batch.add_column(sa.Column("id", sa.Integer(), autoincrement=True))
        op.create_index(
            "uq_capability_flags_global",
            "capability_flags",
            ["capability"],
            unique=True,
            sqlite_where=sa.text("organization_id IS NULL"),
        )
        op.create_index(
            "uq_capability_flags_org",
            "capability_flags",
            ["capability", "organization_id"],
            unique=True,
            sqlite_where=sa.text("organization_id IS NOT NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    op.drop_index("uq_capability_flags_org", table_name="capability_flags")
    op.drop_index("uq_capability_flags_global", table_name="capability_flags")

    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE capability_flags DROP CONSTRAINT IF EXISTS pk_capability_flags"
        )
        op.execute("ALTER TABLE capability_flags ALTER COLUMN id DROP DEFAULT")
        op.drop_column("capability_flags", "id")
        op.execute("DROP SEQUENCE IF EXISTS capability_flags_id_seq")
        op.alter_column(
            "capability_flags",
            "organization_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        op.create_primary_key(
            "pk_capability_flags",
            "capability_flags",
            ["capability", "organization_id"],
        )
    else:
        with op.batch_alter_table(
            "capability_flags",
            recreate="always",
            table_args=(
                sa.PrimaryKeyConstraint("capability", "organization_id"),
            ),
        ) as batch:
            batch.drop_column("id")
