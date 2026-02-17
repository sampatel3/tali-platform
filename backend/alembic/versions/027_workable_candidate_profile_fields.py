"""Add rich candidate profile columns and application Workable fields.

Revision ID: 027_workable_candidate_profile_fields
Revises: 026_workable_sync_cancel
Create Date: 2026-02-17

"""

from alembic import op
import sqlalchemy as sa


revision = "027_workable_candidate_profile_fields"
down_revision = "026_workable_sync_cancel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("candidates") as batch:
        batch.add_column(sa.Column("headline", sa.String(), nullable=True))
        batch.add_column(sa.Column("image_url", sa.String(), nullable=True))
        batch.add_column(sa.Column("location_city", sa.String(), nullable=True))
        batch.add_column(sa.Column("location_country", sa.String(), nullable=True))
        batch.add_column(sa.Column("phone", sa.String(), nullable=True))
        batch.add_column(sa.Column("profile_url", sa.String(), nullable=True))
        batch.add_column(sa.Column("social_profiles", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("tags", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("skills", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("education_entries", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("experience_entries", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("summary", sa.Text(), nullable=True))
        batch.add_column(sa.Column("workable_enriched", sa.Boolean(), server_default="0", nullable=False))
        batch.add_column(sa.Column("workable_created_at", sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table("candidate_applications") as batch:
        batch.add_column(sa.Column("workable_sourced", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("workable_profile_url", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("candidate_applications") as batch:
        batch.drop_column("workable_profile_url")
        batch.drop_column("workable_sourced")

    with op.batch_alter_table("candidates") as batch:
        batch.drop_column("workable_created_at")
        batch.drop_column("workable_enriched")
        batch.drop_column("summary")
        batch.drop_column("experience_entries")
        batch.drop_column("education_entries")
        batch.drop_column("skills")
        batch.drop_column("tags")
        batch.drop_column("social_profiles")
        batch.drop_column("profile_url")
        batch.drop_column("phone")
        batch.drop_column("location_country")
        batch.drop_column("location_city")
        batch.drop_column("image_url")
        batch.drop_column("headline")
