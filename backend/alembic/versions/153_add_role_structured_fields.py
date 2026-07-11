"""E1: structured role columns (employment / location / salary).

Promotes employment_type / workplace_type / location / department / salary out of
the ``workable_job_data`` JSON blob into first-class ``roles`` columns. These are
the structured fields the native careers/apply surface reads; the public JobPage
already carries its own snapshot, so this is additive foundation, not a behaviour
change. All nullable, no backfill (derivable-from-workable_job_data backfill is
deferred — same call the ats branch made). Deliberately EXCLUDES the parallel
publishing model's ``status`` / ``slug`` columns: publishing rides ``job_pages``.

Revision ID: 153_add_role_structured_fields
Revises: 152_add_source_attribution_and_dispositions
Create Date: 2026-07-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "153_add_role_structured_fields"
down_revision = "152_add_source_attribution_and_dispositions"
branch_labels = None
depends_on = None

_NULLABLE_COLS = [
    ("employment_type", sa.String()),
    ("workplace_type", sa.String()),
    ("location_city", sa.String()),
    ("location_country", sa.String()),
    ("department", sa.String()),
    ("salary_min", sa.Integer()),
    ("salary_max", sa.Integer()),
    ("salary_currency", sa.String()),
    ("salary_period", sa.String()),
]


def upgrade() -> None:
    for name, type_ in _NULLABLE_COLS:
        op.add_column("roles", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _type in reversed(_NULLABLE_COLS):
        op.drop_column("roles", name)
