"""Add candidate_applications.genuine_pre_screen_score_100.

``pre_screen_score_100`` was repurposed as the "best available" display/rank
score: full cv_match scoring overwrites it (via refresh_pre_screening_fields)
so the directory list and detail page agree. That means it no longer holds the
genuine cheap pre-screen verdict — which the decision engine's pre_screen gate
actually wants. The genuine score is still durably recorded in
``pre_screen_evidence['llm_score_100']`` (the original LLM score before any
fraud cap), so this column gives it a first-class, never-overwritten home.

Backfill:
  1. From ``pre_screen_evidence->>'llm_score_100'`` where present (the durable
     genuine score, recoverable even after cv_match overwrote the shared col).
  2. For pure pre-screen rows (cv_match_score IS NULL) still missing it, the
     shared column was never overwritten, so it == the genuine score.

Revision ID: 102_add_genuine_pre_screen_score
Revises: 101_add_candidate_phone_normalized
Create Date: 2026-05-24
"""

from __future__ import annotations

import json
import re

import sqlalchemy as sa
from alembic import op


revision = "102_add_genuine_pre_screen_score"
down_revision = "101_add_candidate_phone_normalized"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_applications",
        sa.Column("genuine_pre_screen_score_100", sa.Float(), nullable=True),
    )
    # 1. Recover the genuine pre-screen score from evidence (durable, survives
    #    the cv_match overwrite). Guard the cast with a numeric regex so a
    #    malformed value can never abort the migration.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        rows = bind.execute(
            sa.text(
                "SELECT id, pre_screen_evidence FROM candidate_applications "
                "WHERE pre_screen_evidence IS NOT NULL"
            )
        ).mappings()
        for row in rows:
            evidence = row["pre_screen_evidence"]
            if isinstance(evidence, str):
                try:
                    evidence = json.loads(evidence)
                except (TypeError, ValueError):
                    continue
            if not isinstance(evidence, dict):
                continue
            raw_score = evidence.get("llm_score_100")
            if re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", str(raw_score)) is None:
                continue
            bind.execute(
                sa.text(
                    "UPDATE candidate_applications "
                    "SET genuine_pre_screen_score_100 = :score "
                    "WHERE id = :application_id"
                ),
                {
                    "application_id": row["id"],
                    "score": float(raw_score),
                },
            )
    else:
        op.execute(
            """
            UPDATE candidate_applications
            SET genuine_pre_screen_score_100 =
                (pre_screen_evidence->>'llm_score_100')::double precision
            WHERE pre_screen_evidence->>'llm_score_100' IS NOT NULL
              AND pre_screen_evidence->>'llm_score_100' ~ '^-?[0-9]+(\\.[0-9]+)?$'
            """
        )
    # 2. Pure pre-screen rows (never full-scored) still have the genuine value
    #    in the shared column — it was never overwritten.
    op.execute(
        """
        UPDATE candidate_applications
        SET genuine_pre_screen_score_100 = pre_screen_score_100
        WHERE genuine_pre_screen_score_100 IS NULL
          AND cv_match_score IS NULL
          AND pre_screen_score_100 IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("candidate_applications", "genuine_pre_screen_score_100")
