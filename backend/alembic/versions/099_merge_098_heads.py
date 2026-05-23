"""Merge the two ``098`` alembic heads into one.

Two migrations both branched from ``097_add_decision_type_index``:
``098_add_org_two_factor_required`` (#256) and
``098_prescreen_calibration_samples`` (#304). Each is valid alone, but
merging both PRs left the revision tree with two heads, so
``alembic upgrade head`` — run on boot by ``railway_start`` — fails with
"Multiple head revisions are present". This is an empty merge revision
that rejoins the branches so a single head exists again. No schema change.

Revision ID: 099_merge_098_heads
Revises: 098_add_org_two_factor_required, 098_prescreen_calibration_samples
Create Date: 2026-05-23
"""

from __future__ import annotations


revision = "099_merge_098_heads"
down_revision = (
    "098_add_org_two_factor_required",
    "098_prescreen_calibration_samples",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
