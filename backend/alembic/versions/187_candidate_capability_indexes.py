"""Build candidate-capability lookup indexes without blocking writes.

Revision ID: 187_candidate_capability_indexes
Revises: 186_candidate_action_provenance
Create Date: 2026-07-22

This revision contains only idempotent concurrent index work. PostgreSQL leaves
an INVALID index behind when a concurrent build fails; ``IF NOT EXISTS`` alone
would silently accept that unusable index on retry. Each build therefore removes
only an invalid same-name index before retrying, while preserving every valid
index created by an earlier interrupted attempt.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "187_candidate_capability_indexes"
down_revision = "186_candidate_action_provenance"
branch_labels = None
depends_on = None


_INDEXES: tuple[tuple[str, str, str], ...] = (
    (
        "ix_share_links_view_role_id",
        "share_links",
        "view_role_id",
    ),
    (
        "ix_roles_related_source_role_id",
        "roles",
        "related_source_role_id",
    ),
    (
        "ix_sister_role_evaluations_candidate_id",
        "sister_role_evaluations",
        "candidate_id",
    ),
    (
        "ix_sister_role_evaluations_ats_application_id",
        "sister_role_evaluations",
        "ats_application_id",
    ),
    (
        "ix_sister_role_evaluations_deleted_at",
        "sister_role_evaluations",
        "deleted_at",
    ),
    (
        "ix_sister_evaluations_role_membership_state",
        "sister_role_evaluations",
        "role_id, deleted_at, application_outcome, pipeline_stage",
    ),
    (
        "ix_candidate_application_events_role_id",
        "candidate_application_events",
        "role_id",
    ),
    (
        "ix_candidate_application_events_agent_decision_id",
        "candidate_application_events",
        "agent_decision_id",
    ),
    (
        "ix_application_events_org_role_created",
        "candidate_application_events",
        "organization_id, role_id, created_at",
    ),
)

_LIVE_MEMBERSHIP_INDEX = "uq_sister_evaluations_live_role_candidate"


def _run_concurrently(sql: str) -> None:
    with op.get_context().autocommit_block():
        op.execute(sql)


def _create_index_concurrently(name: str, sql: str) -> None:
    """Create a valid index, recovering an interrupted concurrent build."""

    invalid = op.get_bind().execute(
        sa.text(
            """
            SELECT NOT (index_state.indisvalid AND index_state.indisready)
            FROM pg_index AS index_state
            WHERE index_state.indexrelid = to_regclass(:index_name)
            """
        ),
        {"index_name": name},
    ).scalar_one_or_none()
    if invalid:
        _run_concurrently(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    _run_concurrently(sql)


def upgrade() -> None:
    for name, table, columns in _INDEXES:
        _create_index_concurrently(
            name,
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
            f"ON {table} ({columns})",
        )
    # Historical memberships remain as soft-deleted audit shadows. Exactly
    # one live row, however, is the role's canonical pool membership. Revision
    # 185 deterministically collapsed legacy duplicates and its compatibility
    # trigger serializes mixed-version writers, making this concurrent unique
    # build safe without blocking ordinary reads/writes.
    _create_index_concurrently(
        _LIVE_MEMBERSHIP_INDEX,
        f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
        f"{_LIVE_MEMBERSHIP_INDEX} "
        "ON sister_role_evaluations (role_id, candidate_id) "
        "WHERE deleted_at IS NULL",
    )
    # Validation takes only a SHARE UPDATE EXCLUSIVE lock and leaves ordinary
    # inserts/updates available.  The constraints already protected every new
    # row from revision 186 onward.
    op.execute(
        "ALTER TABLE candidate_application_events "
        "VALIDATE CONSTRAINT fk_candidate_application_events_role_id"
    )
    op.execute(
        "ALTER TABLE candidate_application_events "
        "VALIDATE CONSTRAINT fk_candidate_application_events_agent_decision_id"
    )


def downgrade() -> None:
    _run_concurrently(
        f"DROP INDEX CONCURRENTLY IF EXISTS {_LIVE_MEMBERSHIP_INDEX}"
    )
    for name, _table, _columns in reversed(_INDEXES):
        _run_concurrently(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
