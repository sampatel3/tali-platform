"""Settings redesign — workspace defaults for new roles + per-role score threshold.

HANDOFF settings.md — replaces the old "Scoring" + "Assessment" + "AI tooling"
settings tabs with a single "AI agent" tab that exposes three workspace-wide
defaults inherited at role creation time:

- ``organizations.default_role_requirements`` — JSON list of strings, pre-fills
  the must-haves on every new role. Backfilled by splitting any pre-existing
  ``default_additional_requirements`` text blob on newlines.
- ``organizations.default_role_budget_cents`` — monthly USD cap (cents) that
  the agent will respect on a new role until a recruiter changes it.
- ``organizations.default_score_threshold`` — 0..100 minimum total score that
  feeds the new role's auto-shortlist threshold.
- ``organizations.monthly_spend_cap_cents`` — workspace-wide cap. When projected
  month-end > cap the agent pauses new invites (covered by the existing
  "Spend over budget" notification).

Per-role overrides win — existing roles are not retroactively updated when the
defaults change.

Also adds ``roles.score_threshold`` so each role gets its own 0..100 threshold
seeded from the workspace default at create-time.

Revision ID: 060_add_settings_redesign_fields
Revises: 059_add_share_links
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "060_add_settings_redesign_fields"
down_revision = "059_add_share_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("default_role_requirements", sa.JSON, nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("default_role_budget_cents", sa.Integer, nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("default_score_threshold", sa.Integer, nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("monthly_spend_cap_cents", sa.Integer, nullable=True),
    )

    # Backfill default_role_requirements from any pre-existing free-text
    # default_additional_requirements blob (one requirement per non-empty
    # line). Postgres-only — JSON column lets us write a JSON array literal.
    op.execute(
        """
        UPDATE organizations
        SET default_role_requirements = (
          SELECT to_jsonb(array_agg(trim(line) ORDER BY ord))
          FROM (
            SELECT trim(line) AS line, ord
            FROM regexp_split_to_table(
              coalesce(default_additional_requirements, ''),
              E'\\n'
            ) WITH ORDINALITY AS s(line, ord)
          ) parts
          WHERE trim(line) <> ''
        )
        WHERE default_additional_requirements IS NOT NULL
          AND trim(default_additional_requirements) <> ''
        """
    )

    op.add_column(
        "roles",
        sa.Column("score_threshold", sa.Integer, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("roles", "score_threshold")
    op.drop_column("organizations", "monthly_spend_cap_cents")
    op.drop_column("organizations", "default_score_threshold")
    op.drop_column("organizations", "default_role_budget_cents")
    op.drop_column("organizations", "default_role_requirements")
