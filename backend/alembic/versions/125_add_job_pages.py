"""Public JOB PAGE entity.

Publishing a requisition snapshots its PUBLIC-safe fields onto a ``job_pages``
row addressed by an unguessable ``token``; the page is served with NO auth at
``/api/v1/public/job/{token}``. Org-scoped (the poster / consultancy) and
deliberately carries NO client / rate / margin — the consultancy economics on
the RoleBrief never leak to the public. One page per ``brief_id``.

Adds:
  * ``job_pages`` table — id, organization_id [FK organizations.id, indexed],
    brief_id [FK role_briefs.id, indexed], token [unique, indexed], title,
    jd_markdown, location, workplace_type, employment_type, seniority,
    salary_min, salary_max, salary_currency, status [server_default "open"],
    created_at, published_at.

Indexes are emitted explicitly via op.create_index (the autogenerate-style
create_table does NOT create the indexes implied by ``index=True`` on the
model).

Revision ID: 125_add_job_pages
Revises: 124_add_clients_entity
Create Date: 2026-06-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "125_add_job_pages"
down_revision = "124_add_clients_entity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    current_timestamp = (
        sa.text("CURRENT_TIMESTAMP")
        if bind.dialect.name == "sqlite"
        else sa.text("now()")
    )
    op.create_table(
        "job_pages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("brief_id", sa.Integer(), nullable=True),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("jd_markdown", sa.Text(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("workplace_type", sa.String(), nullable=True),
        sa.Column("employment_type", sa.String(), nullable=True),
        sa.Column("seniority", sa.String(), nullable=True),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("salary_currency", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=current_timestamp,
            nullable=True,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["brief_id"], ["role_briefs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_pages_id", "job_pages", ["id"], unique=False)
    op.create_index(
        "ix_job_pages_organization_id",
        "job_pages",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_job_pages_brief_id", "job_pages", ["brief_id"], unique=False
    )
    op.create_index(
        "ix_job_pages_token", "job_pages", ["token"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_job_pages_token", table_name="job_pages")
    op.drop_index("ix_job_pages_brief_id", table_name="job_pages")
    op.drop_index("ix_job_pages_organization_id", table_name="job_pages")
    op.drop_index("ix_job_pages_id", table_name="job_pages")
    op.drop_table("job_pages")
