"""Add workspace-wide agent pause overlay and actor provenance.

Revision ID: 172_workspace_agent_control
Revises: 171_agent_chat_event_source_key
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "172_workspace_agent_control"
down_revision = "171_agent_chat_event_source_key"
branch_labels = None
depends_on = None

_EVENT_ID_TYPE = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("agent_workspace_paused_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("agent_workspace_paused_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("agent_workspace_paused_by_user_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("agent_workspace_paused_by_name", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "agent_workspace_control_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.create_foreign_key(
        "fk_organizations_agent_workspace_paused_by_user_id_users",
        "organizations",
        "users",
        ["agent_workspace_paused_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_organizations_agent_workspace_paused_by_user_id",
        "organizations",
        ["agent_workspace_paused_by_user_id"],
    )
    op.create_table(
        "workspace_agent_control_events",
        sa.Column("id", _EVENT_ID_TYPE, autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_name", sa.String(length=200), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("from_version", sa.Integer(), nullable=False),
        sa.Column("to_version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "action IN ('paused', 'resumed')",
            name="ck_workspace_agent_control_events_action",
        ),
        sa.CheckConstraint(
            "from_version >= 1 AND to_version > from_version",
            name="ck_workspace_agent_control_events_version_advances",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_workspace_agent_control_events_organization_id", ["organization_id"]),
        ("ix_workspace_agent_control_events_actor_user_id", ["actor_user_id"]),
        ("ix_workspace_agent_control_events_action", ["action"]),
        ("ix_workspace_agent_control_events_request_id", ["request_id"]),
        ("ix_workspace_agent_control_events_created_at", ["created_at"]),
        (
            "ix_workspace_agent_control_events_org_created",
            ["organization_id", "created_at"],
        ),
    ):
        op.create_index(name, "workspace_agent_control_events", columns)

    if op.get_bind().dialect.name != "postgresql":
        return

    # Resolve one final piece of evidence per organization before mutating any
    # role. Preferred evidence is a same-request append-only bulk command. A
    # strict fallback covers the short production window where pause-all wrote
    # no audit rows: >=2 latest manual pauses in one second, zero running
    # enabled roles, and exactly one active owner who predates the cluster.
    # A later audited resume defeats an older fallback. Conversely, the newer
    # 14:34 fallback observed in production correctly supersedes its older
    # 13:39 audited pause group.
    op.execute(
        sa.text(
            """
            CREATE TEMP TABLE workspace_agent_control_migration_evidence
            ON COMMIT DROP AS
            WITH audit_commands AS (
                SELECT
                    organization_id,
                    request_id,
                    action,
                    MAX(created_at) AS changed_at,
                    MAX(actor_user_id) AS actor_user_id,
                    COUNT(*) AS event_count
                FROM role_change_events
                WHERE request_id IS NOT NULL
                  AND (
                    (action = 'agent_paused' AND reason = 'paused by recruiter')
                    OR
                    (action = 'agent_resumed' AND reason = 'bulk resume requested by recruiter')
                  )
                GROUP BY organization_id, request_id, action
                HAVING COUNT(*) >= 2
            ), latest_audit AS (
                SELECT DISTINCT ON (organization_id)
                    organization_id,
                    request_id,
                    action,
                    changed_at,
                    actor_user_id
                FROM audit_commands
                ORDER BY organization_id, changed_at DESC, request_id DESC, action DESC
            ), latest_bulk_resume AS (
                SELECT organization_id, MAX(created_at) AS changed_at
                FROM role_change_events
                WHERE action = 'agent_resumed'
                  AND reason = 'bulk resume requested by recruiter'
                GROUP BY organization_id
            ), latest_manual AS (
                SELECT organization_id, MAX(agent_paused_at) AS changed_at
                FROM roles
                WHERE deleted_at IS NULL
                  AND agentic_mode_enabled IS TRUE
                  AND agent_paused_reason = 'paused by recruiter'
                  AND agent_paused_at IS NOT NULL
                GROUP BY organization_id
            ), strict_cluster AS (
                SELECT
                    latest_manual.organization_id,
                    latest_manual.changed_at,
                    COUNT(role.id) AS clustered_roles
                FROM latest_manual
                JOIN roles AS role
                  ON role.organization_id = latest_manual.organization_id
                 AND role.deleted_at IS NULL
                 AND role.agentic_mode_enabled IS TRUE
                 AND role.agent_paused_reason = 'paused by recruiter'
                 AND role.agent_paused_at >= latest_manual.changed_at - INTERVAL '1 second'
                 AND role.agent_paused_at <= latest_manual.changed_at
                GROUP BY latest_manual.organization_id, latest_manual.changed_at
                HAVING COUNT(role.id) >= 2
            ), strict_evidence AS (
                SELECT
                    strict_cluster.organization_id,
                    strict_cluster.changed_at,
                    MIN(owner.id) AS actor_user_id,
                    MIN(COALESCE(owner.full_name, owner.email)) AS actor_name
                FROM strict_cluster
                JOIN users AS owner
                  ON owner.organization_id = strict_cluster.organization_id
                 AND owner.role = 'owner'
                 AND owner.is_active IS TRUE
                 AND owner.created_at <= strict_cluster.changed_at
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM roles AS running
                    WHERE running.organization_id = strict_cluster.organization_id
                      AND running.deleted_at IS NULL
                      AND running.agentic_mode_enabled IS TRUE
                      AND running.agent_paused_at IS NULL
                )
                GROUP BY strict_cluster.organization_id, strict_cluster.changed_at
                HAVING COUNT(owner.id) = 1
            ), pause_candidates AS (
                SELECT
                    latest_audit.organization_id,
                    'audit'::text AS evidence_source,
                    latest_audit.request_id,
                    latest_audit.changed_at,
                    latest_audit.actor_user_id,
                    LEFT(COALESCE(actor.full_name, actor.email), 200) AS actor_name
                FROM latest_audit
                LEFT JOIN users AS actor
                  ON actor.id = latest_audit.actor_user_id
                 AND actor.organization_id = latest_audit.organization_id
                WHERE latest_audit.action = 'agent_paused'
                  AND EXISTS (
                    SELECT 1
                    FROM roles AS enabled
                    WHERE enabled.organization_id = latest_audit.organization_id
                      AND enabled.deleted_at IS NULL
                      AND enabled.agentic_mode_enabled IS TRUE
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM roles AS running
                    WHERE running.organization_id = latest_audit.organization_id
                      AND running.deleted_at IS NULL
                      AND running.agentic_mode_enabled IS TRUE
                      AND running.agent_paused_at IS NULL
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM latest_bulk_resume
                    WHERE latest_bulk_resume.organization_id = latest_audit.organization_id
                      AND latest_bulk_resume.changed_at >= latest_audit.changed_at
                  )
                UNION ALL
                SELECT
                    strict_evidence.organization_id,
                    'strict_cluster'::text AS evidence_source,
                    NULL::text AS request_id,
                    strict_evidence.changed_at,
                    strict_evidence.actor_user_id,
                    LEFT(strict_evidence.actor_name, 200) AS actor_name
                FROM strict_evidence
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM latest_bulk_resume
                    WHERE latest_bulk_resume.organization_id = strict_evidence.organization_id
                      AND latest_bulk_resume.changed_at >= strict_evidence.changed_at
                )
            ), ranked AS (
                SELECT
                    pause_candidates.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY organization_id
                        ORDER BY changed_at DESC,
                                 CASE evidence_source WHEN 'audit' THEN 0 ELSE 1 END
                    ) AS evidence_rank
                FROM pause_candidates
            )
            SELECT
                organization_id,
                evidence_source,
                request_id,
                changed_at,
                actor_user_id,
                actor_name
            FROM ranked
            WHERE evidence_rank = 1
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE organizations AS organization
            SET agent_workspace_paused_at = evidence.changed_at,
                agent_workspace_paused_reason = 'workspace paused by recruiter',
                agent_workspace_paused_by_user_id = evidence.actor_user_id,
                agent_workspace_paused_by_name = evidence.actor_name,
                agent_workspace_control_version = 2
            FROM workspace_agent_control_migration_evidence AS evidence
            WHERE organization.id = evidence.organization_id
            """
        )
    )

    # Clear only roles belonging to the exact evidence that established the
    # final overlay. Audit recovery uses its request id; fallback recovery uses
    # only its one-second cluster. Older independent role holds are untouched.
    op.execute(
        sa.text(
            """
            UPDATE roles AS role
            SET agent_paused_at = NULL,
                agent_paused_reason = NULL
            FROM workspace_agent_control_migration_evidence AS evidence
            WHERE role.organization_id = evidence.organization_id
              AND role.deleted_at IS NULL
              AND role.agentic_mode_enabled IS TRUE
              AND role.agent_paused_reason = 'paused by recruiter'
              AND (
                (
                    evidence.evidence_source = 'strict_cluster'
                    AND role.agent_paused_at >= evidence.changed_at - INTERVAL '1 second'
                    AND role.agent_paused_at <= evidence.changed_at
                )
                OR
                (
                    evidence.evidence_source = 'audit'
                    AND EXISTS (
                        SELECT 1
                        FROM role_change_events AS pause_event
                        WHERE pause_event.organization_id = evidence.organization_id
                          AND pause_event.role_id = role.id
                          AND pause_event.request_id = evidence.request_id
                          AND pause_event.action = 'agent_paused'
                          AND NOT EXISTS (
                            SELECT 1
                            FROM role_change_events AS later_resume
                            WHERE later_resume.organization_id = pause_event.organization_id
                              AND later_resume.role_id = pause_event.role_id
                              AND later_resume.action = 'agent_resumed'
                              AND later_resume.id > pause_event.id
                          )
                    )
                )
              )
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO workspace_agent_control_events
                (organization_id, actor_user_id, actor_name, action,
                 from_version, to_version, reason, request_id, created_at)
            SELECT
                organization_id,
                actor_user_id,
                actor_name,
                'paused',
                1,
                2,
                'workspace pause migrated from prior bulk control',
                COALESCE(request_id, 'migration:172_workspace_agent_control'),
                changed_at
            FROM workspace_agent_control_migration_evidence
            """
        )
    )
    op.execute("DROP TABLE workspace_agent_control_migration_evidence")

    # Enforce append-only history below the ORM. The sole permitted mutation is
    # FK anonymization (actor id -> NULL); the actor-name snapshot survives.
    op.execute(
        """
        CREATE FUNCTION reject_workspace_agent_control_event_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND OLD.actor_user_id IS NOT NULL
               AND NEW.actor_user_id IS NULL
               AND (
                   OLD.id, OLD.organization_id, OLD.actor_name, OLD.action,
                   OLD.from_version, OLD.to_version, OLD.reason,
                   OLD.request_id, OLD.created_at
               ) IS NOT DISTINCT FROM (
                   NEW.id, NEW.organization_id, NEW.actor_name, NEW.action,
                   NEW.from_version, NEW.to_version, NEW.reason,
                   NEW.request_id, NEW.created_at
               ) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'workspace_agent_control_events is append-only';
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER workspace_agent_control_events_append_only
        BEFORE UPDATE OR DELETE ON workspace_agent_control_events
        FOR EACH ROW EXECUTE FUNCTION reject_workspace_agent_control_event_mutation();
        """
    )


def downgrade() -> None:
    # A downgrade must not silently start agents. Materialize the workspace
    # hold onto currently running local role state before removing the overlay.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                UPDATE roles
                SET agent_paused_at = organization.agent_workspace_paused_at,
                    agent_paused_reason = 'paused by recruiter'
                FROM organizations AS organization
                WHERE roles.organization_id = organization.id
                  AND organization.agent_workspace_paused_at IS NOT NULL
                  AND roles.deleted_at IS NULL
                  AND roles.agentic_mode_enabled IS TRUE
                  AND roles.agent_paused_at IS NULL
                """
            )
        )
        op.execute(
            "DROP TRIGGER IF EXISTS workspace_agent_control_events_append_only "
            "ON workspace_agent_control_events"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS reject_workspace_agent_control_event_mutation()"
        )
    else:
        op.execute(
            sa.text(
                """
                UPDATE roles
                SET agent_paused_at = (
                        SELECT organization.agent_workspace_paused_at
                        FROM organizations AS organization
                        WHERE organization.id = roles.organization_id
                    ),
                    agent_paused_reason = 'paused by recruiter'
                WHERE roles.deleted_at IS NULL
                  AND roles.agentic_mode_enabled IS TRUE
                  AND roles.agent_paused_at IS NULL
                  AND EXISTS (
                    SELECT 1
                    FROM organizations AS organization
                    WHERE organization.id = roles.organization_id
                      AND organization.agent_workspace_paused_at IS NOT NULL
                  )
                """
            )
        )
    op.drop_table("workspace_agent_control_events")
    op.drop_index(
        "ix_organizations_agent_workspace_paused_by_user_id",
        table_name="organizations",
    )
    op.drop_constraint(
        "fk_organizations_agent_workspace_paused_by_user_id_users",
        "organizations",
        type_="foreignkey",
    )
    op.drop_column("organizations", "agent_workspace_control_version")
    op.drop_column("organizations", "agent_workspace_paused_by_name")
    op.drop_column("organizations", "agent_workspace_paused_by_user_id")
    op.drop_column("organizations", "agent_workspace_paused_reason")
    op.drop_column("organizations", "agent_workspace_paused_at")
