"""Bucketed criteria — workspace-level criteria, role-level provenance, and a
shared bucket dimension (must / preferred / constraint).

This is the structural underpinning for the new chip-based intent UI.

Schema changes
--------------

1. ``org_criteria`` (NEW) — workspace-level criteria authored on
   Settings → AI agent. Mirrors the ``role_criteria`` shape minus the
   role-specific columns.

2. ``role_criteria`` (UPDATED):
   - ``bucket`` — ``must`` | ``preferred`` | ``constraint``. Backfilled from
     the legacy ``must_have`` boolean (true → ``must``, false → ``preferred``)
     and the unused-but-defined ``recruiter_constraint`` source
     (→ ``constraint``).
   - ``org_criterion_id`` — FK into ``org_criteria``. Set when a row was
     copied from a workspace criterion. Null = role-only addition.
   - ``customized_at`` — timestamp set when the recruiter edits a
     workspace-derived row's text or bucket. Sync logic uses this to skip
     overwriting recruiter customizations on the role.

3. ``roles.suppressed_org_criterion_ids`` (NEW) — JSON array of org
   criterion ids the recruiter has explicitly removed from this role.
   Sync logic skips these; "Show hidden" surfaces them so the recruiter
   can add them back.

Backfill
--------

- ``role_criteria.bucket`` populated from ``must_have`` + ``source``.
- ``org_criteria`` populated from the existing
  ``organizations.default_role_requirements`` (JSON string array, with
  prefix-based bucket inference: ``"Must:"`` / ``"Must have:"`` →
  ``must``; ``"Constraint:"`` → ``constraint``; ``"Nice to have:"`` /
  ``"Nice:"`` → ``preferred``; everything else → ``preferred``).
  Legacy free-text ``default_additional_requirements`` is split on
  newlines and used only when ``default_role_requirements`` is empty.
- Existing role-level ``role_criteria`` rows are NOT linked back to
  newly-created org criteria — we don't try to reconcile by text match
  because the text often diverges. Roles snapshot org criteria at the
  next role create / Workable import or via an explicit "sync workspace"
  click on the role page.

Legacy columns are NOT dropped here. They remain populated by the
service layer (chips → joined text mirror) so existing readers
(``additional_requirements`` / ``default_role_requirements``) keep
working until the frontend ships and a follow-up PR retires them.

Revision ID: 066_add_bucketed_criteria
Revises: 065_merge_chat_scope_and_hub_feedback
Create Date: 2026-05-08
"""

from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import column, table


revision = "066_add_bucketed_criteria"
down_revision = "065_merge_chat_scope_and_hub_feedback"
branch_labels = None
depends_on = None


_BUCKET_MUST = "must"
_BUCKET_PREFERRED = "preferred"
_BUCKET_CONSTRAINT = "constraint"

# Order matters — longest/most-specific prefix first so "Must have:" wins
# over a hypothetical "Must:" substring earlier in the line.
_BUCKET_PREFIXES: tuple[tuple[str, str], ...] = (
    ("must have:", _BUCKET_MUST),
    ("must-have:", _BUCKET_MUST),
    ("must:", _BUCKET_MUST),
    ("required:", _BUCKET_MUST),
    ("constraint:", _BUCKET_CONSTRAINT),
    ("constraints:", _BUCKET_CONSTRAINT),
    ("nice to have:", _BUCKET_PREFERRED),
    ("nice-to-have:", _BUCKET_PREFERRED),
    ("nice:", _BUCKET_PREFERRED),
    ("preferred:", _BUCKET_PREFERRED),
)


def _infer_bucket_from_prefix(text: str) -> tuple[str, str]:
    """Return (bucket, stripped_text). Prefix is consumed if matched."""
    raw = (text or "").strip()
    if not raw:
        return _BUCKET_PREFERRED, ""
    lowered = raw.lower()
    for prefix, bucket in _BUCKET_PREFIXES:
        if lowered.startswith(prefix):
            return bucket, raw[len(prefix):].strip()
    return _BUCKET_PREFERRED, raw


