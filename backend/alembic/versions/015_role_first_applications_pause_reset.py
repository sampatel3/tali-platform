"""Add role-first application schema, pause fields, and legacy backfill.

Revision ID: 015_role_first_applications_pause_reset
Revises: 014_add_completed_due_to_timeout
Create Date: 2026-02-13
"""

from __future__ import annotations

import logging
import re

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


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    if not _table_exists(bind, table_name):
        return False
    inspector = sa.inspect(bind)
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    if not _table_exists(bind, table_name):
        return False
    inspector = sa.inspect(bind)
    return any(idx["name"] == index_name for idx in inspector.get_indexes(table_name))


def _normalize_role_name(
    task_role: str | None,
    candidate_position: str | None,
    job_spec_filename: str | None,
    job_spec_text: str | None,
) -> str:
    role = (task_role or "").strip()
    if role:
        role = role.replace("_", " ").replace("-", " ")
        role = " ".join(role.split())
        return role.title()

    spec_filename = (job_spec_filename or "").strip()
    if spec_filename:
        stem = spec_filename.rsplit(".", 1)[0]
        parts = [p.strip() for p in re.split(r"\s*[-|]\s*", stem) if p.strip()]
        for part in parts:
            lower = part.lower()
            if any(token in lower for token in ("engineer", "developer", "scientist", "analyst", "manager")):
                return " ".join(part.split())

    spec_text = " ".join((job_spec_text or "").split()).lower()
    if "data engineer" in spec_text:
        return "Data Engineer"
    if "machine learning engineer" in spec_text:
        return "Machine Learning Engineer"
    if "software engineer" in spec_text:
        return "Software Engineer"

    position = " ".join((candidate_position or "").split())
    if position:
        return position

    return "Unassigned role"


