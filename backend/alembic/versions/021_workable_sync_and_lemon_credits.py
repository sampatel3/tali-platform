"""Add Workable sync and Lemon credits billing fields.

Revision ID: 021_workable_sync_and_lemon_credits
Revises: 020_add_assessment_terminal_cli_fields
Create Date: 2026-02-15 18:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "021_workable_sync_and_lemon_credits"
down_revision = "020_add_assessment_terminal_cli_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("workable_last_sync_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("workable_last_sync_status", sa.String(), nullable=True))
        batch.add_column(sa.Column("workable_last_sync_summary", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("billing_provider", sa.String(), nullable=False, server_default="lemon"))
        batch.add_column(sa.Column("billing_config", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("credits_balance", sa.Integer(), nullable=False, server_default="0"))

    with op.batch_alter_table("roles") as batch:
        batch.add_column(sa.Column("source", sa.String(), nullable=False, server_default="manual"))
        batch.add_column(sa.Column("workable_job_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("workable_job_data", sa.JSON(), nullable=True))
        batch.create_index("ix_roles_workable_job_id", ["workable_job_id"], unique=False)
        batch.create_unique_constraint("uq_roles_org_workable_job", ["organization_id", "workable_job_id"])

    with op.batch_alter_table("candidate_applications") as batch:
        batch.add_column(sa.Column("source", sa.String(), nullable=False, server_default="manual"))
        batch.add_column(sa.Column("workable_candidate_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("workable_stage", sa.String(), nullable=True))
        batch.add_column(sa.Column("workable_score_raw", sa.Float(), nullable=True))
        batch.add_column(sa.Column("workable_score", sa.Float(), nullable=True))
        batch.add_column(sa.Column("workable_score_source", sa.String(), nullable=True))
        batch.add_column(sa.Column("rank_score", sa.Float(), nullable=True))
        batch.add_column(sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_candidate_applications_workable_candidate_id", ["workable_candidate_id"], unique=False)

    with op.batch_alter_table("assessments") as batch:
        batch.add_column(sa.Column("invite_channel", sa.String(), nullable=False, server_default="manual"))
        batch.add_column(sa.Column("invite_sent_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("credit_consumed_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "billing_credit_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("external_ref", sa.String(), nullable=True),
        sa.Column("assessment_id", sa.Integer(), sa.ForeignKey("assessments.id"), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_billing_credit_ledger_id", "billing_credit_ledger", ["id"], unique=False)
    op.create_index(
        "ix_billing_credit_ledger_organization_id",
        "billing_credit_ledger",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_billing_credit_ledger_assessment_id",
        "billing_credit_ledger",
        ["assessment_id"],
        unique=False,
    )
    op.create_index(
        "ix_billing_credit_ledger_external_ref",
        "billing_credit_ledger",
        ["external_ref"],
        unique=True,
    )

    with op.batch_alter_table("organizations") as batch:
        batch.alter_column("billing_provider", server_default=None)
        batch.alter_column("credits_balance", server_default=None)
    with op.batch_alter_table("roles") as batch:
        batch.alter_column("source", server_default=None)
    with op.batch_alter_table("candidate_applications") as batch:
        batch.alter_column("source", server_default=None)
    with op.batch_alter_table("assessments") as batch:
        batch.alter_column("invite_channel", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_billing_credit_ledger_external_ref", table_name="billing_credit_ledger")
    op.drop_index("ix_billing_credit_ledger_assessment_id", table_name="billing_credit_ledger")
    op.drop_index("ix_billing_credit_ledger_organization_id", table_name="billing_credit_ledger")
    op.drop_index("ix_billing_credit_ledger_id", table_name="billing_credit_ledger")
    op.drop_table("billing_credit_ledger")

    with op.batch_alter_table("assessments") as batch:
        batch.drop_column("credit_consumed_at")
        batch.drop_column("invite_sent_at")
        batch.drop_column("invite_channel")

    with op.batch_alter_table("candidate_applications") as batch:
        batch.drop_index("ix_candidate_applications_workable_candidate_id")
        batch.drop_column("last_synced_at")
        batch.drop_column("rank_score")
        batch.drop_column("workable_score_source")
        batch.drop_column("workable_score")
        batch.drop_column("workable_score_raw")
        batch.drop_column("workable_stage")
        batch.drop_column("workable_candidate_id")
        batch.drop_column("source")

    with op.batch_alter_table("roles") as batch:
        batch.drop_constraint("uq_roles_org_workable_job", type_="unique")
        batch.drop_index("ix_roles_workable_job_id")
        batch.drop_column("workable_job_data")
        batch.drop_column("workable_job_id")
        batch.drop_column("source")

    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("credits_balance")
        batch.drop_column("billing_config")
        batch.drop_column("billing_provider")
        batch.drop_column("workable_last_sync_summary")
        batch.drop_column("workable_last_sync_status")
        batch.drop_column("workable_last_sync_at")
