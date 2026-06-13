"""Add structured salary-expectation columns to candidate_applications.

Salary expectation lived only as free text in the Workable questionnaire
answers, so the grounded "top N with salary <= X" search had to send the notes
to Anthropic Citations on every query just to extract + cite the figure. These
columns capture the figure ONCE at Workable sync (parsed by
workable/salary_parser.py) so the search reads a number directly and the cap
verdict becomes a pure data lookup + compare.

* ``salary_expectation_amount``   — numeric value in the source currency
* ``salary_expectation_currency`` — ISO code (detected, or assumed AED)
* ``salary_expectation_aed``      — value normalised to AED (the comparison field)
* ``salary_expectation_raw``      — verbatim answer text (display / audit / evidence)

No backfill: the parse is fuzzy (which question is "salary expectation" varies
per org) and not expressible in SQL. Existing rows stay NULL and the search
falls back to its LLM-extraction path until the next full sync repopulates them.

Revision ID: 115_add_application_salary_expectation
Revises: 114_add_threshold_calibrations
Create Date: 2026-06-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "115_add_application_salary_expectation"
down_revision = "114_add_threshold_calibrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_applications",
        sa.Column("salary_expectation_amount", sa.Float(), nullable=True),
    )
    op.add_column(
        "candidate_applications",
        sa.Column("salary_expectation_currency", sa.String(), nullable=True),
    )
    op.add_column(
        "candidate_applications",
        sa.Column("salary_expectation_aed", sa.Float(), nullable=True),
    )
    op.add_column(
        "candidate_applications",
        sa.Column("salary_expectation_raw", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("candidate_applications", "salary_expectation_raw")
    op.drop_column("candidate_applications", "salary_expectation_aed")
    op.drop_column("candidate_applications", "salary_expectation_currency")
    op.drop_column("candidate_applications", "salary_expectation_amount")