def _backfill_legacy_role_data(bind) -> None:
    if not _table_exists(bind, "assessments"):
        return

    LOGGER.info("Backfilling roles/applications from legacy assessments data")
    role_cache: dict[tuple[int, str], int] = {}
    app_cache: dict[tuple[int, int, int], int] = {}

    rows = bind.execute(
        sa.text(
            """
            SELECT
                a.id AS assessment_id,
                a.organization_id,
                a.candidate_id,
                a.task_id,
                c.position AS candidate_position,
                c.job_spec_file_url,
                c.job_spec_filename,
                c.job_spec_text,
                c.job_spec_uploaded_at,
                c.cv_file_url,
                c.cv_filename,
                c.cv_text,
                c.cv_uploaded_at,
                t.role AS task_role
            FROM assessments a
            LEFT JOIN candidates c ON c.id = a.candidate_id
            LEFT JOIN tasks t ON t.id = a.task_id
            ORDER BY a.organization_id, a.id
            """
        )
    ).mappings().all()

    for row in rows:
        org_id = row["organization_id"]
        candidate_id = row["candidate_id"]
        if org_id is None or candidate_id is None:
            continue

        role_name = _normalize_role_name(
            row["task_role"],
            row["candidate_position"],
            row["job_spec_filename"],
            row["job_spec_text"],
        )
        role_key = (int(org_id), role_name.lower())
        role_id = role_cache.get(role_key)
        if role_id is None:
            role_id = bind.execute(
                sa.text(
                    """
                    SELECT id
                    FROM roles
                    WHERE organization_id = :organization_id
                      AND lower(name) = :normalized_name
                    ORDER BY id
                    LIMIT 1
                    """
                ),
                {"organization_id": org_id, "normalized_name": role_name.lower()},
            ).scalar()

            if role_id is None:
                bind.execute(
                    sa.text(
                        """
                        INSERT INTO roles (
                            organization_id,
                            name,
                            description,
                            job_spec_file_url,
                            job_spec_filename,
                            job_spec_text,
                            job_spec_uploaded_at
                        ) VALUES (
                            :organization_id,
                            :name,
                            :description,
                            :job_spec_file_url,
                            :job_spec_filename,
                            :job_spec_text,
                            :job_spec_uploaded_at
                        )
                        """
                    ),
                    {
                        "organization_id": org_id,
                        "name": role_name,
                        "description": None,
                        "job_spec_file_url": row["job_spec_file_url"],
                        "job_spec_filename": row["job_spec_filename"],
                        "job_spec_text": row["job_spec_text"],
                        "job_spec_uploaded_at": row["job_spec_uploaded_at"],
                    },
                )
                role_id = bind.execute(
                    sa.text(
                        """
                        SELECT id
                        FROM roles
                        WHERE organization_id = :organization_id
                          AND lower(name) = :normalized_name
                        ORDER BY id
                        LIMIT 1
                        """
                    ),
                    {"organization_id": org_id, "normalized_name": role_name.lower()},
                ).scalar()
            else:
                bind.execute(
                    sa.text(
                        """
                        UPDATE roles
                        SET
                            job_spec_file_url = COALESCE(job_spec_file_url, :job_spec_file_url),
                            job_spec_filename = COALESCE(job_spec_filename, :job_spec_filename),
                            job_spec_text = COALESCE(job_spec_text, :job_spec_text),
                            job_spec_uploaded_at = COALESCE(job_spec_uploaded_at, :job_spec_uploaded_at)
                        WHERE id = :role_id
                        """
                    ),
                    {
                        "role_id": role_id,
                        "job_spec_file_url": row["job_spec_file_url"],
                        "job_spec_filename": row["job_spec_filename"],
                        "job_spec_text": row["job_spec_text"],
                        "job_spec_uploaded_at": row["job_spec_uploaded_at"],
                    },
                )

            role_cache[role_key] = int(role_id)

        app_key = (int(org_id), int(candidate_id), int(role_id))
        app_id = app_cache.get(app_key)
        if app_id is None:
            app_id = bind.execute(
                sa.text(
                    """
                    SELECT id
                    FROM candidate_applications
                    WHERE organization_id = :organization_id
                      AND candidate_id = :candidate_id
                      AND role_id = :role_id
                    ORDER BY id
                    LIMIT 1
                    """
                ),
                {
                    "organization_id": org_id,
                    "candidate_id": candidate_id,
                    "role_id": role_id,
                },
            ).scalar()

            if app_id is None:
                bind.execute(
                    sa.text(
                        """
                        INSERT INTO candidate_applications (
                            organization_id,
                            candidate_id,
                            role_id,
                            status,
                            notes,
                            cv_file_url,
                            cv_filename,
                            cv_text,
                            cv_uploaded_at
                        ) VALUES (
                            :organization_id,
                            :candidate_id,
                            :role_id,
                            'applied',
                            NULL,
                            :cv_file_url,
                            :cv_filename,
                            :cv_text,
                            :cv_uploaded_at
                        )
                        """
                    ),
                    {
                        "organization_id": org_id,
                        "candidate_id": candidate_id,
                        "role_id": role_id,
                        "cv_file_url": row["cv_file_url"],
                        "cv_filename": row["cv_filename"],
                        "cv_text": row["cv_text"],
                        "cv_uploaded_at": row["cv_uploaded_at"],
                    },
                )
                app_id = bind.execute(
                    sa.text(
                        """
                        SELECT id
                        FROM candidate_applications
                        WHERE organization_id = :organization_id
                          AND candidate_id = :candidate_id
                          AND role_id = :role_id
                        ORDER BY id
                        LIMIT 1
                        """
                    ),
                    {
                        "organization_id": org_id,
                        "candidate_id": candidate_id,
                        "role_id": role_id,
                    },
                ).scalar()

            app_cache[app_key] = int(app_id)

        bind.execute(
            sa.text(
                """
                UPDATE assessments
                SET role_id = :role_id, application_id = :application_id
                WHERE id = :assessment_id
                """
            ),
            {
                "role_id": role_id,
                "application_id": app_id,
                "assessment_id": row["assessment_id"],
            },
        )

        task_id = row["task_id"]
        if task_id is None:
            continue
        exists = bind.execute(
            sa.text(
                """
                SELECT 1
                FROM role_tasks
                WHERE role_id = :role_id AND task_id = :task_id
                """
            ),
            {"role_id": role_id, "task_id": task_id},
        ).scalar()
        if not exists:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO role_tasks (role_id, task_id)
                    VALUES (:role_id, :task_id)
                    """
                ),
                {"role_id": role_id, "task_id": task_id},
            )


def upgrade():
    bind = op.get_bind()

    if not _table_exists(bind, "roles"):
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
    if not _index_exists(bind, "roles", "ix_roles_id"):
        op.create_index("ix_roles_id", "roles", ["id"])
    if not _index_exists(bind, "roles", "ix_roles_organization_id"):
        op.create_index("ix_roles_organization_id", "roles", ["organization_id"])

    if not _table_exists(bind, "candidate_applications"):
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
    if not _index_exists(bind, "candidate_applications", "ix_candidate_applications_id"):
        op.create_index("ix_candidate_applications_id", "candidate_applications", ["id"])
    if not _index_exists(bind, "candidate_applications", "ix_candidate_applications_org_id"):
        op.create_index("ix_candidate_applications_org_id", "candidate_applications", ["organization_id"])
    if not _index_exists(bind, "candidate_applications", "ix_candidate_applications_candidate_id"):
        op.create_index("ix_candidate_applications_candidate_id", "candidate_applications", ["candidate_id"])
    if not _index_exists(bind, "candidate_applications", "ix_candidate_applications_role_id"):
        op.create_index("ix_candidate_applications_role_id", "candidate_applications", ["role_id"])

    if not _table_exists(bind, "role_tasks"):
        op.create_table(
            "role_tasks",
            sa.Column("role_id", sa.Integer(), nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("role_id", "task_id"),
        )

    if not _column_exists(bind, "assessments", "role_id"):
        op.add_column("assessments", sa.Column("role_id", sa.Integer(), nullable=True))
    if not _column_exists(bind, "assessments", "application_id"):
        op.add_column("assessments", sa.Column("application_id", sa.Integer(), nullable=True))
    if not _column_exists(bind, "assessments", "is_timer_paused"):
        op.add_column(
            "assessments",
            sa.Column("is_timer_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if not _column_exists(bind, "assessments", "paused_at"):
        op.add_column("assessments", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True))
    if not _column_exists(bind, "assessments", "pause_reason"):
        op.add_column("assessments", sa.Column("pause_reason", sa.String(), nullable=True))
    if not _column_exists(bind, "assessments", "total_paused_seconds"):
        op.add_column(
            "assessments",
            sa.Column("total_paused_seconds", sa.Integer(), nullable=False, server_default=sa.text("0")),
        )

    if not _index_exists(bind, "assessments", "ix_assessments_role_id"):
        op.create_index("ix_assessments_role_id", "assessments", ["role_id"])
    if not _index_exists(bind, "assessments", "ix_assessments_application_id"):
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

    _backfill_legacy_role_data(bind)

    op.alter_column("assessments", "is_timer_paused", server_default=None)
    op.alter_column("assessments", "total_paused_seconds", server_default=None)
    op.alter_column("candidate_applications", "status", server_default=None)


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
