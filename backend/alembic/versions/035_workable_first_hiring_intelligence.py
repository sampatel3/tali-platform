"""Add Workable-first hiring intelligence schema.

Revision ID: 035_workable_first_hiring_intelligence
Revises: 034_add_application_score_cache_columns
Create Date: 2026-04-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "035_workable_first_hiring_intelligence"
down_revision = "035_add_role_scoring_criteria_threshold"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("fireflies_api_key_encrypted", sa.String(), nullable=True))
        batch.add_column(sa.Column("fireflies_webhook_secret", sa.String(), nullable=True))
        batch.add_column(sa.Column("fireflies_owner_email", sa.String(), nullable=True))
        batch.add_column(
            sa.Column(
                "fireflies_single_account_mode",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )

    with op.batch_alter_table("roles") as batch:
        batch.add_column(sa.Column("screening_pack_template", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("tech_interview_pack_template", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("auto_reject_enabled", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("auto_reject_threshold_100", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("workable_actor_member_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("workable_disqualify_reason_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("auto_reject_note_template", sa.Text(), nullable=True))

    with op.batch_alter_table("candidate_applications") as batch:
        batch.add_column(sa.Column("pre_screen_score_100", sa.Float(), nullable=True))
        batch.add_column(sa.Column("requirements_fit_score_100", sa.Float(), nullable=True))
        batch.add_column(sa.Column("pre_screen_recommendation", sa.String(), nullable=True))
        batch.add_column(sa.Column("pre_screen_evidence", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("auto_reject_state", sa.String(), nullable=True))
        batch.add_column(sa.Column("auto_reject_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("auto_reject_triggered_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("screening_pack", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("tech_interview_pack", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("screening_interview_summary", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("tech_interview_summary", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("interview_evidence_summary", sa.JSON(), nullable=True))

    op.create_table(
        "application_interviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("candidate_applications.id"), nullable=False),
        sa.Column("stage", sa.String(), nullable=False, server_default="screening"),
        sa.Column("source", sa.String(), nullable=False, server_default="manual"),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("provider_meeting_id", sa.String(), nullable=True),
        sa.Column("provider_url", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="linked"),
        sa.Column("transcript_text", sa.Text(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("speakers", sa.JSON(), nullable=True),
        sa.Column("provider_payload", sa.JSON(), nullable=True),
        sa.Column("meeting_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "ix_candidate_applications_org_outcome_pre_screen_sort",
        "candidate_applications",
        [
            "organization_id",
            "deleted_at",
            "application_outcome",
            "pre_screen_score_100",
            "pipeline_stage_updated_at",
            "id",
        ],
        unique=False,
    )
    op.create_index(
        "ix_candidate_applications_org_role_outcome_pre_screen_sort",
        "candidate_applications",
        [
            "organization_id",
            "role_id",
            "deleted_at",
            "application_outcome",
            "pre_screen_score_100",
            "pipeline_stage_updated_at",
            "id",
        ],
        unique=False,
    )
    op.create_index("ix_application_interviews_application_id", "application_interviews", ["application_id"], unique=False)
    op.create_index("ix_application_interviews_organization_id", "application_interviews", ["organization_id"], unique=False)
    op.create_index("ix_application_interviews_provider_meeting_id", "application_interviews", ["provider_meeting_id"], unique=False)

    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE candidate_applications
            SET pre_screen_score_100 = COALESCE(pre_screen_score_100, role_fit_score_cache_100),
                rank_score = COALESCE(pre_screen_score_100, role_fit_score_cache_100, rank_score)
            WHERE role_fit_score_cache_100 IS NOT NULL
            """
        )
    )

    with op.batch_alter_table("organizations") as batch:
        batch.alter_column(
            "fireflies_single_account_mode",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    op.drop_index("ix_application_interviews_provider_meeting_id", table_name="application_interviews")
    op.drop_index("ix_application_interviews_organization_id", table_name="application_interviews")
    op.drop_index("ix_application_interviews_application_id", table_name="application_interviews")
    op.drop_index("ix_candidate_applications_org_role_outcome_pre_screen_sort", table_name="candidate_applications")
    op.drop_index("ix_candidate_applications_org_outcome_pre_screen_sort", table_name="candidate_applications")
    op.drop_table("application_interviews")

    with op.batch_alter_table("candidate_applications") as batch:
        batch.drop_column("interview_evidence_summary")
        batch.drop_column("tech_interview_summary")
        batch.drop_column("screening_interview_summary")
        batch.drop_column("tech_interview_pack")
        batch.drop_column("screening_pack")
        batch.drop_column("auto_reject_triggered_at")
        batch.drop_column("auto_reject_reason")
        batch.drop_column("auto_reject_state")
        batch.drop_column("pre_screen_evidence")
        batch.drop_column("pre_screen_recommendation")
        batch.drop_column("requirements_fit_score_100")
        batch.drop_column("pre_screen_score_100")

    with op.batch_alter_table("roles") as batch:
        batch.drop_column("auto_reject_note_template")
        batch.drop_column("workable_disqualify_reason_id")
        batch.drop_column("workable_actor_member_id")
        batch.drop_column("auto_reject_threshold_100")
        batch.drop_column("auto_reject_enabled")
        batch.drop_column("tech_interview_pack_template")
        batch.drop_column("screening_pack_template")

    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("fireflies_single_account_mode")
        batch.drop_column("fireflies_owner_email")
        batch.drop_column("fireflies_webhook_secret")
        batch.drop_column("fireflies_api_key_encrypted")
