"""Serialize runtime mutations and freeze assessment CV evidence.

Revision ID: 180_harden_runtime_state
Revises: 179_freeze_assessment_task_specs
"""

from alembic import op
import sqlalchemy as sa


revision = "180_harden_runtime_state"
down_revision = "179_freeze_assessment_task_specs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("assessments") as batch:
        batch.add_column(sa.Column("runtime_operation_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("runtime_operation_kind", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column("runtime_operation_started_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("cv_text_snapshot", sa.Text(), nullable=True))
        batch.create_check_constraint(
            "ck_assessments_submission_artifact_complete",
            "((submission_artifact IS NULL AND submission_artifact_sha256 IS NULL AND submission_artifact_captured_at IS NULL) OR "
            "(submission_artifact IS NOT NULL AND submission_artifact_sha256 IS NOT NULL AND submission_artifact_captured_at IS NOT NULL))",
        )
        batch.create_check_constraint(
            "ck_assessments_task_snapshot_complete",
            "((task_spec_snapshot IS NULL AND task_spec_snapshot_sha256 IS NULL) OR "
            "(task_spec_snapshot IS NOT NULL AND task_spec_snapshot_sha256 IS NOT NULL))",
        )
        batch.create_check_constraint(
            "ck_assessments_candidate_session_complete",
            "((candidate_session_hash IS NULL AND candidate_session_bound_at IS NULL) OR "
            "(candidate_session_hash IS NOT NULL AND candidate_session_bound_at IS NOT NULL))",
        )
        batch.create_check_constraint(
            "ck_assessments_runtime_operation_complete",
            "((runtime_operation_id IS NULL AND runtime_operation_kind IS NULL AND runtime_operation_started_at IS NULL) OR "
            "(runtime_operation_id IS NOT NULL AND runtime_operation_kind IS NOT NULL AND runtime_operation_started_at IS NOT NULL))",
        )

    # Freeze the best CV evidence already associated with every historical
    # assessment. Prefer the role-scoped application copy; candidate.cv_text is
    # the legacy fallback. Future starts/uploads write this column directly.
    assessments = sa.table(
        "assessments",
        sa.column("application_id", sa.Integer()),
        sa.column("candidate_id", sa.Integer()),
        sa.column("cv_text_snapshot", sa.Text()),
    )
    applications = sa.table(
        "candidate_applications",
        sa.column("id", sa.Integer()),
        sa.column("cv_text", sa.Text()),
    )
    candidates = sa.table(
        "candidates",
        sa.column("id", sa.Integer()),
        sa.column("cv_text", sa.Text()),
    )
    application_cv = (
        sa.select(applications.c.cv_text)
        .where(applications.c.id == assessments.c.application_id)
        .scalar_subquery()
    )
    candidate_cv = (
        sa.select(candidates.c.cv_text)
        .where(candidates.c.id == assessments.c.candidate_id)
        .scalar_subquery()
    )
    op.get_bind().execute(
        assessments.update()
        .where(assessments.c.cv_text_snapshot.is_(None))
        .values(cv_text_snapshot=sa.func.coalesce(application_cv, candidate_cv))
    )


def downgrade() -> None:
    with op.batch_alter_table("assessments") as batch:
        batch.drop_constraint("ck_assessments_runtime_operation_complete", type_="check")
        batch.drop_constraint("ck_assessments_candidate_session_complete", type_="check")
        batch.drop_constraint("ck_assessments_task_snapshot_complete", type_="check")
        batch.drop_constraint("ck_assessments_submission_artifact_complete", type_="check")
        batch.drop_column("cv_text_snapshot")
        batch.drop_column("runtime_operation_started_at")
        batch.drop_column("runtime_operation_kind")
        batch.drop_column("runtime_operation_id")
