"""Post-submit understanding check on the assessment row.

Revision ID: 189_understanding_check
Revises: 188_enforce_active_decision_slot
Create Date: 2026-07-23

Revision ids are capped at 32 characters by ``alembic_version.version_num``;
migrations run on boot, so an over-long id fails the deploy rather than the
test suite. Keep new ids short.

Additive and nullable throughout. Every column describes a phase that only
exists after an assessment is frozen, so historical rows are correct with all
of them NULL: ``understanding_check_status IS NULL`` means "this run predates
the check", which the grader treats as not-assessed rather than as a zero.

``understanding_check_questions`` stores the correct option index alongside
each question and is server-only; no candidate-facing serializer reads this
column directly.

The partial index on ``understanding_check_status`` supports the expiry sweep,
which scans only the small set of rows in the open window.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "189_understanding_check"
down_revision = "188_enforce_active_decision_slot"
branch_labels = None
depends_on = None


_STATUS_INDEX = "ix_assessments_understanding_check_status"

_COLUMNS = (
    ("understanding_check_status", sa.String(length=16)),
    ("understanding_check_questions", sa.JSON()),
    ("understanding_check_answers", sa.JSON()),
    ("understanding_check_score", sa.Float()),
    ("understanding_check_started_at", sa.DateTime(timezone=True)),
    ("understanding_check_expires_at", sa.DateTime(timezone=True)),
    ("understanding_check_completed_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        column["name"]
        for column in sa.inspect(bind).get_columns("assessments")
    }
    for name, type_ in _COLUMNS:
        if name in existing:
            continue
        op.add_column("assessments", sa.Column(name, type_, nullable=True))

    index_names = {
        index["name"] for index in sa.inspect(bind).get_indexes("assessments")
    }
    if _STATUS_INDEX not in index_names:
        op.create_index(
            _STATUS_INDEX,
            "assessments",
            ["understanding_check_status"],
            unique=False,
            postgresql_where=sa.text("understanding_check_status IS NOT NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    index_names = {
        index["name"] for index in sa.inspect(bind).get_indexes("assessments")
    }
    if _STATUS_INDEX in index_names:
        op.drop_index(_STATUS_INDEX, table_name="assessments")

    existing = {
        column["name"]
        for column in sa.inspect(bind).get_columns("assessments")
    }
    for name, _type in reversed(_COLUMNS):
        if name in existing:
            op.drop_column("assessments", name)
