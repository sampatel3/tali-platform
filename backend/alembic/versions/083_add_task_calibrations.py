"""``task_calibrations`` — Amendment A2: task selection + predictive quality.

One row per (task_template_id, role_family). ``predictive_quality`` is
the correlation between assessment score and realised hiring outcome
quality for that template within that role family — used by the
``task_selection`` sub-agent to choose the best-calibrated template.

Recomputed nightly. Templates that decay below threshold are flagged
for retirement (A2.9.3) — invalidated by stamping the
``retired_at`` column, not deleted.

We reuse the existing ``tasks`` table (with ``is_template=True``) as
the canonical template store — no new ``task_templates`` table. The
calibration row links via ``task_id`` to ``tasks.id``.

Revision ID: 083_add_task_calibrations
Revises: 082_add_role_intents
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "083_add_task_calibrations"
down_revision = "082_add_role_intents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_calibrations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id"),
            nullable=False,
            index=True,
        ),
        # Role family is a string slug (see ``cv_matching.calibrators.extractor``
        # for the slugifier the rest of the codebase uses). Pre-pilot we
        # don't have a separate ``role_families`` table — strings are
        # the canonical identifier.
        sa.Column("role_family", sa.String(length=64), nullable=False, index=True),
        # Predictive quality: correlation of agent assessment score
        # with realised quality_signal in the window. Range -1..1.
        sa.Column("predictive_quality", sa.Float(), nullable=False, server_default="0"),
        # Sample size — number of assessment→outcome pairs contributing.
        # Low n means low confidence regardless of the correlation value.
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        # Average quality_signal in the window — orientation metric.
        sa.Column("avg_outcome_quality", sa.Float(), nullable=True),
        sa.Column("last_recomputed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # Retirement workflow (A2.9.3). Set when a template's calibration
        # decays below threshold or it goes unused for N months. Never
        # deletes the row.
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "task_id", "role_family", name="uq_task_calibrations_task_role_family",
        ),
    )


def downgrade() -> None:
    op.drop_table("task_calibrations")
