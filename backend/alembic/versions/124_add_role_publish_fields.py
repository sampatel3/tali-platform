"""P1: role publish fields — structural columns + status + slug.

Promotes employment_type / workplace_type / location / department / salary out of
the workable_job_data JSON blob into first-class columns, plus lifecycle status
(default 'draft') and a unique per-org slug for public careers URLs. All additive;
status defaults 'draft' so it is inert until the careers/publish surface consumes
it. Backfill from workable_job_data is deferred to the careers build.

Revision ID: 124_add_role_publish_fields
Revises: 123_add_user_role
Create Date: 2026-06-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "124_add_role_publish_fields"
down_revision = "123_add_user_role"
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
    ("slug", sa.String()),
]


def upgrade() -> None:
    for name, type_ in _NULLABLE_COLS:
        op.add_column("roles", sa.Column(name, type_, nullable=True))
    op.add_column(
        "roles",
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
    )
    op.create_unique_constraint(
        "uq_roles_org_slug", "roles", ["organization_id", "slug"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_roles_org_slug", "roles", type_="unique")
    op.drop_column("roles", "status")
    for name, _type in reversed(_NULLABLE_COLS):
        op.drop_column("roles", name)
