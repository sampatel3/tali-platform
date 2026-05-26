"""Merge the two ``104`` alembic heads into one.

Two migrations both branched from ``103_add_workable_stages_cache``:
``104_add_graph_episode_outbox`` and ``104_add_assessment_experiments``
(#378). Each is valid alone, but merging both PRs left the revision tree
with two heads, so ``alembic upgrade head`` — run on boot by
``railway_start`` — fails with "Multiple head revisions are present".
This is an empty merge revision that rejoins the branches so a single
head exists again. No schema change.

Revision ID: 105_merge_104_heads
Revises: 104_add_assessment_experiments, 104_add_graph_episode_outbox
Create Date: 2026-05-26
"""

from __future__ import annotations


revision = "105_merge_104_heads"
down_revision = (
    "104_add_assessment_experiments",
    "104_add_graph_episode_outbox",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
