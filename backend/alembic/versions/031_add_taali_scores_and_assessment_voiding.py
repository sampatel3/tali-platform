"""Add TAALI score fields and assessment voiding metadata.

Revision ID: 031_add_taali_scores_and_assessment_voiding
Revises: 030_org_preferences_and_calibration_warmup
Create Date: 2026-03-02
"""

from alembic import op
import sqlalchemy as sa


revision = "031_add_taali_scores_and_assessment_voiding"
down_revision = "030_org_preferences_and_calibration_warmup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assessments", sa.Column("assessment_score", sa.Float(), nullable=True))
    op.add_column("assessments", sa.Column("taali_score", sa.Float(), nullable=True))
    op.add_column(
        "assessments",
        sa.Column("is_voided", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("assessments", sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("assessments", sa.Column("void_reason", sa.Text(), nullable=True))
    op.add_column("assessments", sa.Column("superseded_by_assessment_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_assessments_superseded_by_assessment_id",
        "assessments",
        "assessments",
        ["superseded_by_assessment_id"],
        ["id"],
    )

    conn = op.get_bind()

    conn.execute(
        sa.text(
            """
            UPDATE assessments
            SET assessment_score = COALESCE(
                assessment_score,
                final_score,
                CASE WHEN score IS NOT NULL THEN score * 10.0 ELSE NULL END
            )
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE assessments
            SET taali_score = ROUND(
                CAST(
                    CASE
                        WHEN taali_score IS NOT NULL THEN taali_score
                        WHEN assessment_score IS NOT NULL AND cv_job_match_score IS NOT NULL THEN (assessment_score + cv_job_match_score) / 2.0
                        WHEN assessment_score IS NOT NULL THEN assessment_score
                        ELSE NULL
                    END AS numeric
                ),
                1
            )::double precision
            """
        )
    )

    duplicate_rows = conn.execute(
        sa.text(
            """
            SELECT id, candidate_id, role_id, created_at, completed_at, updated_at
            FROM assessments
            WHERE role_id IS NOT NULL
            ORDER BY candidate_id ASC, role_id ASC, COALESCE(completed_at, created_at) DESC, id DESC
            """
        )
    ).mappings().all()

    keep_by_key: dict[tuple[int, int], int] = {}
    for row in duplicate_rows:
        key = (row["candidate_id"], row["role_id"])
        if key not in keep_by_key:
            keep_by_key[key] = row["id"]
            continue
        conn.execute(
            sa.text(
                """
                UPDATE assessments
                SET is_voided = :is_voided,
                    voided_at = :voided_at,
                    void_reason = :void_reason,
                    superseded_by_assessment_id = :superseded_by_assessment_id
                WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "is_voided": True,
                "voided_at": row["updated_at"] or row["completed_at"] or row["created_at"],
                "void_reason": "Auto-voided during TAALI score migration: superseded legacy duplicate attempt",
                "superseded_by_assessment_id": keep_by_key[key],
            },
        )

    op.create_index(
        "uq_assessments_candidate_role_active",
        "assessments",
        ["candidate_id", "role_id"],
        unique=True,
        sqlite_where=sa.text("role_id IS NOT NULL AND is_voided = 0"),
        postgresql_where=sa.text("role_id IS NOT NULL AND is_voided = false"),
    )


def downgrade() -> None:
    op.drop_index("uq_assessments_candidate_role_active", table_name="assessments")
    op.drop_constraint(
        "fk_assessments_superseded_by_assessment_id",
        "assessments",
        type_="foreignkey",
    )
    op.drop_column("assessments", "superseded_by_assessment_id")
    op.drop_column("assessments", "void_reason")
    op.drop_column("assessments", "voided_at")
    op.drop_column("assessments", "is_voided")
    op.drop_column("assessments", "taali_score")
    op.drop_column("assessments", "assessment_score")
