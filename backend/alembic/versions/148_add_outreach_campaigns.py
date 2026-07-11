"""Outreach campaigns + messages: the draft/approve/send/track layer.

Revision ID: 145_add_outreach_campaigns
Revises: 144_add_email_suppressions_and_prospects
"""

from alembic import op
import sqlalchemy as sa

revision = "148_add_outreach_campaigns"
down_revision = "147_add_offer_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outreach_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "role_id", sa.Integer(), sa.ForeignKey("roles.id"), nullable=True
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("brief", sa.Text(), nullable=True),
        sa.Column("job_page_token", sa.String(), nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("counts", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_outreach_campaigns_organization_id",
        "outreach_campaigns",
        ["organization_id"],
    )
    op.create_index(
        "ix_outreach_campaigns_role_id", "outreach_campaigns", ["role_id"]
    )

    op.create_table(
        "outreach_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("outreach_campaigns.id"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "prospect_id",
            sa.Integer(),
            sa.ForeignKey("prospects.id"),
            nullable=True,
        ),
        sa.Column(
            "candidate_id",
            sa.Integer(),
            sa.ForeignKey("candidates.id"),
            nullable=True,
        ),
        sa.Column(
            "source_application_id",
            sa.Integer(),
            sa.ForeignKey("candidate_applications.id"),
            nullable=True,
        ),
        sa.Column("recipient_name", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("resend_email_id", sa.String(), nullable=True),
        sa.Column("interest_token", sa.String(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("interested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "campaign_id", "email", name="uq_outreach_message_campaign_email"
        ),
    )
    op.create_index(
        "ix_outreach_messages_campaign_id", "outreach_messages", ["campaign_id"]
    )
    op.create_index(
        "ix_outreach_messages_organization_id",
        "outreach_messages",
        ["organization_id"],
    )
    op.create_index(
        "ix_outreach_messages_resend_email_id",
        "outreach_messages",
        ["resend_email_id"],
        unique=True,
    )
    op.create_index(
        "ix_outreach_messages_interest_token",
        "outreach_messages",
        ["interest_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outreach_messages_interest_token", table_name="outreach_messages"
    )
    op.drop_index(
        "ix_outreach_messages_resend_email_id", table_name="outreach_messages"
    )
    op.drop_index(
        "ix_outreach_messages_organization_id", table_name="outreach_messages"
    )
    op.drop_index(
        "ix_outreach_messages_campaign_id", table_name="outreach_messages"
    )
    op.drop_table("outreach_messages")
    op.drop_index(
        "ix_outreach_campaigns_role_id", table_name="outreach_campaigns"
    )
    op.drop_index(
        "ix_outreach_campaigns_organization_id", table_name="outreach_campaigns"
    )
    op.drop_table("outreach_campaigns")
