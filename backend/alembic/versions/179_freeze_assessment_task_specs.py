"""Freeze task specifications per assessment.

Revision ID: 179_freeze_assessment_task_specs
Revises: 178_candidate_runtime_session
"""

from __future__ import annotations

import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "179_freeze_assessment_task_specs"
down_revision = "178_candidate_runtime_session"
branch_labels = None
depends_on = None


_SNAPSHOT_FIELDS = (
    "id",
    "task_key",
    "name",
    "description",
    "role",
    "scenario",
    "duration_minutes",
    "starter_code",
    "test_code",
    "repo_structure",
    "evaluation_rubric",
    "extra_data",
    "calibration_prompt",
    "score_weights",
    "recruiter_weight_preset",
    "proctoring_enabled",
    "claude_budget_limit_usd",
)


def _digest(snapshot: dict) -> str:
    payload = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def upgrade() -> None:
    op.add_column(
        "assessments",
        sa.Column("task_spec_snapshot", sa.JSON(), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column("task_spec_snapshot_sha256", sa.String(length=64), nullable=True),
    )

    tasks = sa.table(
        "tasks",
        sa.column("id", sa.Integer()),
        sa.column("task_key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("role", sa.String()),
        sa.column("scenario", sa.Text()),
        sa.column("duration_minutes", sa.Integer()),
        sa.column("starter_code", sa.Text()),
        sa.column("test_code", sa.Text()),
        sa.column("repo_structure", sa.JSON()),
        sa.column("evaluation_rubric", sa.JSON()),
        sa.column("extra_data", sa.JSON()),
        sa.column("calibration_prompt", sa.Text()),
        sa.column("score_weights", sa.JSON()),
        sa.column("recruiter_weight_preset", sa.String()),
        sa.column("proctoring_enabled", sa.Boolean()),
        sa.column("claude_budget_limit_usd", sa.Float()),
    )
    assessments = sa.table(
        "assessments",
        sa.column("id", sa.Integer()),
        sa.column("task_id", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("task_spec_snapshot", sa.JSON()),
        sa.column("task_spec_snapshot_sha256", sa.String(length=64)),
    )

    connection = op.get_bind()
    task_snapshots: dict[int, tuple[dict, str]] = {}
    for row in connection.execute(sa.select(tasks)).mappings():
        snapshot = {"version": 1}
        for field in _SNAPSHOT_FIELDS:
            snapshot[field] = row[field]
        task_snapshots[int(row["id"])] = (snapshot, _digest(snapshot))

    assessment_rows = connection.execute(
        sa.select(assessments.c.id, assessments.c.task_id).where(
            assessments.c.task_id.is_not(None),
            assessments.c.task_spec_snapshot.is_(None),
        )
    ).mappings()
    for row in assessment_rows:
        frozen = task_snapshots.get(int(row["task_id"]))
        if frozen is None:
            continue
        snapshot, digest = frozen
        connection.execute(
            assessments.update()
            .where(assessments.c.id == row["id"])
            .values(
                task_spec_snapshot=snapshot,
                task_spec_snapshot_sha256=digest,
            )
        )


def downgrade() -> None:
    op.drop_column("assessments", "task_spec_snapshot_sha256")
    op.drop_column("assessments", "task_spec_snapshot")
