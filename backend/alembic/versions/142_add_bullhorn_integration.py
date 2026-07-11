"""Bullhorn ATS integration — models, secrets, stage map.

Additive schema for the Bullhorn integration (docs/BULLHORN_BUILD_PLAN.md §3).
All new columns are nullable / defaulted so this is a pure add on existing rows.

``organizations``:
  * bullhorn_username, bullhorn_client_id, bullhorn_rest_url (String)
  * bullhorn_client_secret, bullhorn_refresh_token (Text — Fernet CIPHERTEXT,
    same encrypt_text/decrypt_text mechanism as fireflies_api_key_encrypted)
  * bullhorn_connected (Boolean, default false)
  * bullhorn_last_sync_at (DateTime), bullhorn_last_sync_status (String),
    bullhorn_last_sync_summary (JSON), bullhorn_sync_progress (JSON)
  * bullhorn_event_subscription_id (String), bullhorn_event_request_id
    (String — destructive-queue checkpoint), bullhorn_config (JSON)

``candidates``:
  * bullhorn_candidate_id (String, indexed), bullhorn_data (JSON)

``candidate_applications``:
  * bullhorn_job_submission_id (String, indexed), bullhorn_status (String),
    bullhorn_status_local_write_at (DateTime — local-write-wins guard)

``roles``:
  * bullhorn_job_order_id (String, indexed), bullhorn_job_data (JSON)
  * unique (organization_id, bullhorn_job_order_id)

New table ``ats_stage_map``: per-org remote-status → Taali-stage mapping,
unique (org_id, ats, remote_status).

Indexes implied by ``index=True`` on the models are emitted explicitly here —
the autogenerate-style create_table does NOT create them.

Revision ID: 142_add_bullhorn_integration
Revises: 141_add_auth_hardening
Create Date: 2026-07-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "142_add_bullhorn_integration"
down_revision = "141_add_auth_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- organizations ---
    op.add_column("organizations", sa.Column("bullhorn_username", sa.String(), nullable=True))
    op.add_column("organizations", sa.Column("bullhorn_client_id", sa.String(), nullable=True))
    op.add_column("organizations", sa.Column("bullhorn_client_secret", sa.Text(), nullable=True))
    op.add_column("organizations", sa.Column("bullhorn_refresh_token", sa.Text(), nullable=True))
    op.add_column("organizations", sa.Column("bullhorn_rest_url", sa.String(), nullable=True))
    op.add_column(
        "organizations",
        sa.Column("bullhorn_connected", sa.Boolean(), nullable=True, server_default=sa.false()),
    )
    op.add_column("organizations", sa.Column("bullhorn_last_sync_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("organizations", sa.Column("bullhorn_last_sync_status", sa.String(), nullable=True))
    op.add_column("organizations", sa.Column("bullhorn_last_sync_summary", sa.JSON(), nullable=True))
    op.add_column("organizations", sa.Column("bullhorn_sync_progress", sa.JSON(), nullable=True))
    op.add_column("organizations", sa.Column("bullhorn_event_subscription_id", sa.String(), nullable=True))
    op.add_column("organizations", sa.Column("bullhorn_event_request_id", sa.String(), nullable=True))
    op.add_column("organizations", sa.Column("bullhorn_config", sa.JSON(), nullable=True))

    # --- candidates ---
    op.add_column("candidates", sa.Column("bullhorn_candidate_id", sa.String(), nullable=True))
    op.add_column("candidates", sa.Column("bullhorn_data", sa.JSON(), nullable=True))
    op.create_index(
        "ix_candidates_bullhorn_candidate_id",
        "candidates",
        ["bullhorn_candidate_id"],
        unique=False,
    )

    # --- candidate_applications ---
    op.add_column("candidate_applications", sa.Column("bullhorn_job_submission_id", sa.String(), nullable=True))
    op.add_column("candidate_applications", sa.Column("bullhorn_status", sa.String(), nullable=True))
    op.add_column(
        "candidate_applications",
        sa.Column("bullhorn_status_local_write_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_candidate_applications_bullhorn_job_submission_id",
        "candidate_applications",
        ["bullhorn_job_submission_id"],
        unique=False,
    )

    # --- roles ---
    op.add_column("roles", sa.Column("bullhorn_job_order_id", sa.String(), nullable=True))
    op.add_column("roles", sa.Column("bullhorn_job_data", sa.JSON(), nullable=True))
    op.create_index(
        "ix_roles_bullhorn_job_order_id",
        "roles",
        ["bullhorn_job_order_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_roles_org_bullhorn_job_order",
        "roles",
        ["organization_id", "bullhorn_job_order_id"],
    )

    # --- ats_stage_map ---
    op.create_table(
        "ats_stage_map",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("ats", sa.String(), nullable=False),
        sa.Column("remote_status", sa.String(), nullable=False),
        sa.Column("taali_stage", sa.String(), nullable=False),
        sa.Column("is_reject", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "ats", "remote_status", name="uq_ats_stage_map_org_ats_remote_status"
        ),
    )
    op.create_index("ix_ats_stage_map_id", "ats_stage_map", ["id"], unique=False)
    op.create_index("ix_ats_stage_map_org_id", "ats_stage_map", ["org_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ats_stage_map_org_id", table_name="ats_stage_map")
    op.drop_index("ix_ats_stage_map_id", table_name="ats_stage_map")
    op.drop_table("ats_stage_map")

    op.drop_constraint("uq_roles_org_bullhorn_job_order", "roles", type_="unique")
    op.drop_index("ix_roles_bullhorn_job_order_id", table_name="roles")
    op.drop_column("roles", "bullhorn_job_data")
    op.drop_column("roles", "bullhorn_job_order_id")

    op.drop_index(
        "ix_candidate_applications_bullhorn_job_submission_id",
        table_name="candidate_applications",
    )
    op.drop_column("candidate_applications", "bullhorn_status_local_write_at")
    op.drop_column("candidate_applications", "bullhorn_status")
    op.drop_column("candidate_applications", "bullhorn_job_submission_id")

    op.drop_index("ix_candidates_bullhorn_candidate_id", table_name="candidates")
    op.drop_column("candidates", "bullhorn_data")
    op.drop_column("candidates", "bullhorn_candidate_id")

    op.drop_column("organizations", "bullhorn_config")
    op.drop_column("organizations", "bullhorn_event_request_id")
    op.drop_column("organizations", "bullhorn_event_subscription_id")
    op.drop_column("organizations", "bullhorn_sync_progress")
    op.drop_column("organizations", "bullhorn_last_sync_summary")
    op.drop_column("organizations", "bullhorn_last_sync_status")
    op.drop_column("organizations", "bullhorn_last_sync_at")
    op.drop_column("organizations", "bullhorn_connected")
    op.drop_column("organizations", "bullhorn_rest_url")
    op.drop_column("organizations", "bullhorn_refresh_token")
    op.drop_column("organizations", "bullhorn_client_secret")
    op.drop_column("organizations", "bullhorn_client_id")
    op.drop_column("organizations", "bullhorn_username")