def _split_text_blob(text: str | None) -> list[str]:
    """Reuse the same splitter shape as alembic 040 so the produced
    criteria match what role_criteria backfill produced."""
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
    for raw_part in parts:
        cleaned = re.sub(r"^\s*(?:[-*•]|\d+[\).\-\s])\s*", "", str(raw_part or "")).strip()
        if not cleaned:
            continue
        compact = re.sub(r"\s+", " ", cleaned)
        lowered = compact.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        items.append(compact[:220])
        if len(items) >= 16:
            break
    return items


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. New table: org_criteria
    # ------------------------------------------------------------------
    op.create_table(
        "org_criteria",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column(
            "organization_id",
            sa.Integer,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordering", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("weight", sa.Float, nullable=False, server_default=sa.text("1.0")),
        sa.Column(
            "bucket",
            sa.String,
            nullable=False,
            server_default=sa.text("'preferred'"),
        ),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_org_criteria_organization_id",
        "org_criteria",
        ["organization_id"],
    )

    # ------------------------------------------------------------------
    # 2. role_criteria: add bucket + org_criterion_id + customized_at
    # ------------------------------------------------------------------
    op.add_column(
        "role_criteria",
        sa.Column(
            "bucket",
            sa.String,
            nullable=False,
            server_default=sa.text("'preferred'"),
        ),
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("role_criteria") as batch_op:
            batch_op.add_column(
                sa.Column("org_criterion_id", sa.Integer, nullable=True)
            )
            batch_op.create_foreign_key(
                "role_criteria_org_criterion_id_fkey",
                "org_criteria",
                ["org_criterion_id"],
                ["id"],
                ondelete="SET NULL",
            )
    else:
        op.add_column(
            "role_criteria",
            sa.Column(
                "org_criterion_id",
                sa.Integer,
                sa.ForeignKey("org_criteria.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    op.add_column(
        "role_criteria",
        sa.Column("customized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_role_criteria_org_criterion_id",
        "role_criteria",
        ["org_criterion_id"],
    )

    # Backfill bucket from existing must_have + source.
    op.execute(
        """
        UPDATE role_criteria
        SET bucket = CASE
          WHEN source = 'recruiter_constraint' THEN 'constraint'
          WHEN must_have = true THEN 'must'
          ELSE 'preferred'
        END
        """
    )

    # ------------------------------------------------------------------
    # 3. roles: suppressed_org_criterion_ids
    # ------------------------------------------------------------------
    op.add_column(
        "roles",
        sa.Column("suppressed_org_criterion_ids", sa.JSON, nullable=True),
    )

    # ------------------------------------------------------------------
    # 4. Backfill org_criteria from existing organization defaults
    # ------------------------------------------------------------------
    bind = op.get_bind()
    org_rows = bind.execute(
        sa.text(
            "SELECT id, default_role_requirements, default_additional_requirements "
            "FROM organizations"
        )
    ).fetchall()

    org_criteria_t = table(
        "org_criteria",
        column("organization_id", sa.Integer),
        column("ordering", sa.Integer),
        column("weight", sa.Float),
        column("bucket", sa.String),
        column("text", sa.Text),
    )

    inserts: list[dict] = []
    for org_id, default_role_requirements, default_additional_requirements in org_rows:
        # Prefer the structured list. Fall back to the legacy text blob
        # only when the structured list is empty.
        items: list[str] = []
        if isinstance(default_role_requirements, list):
            items = [str(x).strip() for x in default_role_requirements if str(x or "").strip()]
        if not items:
            items = _split_text_blob(default_additional_requirements)
        for ordering, raw in enumerate(items):
            bucket, stripped = _infer_bucket_from_prefix(raw)
            if not stripped:
                continue
            inserts.append(
                {
                    "organization_id": org_id,
                    "ordering": ordering,
                    "weight": 1.0,
                    "bucket": bucket,
                    "text": stripped[:220],
                }
            )
    if inserts:
        op.bulk_insert(org_criteria_t, inserts)


def downgrade() -> None:
    op.drop_column("roles", "suppressed_org_criterion_ids")
    op.drop_index(
        "ix_role_criteria_org_criterion_id", table_name="role_criteria"
    )
    op.drop_column("role_criteria", "customized_at")
    op.drop_column("role_criteria", "org_criterion_id")
    op.drop_column("role_criteria", "bucket")
    op.drop_index("ix_org_criteria_organization_id", table_name="org_criteria")
    op.drop_table("org_criteria")
