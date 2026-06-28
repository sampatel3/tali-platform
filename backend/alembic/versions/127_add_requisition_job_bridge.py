"""Requisition -> inactive-job bridge: ref code + job status.

The requisition publish step now also creates an INACTIVE Taali job (a draft
``Role``) so the recruiter can see it in Jobs and copy its spec to share with
Workable (Workable has no job-creation API). A short, human-friendly ``ref_code``
is stamped on the brief and embedded in that shareable spec; when the recruiter
posts the job in Workable and it syncs back, the import scans the description for
the code and adopts the draft job — flipping it from ``draft`` to ``open`` —
instead of creating a duplicate. ``roles.job_status`` carries that lifecycle
(draft -> open -> filled / filled_external / cancelled); NULL means a legacy /
Workable-synced role whose state is derived from ``workable_job_data`` as before.

Adds:
  * ``role_briefs.ref_code`` — String, unique, indexed, nullable (null until the
    first publish; minted once and reused). The match key for the Workable bridge.
  * ``roles.job_status`` — String, indexed, nullable (null = legacy/derive).

Indexes are emitted explicitly via op.create_index (mirrors 126 — ``index=True``
on the model column does not auto-create the index in the migration).

Revision ID: 127_add_requisition_job_bridge
Revises: 126_add_client_intake_token
Create Date: 2026-06-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "127_add_requisition_job_bridge"
down_revision = "126_add_client_intake_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "role_briefs",
        sa.Column("ref_code", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_role_briefs_ref_code",
        "role_briefs",
        ["ref_code"],
        unique=True,
    )
    op.add_column(
        "roles",
        sa.Column("job_status", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_roles_job_status",
        "roles",
        ["job_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_roles_job_status", table_name="roles")
    op.drop_column("roles", "job_status")
    op.drop_index("ix_role_briefs_ref_code", table_name="role_briefs")
    op.drop_column("role_briefs", "ref_code")
