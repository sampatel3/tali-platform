"""Add role collaboration versioning and append-only change audit.

Revision ID: 169_role_collaboration_controls
Revises: 168_bh_cred_generation
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "169_role_collaboration_controls"
down_revision = "168_bh_cred_generation"
branch_labels = None
depends_on = None

_AUDIT_ID_TYPE = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )

    # Hiring-team rows are access-control attachments, not independent
    # records. Hard deletion of their role/user/org must not be blocked by the
    # pre-existing non-cascading foreign keys.
    if op.get_bind().dialect.name == "postgresql":
        for column, target in (
            ("organization_id", "organizations.id"),
            ("role_id", "roles.id"),
            ("user_id", "users.id"),
        ):
            op.drop_constraint(
                f"job_hiring_team_{column}_fkey",
                "job_hiring_team",
                type_="foreignkey",
            )
            op.create_foreign_key(
                f"job_hiring_team_{column}_fkey",
                "job_hiring_team",
                target.split(".")[0],
                [column],
                [target.split(".")[1]],
                ondelete="CASCADE",
            )

    # Fail-closed rollout: seed every live legacy role with its workspace
    # owner(s) as hiring managers. Existing explicit memberships are retained.
    # New roles are assigned to their creator by the application write paths.
    op.execute(
        sa.text(
            """
            INSERT INTO job_hiring_team
                (organization_id, role_id, user_id, team_role)
            SELECT r.organization_id, r.id, u.id, 'hiring_manager'
            FROM roles AS r
            JOIN users AS u ON u.organization_id = r.organization_id
            WHERE r.deleted_at IS NULL
              AND u.role = 'owner'
              AND u.is_active IS TRUE
              AND NOT EXISTS (
                  SELECT 1
                  FROM job_hiring_team AS jht
                  WHERE jht.role_id = r.id AND jht.user_id = u.id
              )
            """
        )
    )

    op.create_table(
        "role_change_events",
        # SQLite auto-increments only an exact INTEGER PRIMARY KEY. Keep the
        # production BIGINT while matching the ORM's SQLite variant so
        # Alembic-created development databases can append audit rows too.
        sa.Column("id", _AUDIT_ID_TYPE, autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        # Deliberately retain the numeric role id even if a deletable empty role
        # is later removed; an audit record must not disappear with its target.
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("from_version", sa.Integer(), nullable=False),
        sa.Column("to_version", sa.Integer(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "from_version >= 0",
            name="ck_role_change_events_from_version_nonnegative",
        ),
        sa.CheckConstraint(
            "to_version > from_version",
            name="ck_role_change_events_version_advances",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_role_change_events_organization_id",
        "role_change_events",
        ["organization_id"],
    )
    op.create_index(
        "ix_role_change_events_role_id", "role_change_events", ["role_id"]
    )
    op.create_index(
        "ix_role_change_events_actor_user_id",
        "role_change_events",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_role_change_events_action", "role_change_events", ["action"]
    )
    op.create_index(
        "ix_role_change_events_request_id",
        "role_change_events",
        ["request_id"],
    )
    op.create_index(
        "ix_role_change_events_created_at",
        "role_change_events",
        ["created_at"],
    )
    op.create_index(
        "ix_role_change_events_org_role_created",
        "role_change_events",
        ["organization_id", "role_id", "created_at"],
    )

    # Production is PostgreSQL. Enforce append-only semantics below the ORM so
    # an accidental admin/service UPDATE or DELETE cannot rewrite history.
    # The sole allowed update is actor anonymization (the users FK's SET NULL)
    # so normal account deletion cannot make the audit table undeletable.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_role_change_event_mutation()
            RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'UPDATE'
                   AND OLD.actor_user_id IS NOT NULL
                   AND NEW.actor_user_id IS NULL
                   AND (
                       OLD.id,
                       OLD.organization_id,
                       OLD.role_id,
                       OLD.action,
                       OLD.from_version,
                       OLD.to_version,
                       OLD.reason,
                       OLD.request_id,
                       OLD.created_at
                   ) IS NOT DISTINCT FROM (
                       NEW.id,
                       NEW.organization_id,
                       NEW.role_id,
                       NEW.action,
                       NEW.from_version,
                       NEW.to_version,
                       NEW.reason,
                       NEW.request_id,
                       NEW.created_at
                   )
                   AND OLD.changes::jsonb IS NOT DISTINCT FROM NEW.changes::jsonb
                   THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION 'role_change_events is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            """
            CREATE TRIGGER role_change_events_append_only
            BEFORE UPDATE OR DELETE ON role_change_events
            FOR EACH ROW EXECUTE FUNCTION reject_role_change_event_mutation();
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS role_change_events_append_only "
            "ON role_change_events"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_role_change_event_mutation()")
    op.drop_table("role_change_events")
    if op.get_bind().dialect.name == "postgresql":
        for column, target in (
            ("organization_id", "organizations.id"),
            ("role_id", "roles.id"),
            ("user_id", "users.id"),
        ):
            op.drop_constraint(
                f"job_hiring_team_{column}_fkey",
                "job_hiring_team",
                type_="foreignkey",
            )
            op.create_foreign_key(
                f"job_hiring_team_{column}_fkey",
                "job_hiring_team",
                target.split(".")[0],
                [column],
                [target.split(".")[1]],
            )
    op.drop_column("roles", "version")
