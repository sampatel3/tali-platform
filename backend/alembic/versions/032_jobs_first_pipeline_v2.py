"""Add jobs-first pipeline fields, outcome model, events, and org feature flag.

Revision ID: 032_jobs_first_pipeline_v2
Revises: 031_add_taali_scores_and_assessment_voiding
Create Date: 2026-03-05
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "032_jobs_first_pipeline_v2"
down_revision = "031_add_taali_scores_and_assessment_voiding"
branch_labels = None
depends_on = None


def _map_legacy_status(status: str | None) -> tuple[str, str]:
    raw = str(status or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {"invited", "pending", "assessment_sent"}:
        return "invited", "open"
    if raw in {"in_progress", "started"}:
        return "in_assessment", "open"
    if raw in {"review", "completed", "completed_due_to_timeout", "scored"}:
        return "review", "open"
    if raw in {"rejected", "declined", "disqualified"}:
        return "review", "rejected"
    if raw in {"withdrawn"}:
        return "review", "withdrawn"
    if raw in {"hired", "offer_accepted"}:
        return "review", "hired"
    return "applied", "open"


def _status_from_pipeline(stage: str, outcome: str) -> str:
    if outcome in {"rejected", "withdrawn", "hired"}:
        return outcome
    if stage == "in_assessment":
        return "in_progress"
    return stage


def _normalize_stage(value: str | None) -> str | None:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return None
    return text


def upgrade() -> None:
    with op.batch_alter_table("candidate_applications") as batch:
        batch.add_column(sa.Column("pipeline_stage", sa.String(), nullable=True, server_default="applied"))
        batch.add_column(sa.Column("pipeline_stage_updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("pipeline_stage_source", sa.String(), nullable=True, server_default="system"))
        batch.add_column(sa.Column("application_outcome", sa.String(), nullable=True, server_default="open"))
        batch.add_column(sa.Column("application_outcome_updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("external_refs", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("external_stage_raw", sa.String(), nullable=True))
        batch.add_column(sa.Column("external_stage_normalized", sa.String(), nullable=True))
        batch.add_column(sa.Column("integration_sync_state", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("version", sa.Integer(), nullable=True, server_default="1"))

    with op.batch_alter_table("organizations") as batch:
        batch.add_column(
            sa.Column("recruiter_workflow_v2_enabled", sa.Boolean(), nullable=True, server_default=sa.text("false"))
        )

    op.create_table(
        "candidate_application_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("candidate_applications.id"), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("from_stage", sa.String(), nullable=True),
        sa.Column("to_stage", sa.String(), nullable=True),
        sa.Column("from_outcome", sa.String(), nullable=True),
        sa.Column("to_outcome", sa.String(), nullable=True),
        sa.Column("actor_type", sa.String(), nullable=False, server_default="system"),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("application_id", "idempotency_key", name="uq_application_event_idempotency_key"),
    )
    op.create_index("ix_candidate_application_events_application_id", "candidate_application_events", ["application_id"])
    op.create_index("ix_candidate_application_events_organization_id", "candidate_application_events", ["organization_id"])

    now = datetime.now(timezone.utc)
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT id, status, created_at, workable_candidate_id, workable_stage, last_synced_at
            FROM candidate_applications
            """
        )
    ).mappings().all()
    for row in rows:
        stage, outcome = _map_legacy_status(row.get("status"))
        stage_raw = str(row.get("workable_stage") or "").strip() or None
        stage_normalized = _normalize_stage(stage_raw)
        status_mirror = _status_from_pipeline(stage, outcome)
        ts = row.get("created_at") or now
        sync_payload = None
        if row.get("workable_candidate_id") or row.get("last_synced_at"):
            sync_payload = {
                "last_sync_at": (
                    row.get("last_synced_at").isoformat()
                    if hasattr(row.get("last_synced_at"), "isoformat")
                    else None
                ),
                "sync_status": "success" if row.get("last_synced_at") else "never_synced",
            }

        conn.execute(
            sa.text(
                """
                UPDATE candidate_applications
                SET pipeline_stage = :stage,
                    pipeline_stage_updated_at = COALESCE(pipeline_stage_updated_at, :ts),
                    pipeline_stage_source = COALESCE(pipeline_stage_source, 'system'),
                    application_outcome = :outcome,
                    application_outcome_updated_at = COALESCE(application_outcome_updated_at, :ts),
                    external_stage_raw = COALESCE(external_stage_raw, :stage_raw),
                    external_stage_normalized = COALESCE(external_stage_normalized, :stage_normalized),
                    integration_sync_state = COALESCE(integration_sync_state, :sync_payload),
                    version = COALESCE(version, 1),
                    status = :status_mirror
                WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "stage": stage,
                "outcome": outcome,
                "ts": ts,
                "stage_raw": stage_raw,
                "stage_normalized": stage_normalized,
                "sync_payload": sync_payload,
                "status_mirror": status_mirror,
            },
        )
        conn.execute(
            sa.text(
                """
                INSERT INTO candidate_application_events (
                    application_id,
                    organization_id,
                    event_type,
                    to_stage,
                    to_outcome,
                    actor_type,
                    reason,
                    metadata,
                    created_at
                )
                SELECT
                    id,
                    organization_id,
                    'pipeline_initialized',
                    :stage,
                    :outcome,
                    'system',
                    'Initialized during recruiter workflow v2 migration',
                    :metadata,
                    :ts
                FROM candidate_applications
                WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "stage": stage,
                "outcome": outcome,
                "metadata": {
                    "legacy_status": str(row.get("status") or ""),
                    "migration": "032_jobs_first_pipeline_v2",
                },
                "ts": ts,
            },
        )

    with op.batch_alter_table("candidate_applications") as batch:
        batch.alter_column("pipeline_stage", existing_type=sa.String(), nullable=False, server_default=None)
        batch.alter_column("pipeline_stage_updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.alter_column("pipeline_stage_source", existing_type=sa.String(), nullable=False, server_default=None)
        batch.alter_column("application_outcome", existing_type=sa.String(), nullable=False, server_default=None)
        batch.alter_column("application_outcome_updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.alter_column("version", existing_type=sa.Integer(), nullable=False, server_default=None)

    with op.batch_alter_table("organizations") as batch:
        batch.alter_column("recruiter_workflow_v2_enabled", existing_type=sa.Boolean(), nullable=False, server_default=None)


def downgrade() -> None:
    op.drop_index("ix_candidate_application_events_organization_id", table_name="candidate_application_events")
    op.drop_index("ix_candidate_application_events_application_id", table_name="candidate_application_events")
    op.drop_table("candidate_application_events")

    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("recruiter_workflow_v2_enabled")

    with op.batch_alter_table("candidate_applications") as batch:
        batch.drop_column("version")
        batch.drop_column("integration_sync_state")
        batch.drop_column("external_stage_normalized")
        batch.drop_column("external_stage_raw")
        batch.drop_column("external_refs")
        batch.drop_column("application_outcome_updated_at")
        batch.drop_column("application_outcome")
        batch.drop_column("pipeline_stage_source")
        batch.drop_column("pipeline_stage_updated_at")
        batch.drop_column("pipeline_stage")
