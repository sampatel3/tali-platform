"""Add candidates.phone_normalized for phone-fallback dedup.

The same person sometimes applies to a second job under a different email.
Workable mints a fresh candidate record per job, so Tali's sync misses on both
``workable_candidate_id`` and ``email`` and creates a duplicate profile — the
candidate is then evaluated as two different people.

``phone_normalized`` holds the last 9 digits of ``phone`` (country code /
formatting stripped) so the sync can match on phone after email. Indexed for
the equality lookup. Backfilled from existing rows here so the fallback works
against history, not just new syncs.

Revision ID: 101_add_candidate_phone_normalized
Revises: 100_fix_capability_flag_pk
Create Date: 2026-05-23

Note: re-chained from 097 to 100 — main concurrently added 098/099/100, so the
original 097-based revision was a second alembic head that crashed the boot
migration. This now extends the single 100 head linearly.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "101_add_candidate_phone_normalized"
down_revision = "100_fix_capability_flag_pk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidates",
        sa.Column("phone_normalized", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_candidates_phone_normalized",
        "candidates",
        ["phone_normalized"],
        if_not_exists=True,
    )
    # Backfill: last 9 digits of the existing phone, mirroring
    # _normalize_phone_for_match (min 9 digits, else leave NULL).
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        rows = bind.execute(
            sa.text("SELECT id, phone FROM candidates WHERE phone IS NOT NULL")
        ).mappings()
        for row in rows:
            digits = "".join(
                character
                for character in str(row["phone"])
                if character in "0123456789"
            )
            if len(digits) < 9:
                continue
            bind.execute(
                sa.text(
                    "UPDATE candidates SET phone_normalized = :normalized "
                    "WHERE id = :candidate_id"
                ),
                {
                    "candidate_id": row["id"],
                    "normalized": digits[-9:],
                },
            )
    else:
        op.execute(
            """
            UPDATE candidates
            SET phone_normalized = RIGHT(REGEXP_REPLACE(phone, '[^0-9]', '', 'g'), 9)
            WHERE phone IS NOT NULL
              AND LENGTH(REGEXP_REPLACE(phone, '[^0-9]', '', 'g')) >= 9
            """
        )


def downgrade() -> None:
    op.drop_index(
        "ix_candidates_phone_normalized",
        table_name="candidates",
        if_exists=True,
    )
    op.drop_column("candidates", "phone_normalized")
