"""Decision feedback ("Send back & teach") + rubric revisions audit table.

Adds the data backbone for the Hub's teach loop:

- ``decision_feedback`` — one row per "send back & teach" submission.
  Captures the failure mode, free-form correction, scope, and (when
  scope='org') a co-sign requirement. Becomes the input to the nightly
  retune job.
- ``rubric_revisions`` — immutable history of how the rubric has been
  re-tuned over time. Each row links back to the ``decision_feedback``
  rows that informed it. Lets the Hub's SIGNAL section show
  "feedback → revision → next decisions used new weights".
- ``agent_decisions`` columns: ``feedback_id``, ``human_disposition``,
  ``snoozed_until`` so the queue can hide snoozed rows and the Hub can
  compute teach-rate / override-rate without joining feedback every
  time.

The ``status`` column on ``agent_decisions`` is a free-form string, so
the new ``reverted_for_feedback`` value is encoded only at the
application layer (``AGENT_DECISION_STATUSES``) — no DDL needed there.

Revision ID: 063_add_decision_feedback
Revises: 062_add_role_agent_next_run_at
Create Date: 2026-05-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "063_add_decision_feedback"
down_revision = "062_add_role_agent_next_run_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. rubric_revisions — created first because decision_feedback FKs into it.
    op.create_table(
        "rubric_revisions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id"),
            nullable=True,  # null = org-wide
        ),
        sa.Column(
            "parent_revision_id",
            sa.BigInteger(),
            sa.ForeignKey("rubric_revisions.id"),
            nullable=True,
        ),
        sa.Column("cause", sa.String(length=32), nullable=False),
        # JSON list of decision_feedback ids — see RubricRevision model docstring.
        # Portable across PG + SQLite; cardinality is always small.
        sa.Column(
            "feedback_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("weights_diff", sa.JSON(), nullable=True),
        sa.Column("threshold_diff", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_rubric_revisions_org_created",
        "rubric_revisions",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_rubric_revisions_role_id",
        "rubric_revisions",
        ["role_id"],
    )

    # 2. decision_feedback
    op.create_table(
        "decision_feedback",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "decision_id",
            sa.BigInteger(),
            sa.ForeignKey("agent_decisions.id"),
            nullable=False,
        ),
        sa.Column(
            "reviewer_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id"),
            nullable=True,
        ),
        sa.Column("failure_mode", sa.String(length=32), nullable=False),
        sa.Column("correction_text", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column(
            "cosign_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "cosigned_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("cosigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "applied_revision_id",
            sa.BigInteger(),
            sa.ForeignKey("rubric_revisions.id"),
            nullable=True,
        ),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_decision_feedback_org_created",
        "decision_feedback",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_decision_feedback_role_id",
        "decision_feedback",
        ["role_id"],
    )
    op.create_index(
        "ix_decision_feedback_decision_id",
        "decision_feedback",
        ["decision_id"],
    )

    # 3. Extend agent_decisions. The FK to decision_feedback.id forms a
    # cycle with decision_feedback.decision_id, so add it as a separate
    # constraint with use_alter semantics rather than declaring it inline.
    op.add_column(
        "agent_decisions",
        sa.Column("feedback_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_decisions_feedback_id",
        source_table="agent_decisions",
        referent_table="decision_feedback",
        local_cols=["feedback_id"],
        remote_cols=["id"],
    )
    op.add_column(
        "agent_decisions",
        sa.Column("human_disposition", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "agent_decisions",
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_decisions", "snoozed_until")
    op.drop_column("agent_decisions", "human_disposition")
    op.drop_constraint(
        "fk_agent_decisions_feedback_id",
        "agent_decisions",
        type_="foreignkey",
    )
    op.drop_column("agent_decisions", "feedback_id")

    op.drop_index("ix_decision_feedback_decision_id", table_name="decision_feedback")
    op.drop_index("ix_decision_feedback_role_id", table_name="decision_feedback")
    op.drop_index("ix_decision_feedback_org_created", table_name="decision_feedback")
    op.drop_table("decision_feedback")

    op.drop_index("ix_rubric_revisions_role_id", table_name="rubric_revisions")
    op.drop_index("ix_rubric_revisions_org_created", table_name="rubric_revisions")
    op.drop_table("rubric_revisions")
