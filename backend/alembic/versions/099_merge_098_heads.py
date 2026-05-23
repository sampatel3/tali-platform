"""Merge concurrent alembic heads: org 2FA flag + pre-screen calibration table.

Two PRs landed against ``097_add_decision_type_index`` in parallel:

- ``098_prescreen_calibration_samples`` (shadow-score training table for
  the pre-screen score calibrator)
- ``098_add_org_two_factor_required`` (``two_factor_required`` column on
  ``organizations`` so the recruiter settings toggle persists)

After both ship, alembic has two heads. ``alembic upgrade head`` errors
("Multiple head revisions are present") and the Railway start script fails
fast on it, restart-looping the web service (502 at the edge, "Unable to
reach the Taali API" on the login page). This is a pure merge marker — no
schema changes of its own. Same class of incident as
``069_merge_bucketed_criteria_with_decision_policies``.

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
