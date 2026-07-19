"""Bind score-job attempts to their durable scoring batch.

Revision ID: 192_scoring_batch_job_owner
Revises: 191_task_repo_identity
Create Date: 2026-07-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "192_scoring_batch_job_owner"
down_revision = "191_task_repo_identity"
branch_labels = None
depends_on = None


_FK_NAME = "fk_cv_score_jobs_batch_run_id"
_SUPPORTED_DIALECTS = frozenset({"postgresql", "sqlite"})


def _add_column_and_foreign_key(dialect: str) -> None:
    column = sa.Column("batch_run_id", sa.Integer(), nullable=True)
    if dialect == "sqlite":
        with op.batch_alter_table("cv_score_jobs") as batch_op:
            batch_op.add_column(column)
            batch_op.create_foreign_key(
                _FK_NAME,
                "background_job_runs",
                ["batch_run_id"],
                ["id"],
                ondelete="SET NULL",
            )
        return

    op.add_column("cv_score_jobs", column)
    # Every existing value is NULL, so validation is cheap.  NOT VALID keeps
    # the constraint-add lock brief on a large live score-job history.
    op.execute(
        sa.text(
            f"ALTER TABLE cv_score_jobs ADD CONSTRAINT {_FK_NAME} "
            "FOREIGN KEY (batch_run_id) REFERENCES background_job_runs (id) "
            "ON DELETE SET NULL NOT VALID"
        )
    )
    op.execute(sa.text(f"ALTER TABLE cv_score_jobs VALIDATE CONSTRAINT {_FK_NAME}"))


def upgrade() -> None:
    dialect = str(op.get_bind().dialect.name)
    if dialect not in _SUPPORTED_DIALECTS:
        raise RuntimeError(
            "Revision 192 supports only PostgreSQL and SQLite; refusing to "
            f"add scoring-batch ownership on {dialect!r}."
        )
    _add_column_and_foreign_key(dialect)


def downgrade() -> None:
    dialect = str(op.get_bind().dialect.name)
    if dialect not in _SUPPORTED_DIALECTS:
        raise RuntimeError(
            "Revision 192 supports only PostgreSQL and SQLite; refusing to "
            f"remove scoring-batch ownership on {dialect!r}."
        )
    if dialect == "sqlite":
        with op.batch_alter_table("cv_score_jobs") as batch_op:
            batch_op.drop_constraint(_FK_NAME, type_="foreignkey")
            batch_op.drop_column("batch_run_id")
        return
    op.drop_constraint(_FK_NAME, "cv_score_jobs", type_="foreignkey")
    op.drop_column("cv_score_jobs", "batch_run_id")
