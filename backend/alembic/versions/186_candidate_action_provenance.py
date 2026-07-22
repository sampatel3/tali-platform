"""Add logical-role and effect provenance to candidate actions.

Revision ID: 186_candidate_action_provenance
Revises: 185_related_role_membership
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "186_candidate_action_provenance"
down_revision = "185_related_role_membership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_decisions",
        sa.Column(
            "resolution_metadata",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "candidate_application_events",
        sa.Column("role_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "candidate_application_events",
        sa.Column("agent_decision_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "candidate_application_events",
        sa.Column("target_stage", sa.String(), nullable=True),
    )
    op.add_column(
        "candidate_application_events",
        sa.Column("effect_status", sa.String(), nullable=True),
    )

    # Every historical event already belongs to a concrete application.  Use
    # the explicitly recorded acting role when it is a valid role in the same
    # organization; otherwise retain the application's role.  JSON values are
    # length- and digit-guarded before casts so malformed legacy metadata cannot
    # abort the production migration.
    op.execute(
        """
        UPDATE candidate_application_events AS event
        SET role_id = COALESCE(
                (
                    SELECT role.id
                    FROM roles AS role
                    WHERE role.organization_id = event.organization_id
                      AND role.id = CASE
                          WHEN COALESCE(event.metadata ->> 'acting_role_id', '')
                                   ~ '^[1-9][0-9]{0,8}$'
                          THEN (event.metadata ->> 'acting_role_id')::INTEGER
                          WHEN COALESCE(event.metadata ->> 'role_id', '')
                                   ~ '^[1-9][0-9]{0,8}$'
                          THEN (event.metadata ->> 'role_id')::INTEGER
                          ELSE NULL
                      END
                ),
                application.role_id
            ),
            agent_decision_id = (
                SELECT decision.id
                FROM agent_decisions AS decision
                WHERE decision.organization_id = event.organization_id
                  AND decision.application_id = event.application_id
                  AND decision.id = CASE
                      WHEN COALESCE(event.metadata ->> 'agent_decision_id', '')
                               ~ '^[1-9][0-9]{0,17}$'
                      THEN (event.metadata ->> 'agent_decision_id')::BIGINT
                      WHEN COALESCE(event.metadata ->> 'decision_id', '')
                               ~ '^[1-9][0-9]{0,17}$'
                      THEN (event.metadata ->> 'decision_id')::BIGINT
                      ELSE NULL
                  END
            ),
            target_stage = COALESCE(
                NULLIF(BTRIM(event.metadata ->> 'target_stage'), ''),
                NULLIF(BTRIM(event.metadata ->> 'workable_target_stage'), ''),
                NULLIF(BTRIM(event.to_stage), ''),
                NULLIF(BTRIM(event.to_outcome), '')
            ),
            effect_status = CASE
                WHEN LOWER(event.event_type) LIKE '%failed%' THEN 'failed'
                WHEN LOWER(event.event_type) LIKE '%skipped%' THEN 'skipped'
                WHEN LOWER(event.event_type) IN (
                    'pipeline_stage_changed',
                    'application_outcome_changed',
                    'role_pipeline_stage_changed',
                    'role_application_outcome_changed',
                    'workable_moved',
                    'bullhorn_moved',
                    'workable_disqualified',
                    'bullhorn_rejected',
                    'assessment_invite_sent',
                    'assessment_invite_resent'
                ) THEN 'confirmed'
                ELSE NULL
            END
        FROM candidate_applications AS application
        WHERE application.id = event.application_id
        """
    )

    op.alter_column(
        "candidate_application_events",
        "role_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_candidate_application_events_role_id",
        "candidate_application_events",
        "roles",
        ["role_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_candidate_application_events_agent_decision_id",
        "candidate_application_events",
        "agent_decisions",
        ["agent_decision_id"],
        ["id"],
    )
    op.create_index(
        "ix_candidate_application_events_role_id",
        "candidate_application_events",
        ["role_id"],
    )
    op.create_index(
        "ix_candidate_application_events_agent_decision_id",
        "candidate_application_events",
        ["agent_decision_id"],
    )
    op.create_index(
        "ix_application_events_org_role_created",
        "candidate_application_events",
        ["organization_id", "role_id", "created_at"],
    )
    op.drop_constraint(
        "uq_application_event_idempotency_key",
        "candidate_application_events",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_application_event_role_idempotency_key",
        "candidate_application_events",
        ["application_id", "role_id", "idempotency_key"],
    )

    # Queue admission also serializes the logical application, but retain a
    # database invariant as the final defence against alternate producers or
    # worker races. Preserve every historical row and close only redundant
    # active cards, preferring an already-processing action over a pending one.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY role_id, application_id
                       ORDER BY CASE WHEN status = 'processing' THEN 0 ELSE 1 END,
                                created_at DESC,
                                id DESC
                   ) AS active_rank
            FROM agent_decisions
            WHERE status IN ('pending', 'processing')
        )
        UPDATE agent_decisions AS decision
        SET status = 'discarded',
            resolved_at = COALESCE(decision.resolved_at, CURRENT_TIMESTAMP),
            resolution_note = COALESCE(
                NULLIF(BTRIM(decision.resolution_note), ''),
                'Superseded during active-decision uniqueness migration'
            )
        FROM ranked
        WHERE decision.id = ranked.id
          AND ranked.active_rank > 1
        """
    )
    op.create_index(
        "uq_agent_decisions_active_role_application",
        "agent_decisions",
        ["role_id", "application_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_agent_decisions_active_role_application",
        table_name="agent_decisions",
    )
    op.drop_constraint(
        "uq_application_event_role_idempotency_key",
        "candidate_application_events",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_application_event_idempotency_key",
        "candidate_application_events",
        ["application_id", "idempotency_key"],
    )
    op.drop_index(
        "ix_application_events_org_role_created",
        table_name="candidate_application_events",
    )
    op.drop_index(
        "ix_candidate_application_events_agent_decision_id",
        table_name="candidate_application_events",
    )
    op.drop_index(
        "ix_candidate_application_events_role_id",
        table_name="candidate_application_events",
    )
    op.drop_constraint(
        "fk_candidate_application_events_agent_decision_id",
        "candidate_application_events",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_candidate_application_events_role_id",
        "candidate_application_events",
        type_="foreignkey",
    )
    op.drop_column("candidate_application_events", "effect_status")
    op.drop_column("candidate_application_events", "target_stage")
    op.drop_column("candidate_application_events", "agent_decision_id")
    op.drop_column("candidate_application_events", "role_id")
    op.drop_column("agent_decisions", "resolution_metadata")
