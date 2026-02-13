"""Add role-first application schema, pause fields, and irreversible data reset.

Revision ID: 015_role_first_applications_pause_reset
Revises: 014_add_completed_due_to_timeout
Create Date: 2026-02-13
"""

from __future__ import annotations

import logging

from alembic import op
import sqlalchemy as sa

revision = "015_role_first_applications_pause_reset"
down_revision = "014_add_completed_due_to_timeout"
branch_labels = None
depends_on = None

LOGGER = logging.getLogger("alembic.runtime.migration")


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade():
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("job_spec_file_url", sa.String(), nullable=True),
        sa.Column("job_spec_filename", sa.String(), nullable=True),
        sa.Column("job_spec_text", sa.Text(), nullable=True),
        sa.Column("job_spec_uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
    )
    op.create_index("ix_roles_id", "roles", ["id"])
    op.create_index("ix_roles_organization_id", "roles", ["organization_id"])

    op.create_table(
        "candidate_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'applied'")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("cv_file_url", sa.String(), nullable=True),
        sa.Column("cv_filename", sa.String(), nullable=True),
        sa.Column("cv_text", sa.Text(), nullable=True),
        sa.Column("cv_uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.UniqueConstraint("candidate_id", "role_id", name="uq_candidate_role_application"),
    )
    op.create_index("ix_candidate_applications_id", "candidate_applications", ["id"])
    op.create_index("ix_candidate_applications_org_id", "candidate_applications", ["organization_id"])
    op.create_index("ix_candidate_applications_candidate_id", "candidate_applications", ["candidate_id"])
    op.create_index("ix_candidate_applications_role_id", "candidate_applications", ["role_id"])

    op.create_table(
        "role_tasks",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "task_id"),
    )

    op.add_column("assessments", sa.Column("role_id", sa.Integer(), nullable=True))
    op.add_column("assessments", sa.Column("application_id", sa.Integer(), nullable=True))
    op.add_column(
        "assessments",
        sa.Column("is_timer_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("assessments", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("assessments", sa.Column("pause_reason", sa.String(), nullable=True))
    op.add_column(
        "assessments",
        sa.Column("total_paused_seconds", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_assessments_role_id", "assessments", ["role_id"])
    op.create_index("ix_assessments_application_id", "assessments", ["application_id"])
    op.create_foreign_key(
        "fk_assessments_role_id_roles",
        "assessments",
        "roles",
        ["role_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_assessments_application_id_candidate_applications",
        "assessments",
        "candidate_applications",
        ["application_id"],
        ["id"],
    )
    op.alter_column("assessments", "is_timer_paused", server_default=None)
    op.alter_column("assessments", "total_paused_seconds", server_default=None)
    op.alter_column("candidate_applications", "status", server_default=None)

    bind = op.get_bind()
    LOGGER.warning(
        "IRREVERSIBLE RESET (release-gate=2026-02-13-role-first-hardening): deleting all candidates and assessments in this environment."
    )
    if _table_exists(bind, "assessments"):
        op.execute(sa.text("DELETE FROM assessments"))
    if _table_exists(bind, "candidate_applications"):
        op.execute(sa.text("DELETE FROM candidate_applications"))
    if _table_exists(bind, "candidates"):
        op.execute(sa.text("DELETE FROM candidates"))


def downgrade():
    op.drop_constraint("fk_assessments_application_id_candidate_applications", "assessments", type_="foreignkey")
    op.drop_constraint("fk_assessments_role_id_roles", "assessments", type_="foreignkey")
    op.drop_index("ix_assessments_application_id", table_name="assessments")
    op.drop_index("ix_assessments_role_id", table_name="assessments")
    op.drop_column("assessments", "total_paused_seconds")
    op.drop_column("assessments", "pause_reason")
    op.drop_column("assessments", "paused_at")
    op.drop_column("assessments", "is_timer_paused")
    op.drop_column("assessments", "application_id")
    op.drop_column("assessments", "role_id")

    op.drop_table("role_tasks")

    op.drop_index("ix_candidate_applications_role_id", table_name="candidate_applications")
    op.drop_index("ix_candidate_applications_candidate_id", table_name="candidate_applications")
    op.drop_index("ix_candidate_applications_org_id", table_name="candidate_applications")
    op.drop_index("ix_candidate_applications_id", table_name="candidate_applications")
    op.drop_table("candidate_applications")

    op.drop_index("ix_roles_organization_id", table_name="roles")
    op.drop_index("ix_roles_id", table_name="roles")
    op.drop_table("roles")
