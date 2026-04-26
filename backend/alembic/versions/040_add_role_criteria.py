"""Add role_criteria table and backfill from Role.additional_requirements.

Existing recruiter requirement text is parsed into one ``recruiter``-source
criterion per bullet, preserving order. The job-spec Requirements section is
NOT backfilled here — that derivation runs lazily on the next spec upload or
manual re-sync.

Revision ID: 040_add_role_criteria
Revises: 039_drop_org_custom_claude_api_key
Create Date: 2026-04-26
"""

from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import column, table


revision = "040_add_role_criteria"
down_revision = "039_drop_org_custom_claude_api_key"
branch_labels = None
depends_on = None


_MAX_REQUIREMENTS = 16


def _split_requirements(text: str | None) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    parts = re.split(r"[\n;]+", raw)
    if len(parts) <= 1:
        sentence_parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", raw)
        if len(sentence_parts) > 1:
            parts = sentence_parts
    items: list[str] = []
    seen: set[str] = set()
    for entry in parts:
        cleaned = re.sub(r"^\s*(?:[-*•]|\d+[\).\-\s])\s*", "", str(entry or "")).strip()
        if not cleaned:
            continue
        compact = re.sub(r"\s+", " ", cleaned)
        lowered = compact.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        items.append(compact[:220])
        if len(items) >= _MAX_REQUIREMENTS:
            break
    if not items:
        return [re.sub(r"\s+", " ", raw)[:220]]
    return items


def upgrade() -> None:
    op.create_table(
        "role_criteria",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column(
            "role_id",
            sa.Integer,
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String, nullable=False, server_default=sa.text("'recruiter'")),
        sa.Column("ordering", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("weight", sa.Float, nullable=False, server_default=sa.text("1.0")),
        sa.Column("must_have", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_role_criteria_role_id", "role_criteria", ["role_id"])

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, additional_requirements FROM roles "
            "WHERE additional_requirements IS NOT NULL AND deleted_at IS NULL"
        )
    ).fetchall()

    role_criteria_t = table(
        "role_criteria",
        column("role_id", sa.Integer),
        column("source", sa.String),
        column("ordering", sa.Integer),
        column("weight", sa.Float),
        column("must_have", sa.Boolean),
        column("text", sa.Text),
    )

    inserts: list[dict] = []
    for role_id, additional_requirements in rows:
        for ordering, text in enumerate(_split_requirements(additional_requirements)):
            inserts.append(
                {
                    "role_id": role_id,
                    "source": "recruiter",
                    "ordering": ordering,
                    "weight": 1.0,
                    "must_have": False,
                    "text": text,
                }
            )
    if inserts:
        op.bulk_insert(role_criteria_t, inserts)


def downgrade() -> None:
    op.drop_index("ix_role_criteria_role_id", table_name="role_criteria")
    op.drop_table("role_criteria")
