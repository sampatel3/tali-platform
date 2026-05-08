"""Cohort-planner additions:

  1. ``agent_needs_input`` table — open questions the orchestrator
     surfaces to the recruiter when sub-agents detect the role spec is
     incomplete (empty must_have slot, no monthly budget cap, ambiguous
     thresholds, etc.). Each row is one specific ask; recruiters
     answer inline on the role page or in the Hub and the next cycle
     unblocks.

  2. ``roles.agent_send_assessment_requires_approval`` — per-role HITL
     toggle for the send-assessment step. The previous behaviour
     auto-executed sends; some recruiters want human approval for
     every invite. Defaults to ``True`` in the cohort-planner era so
     turning agent mode on never silently spends budget on assessment
     invites the recruiter didn't explicitly approve.

Revision ID: 067_add_agent_needs_input_and_send_assessment_hitl
Revises: 066_add_decision_policies
Create Date: 2026-05-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "067_add_agent_needs_input_and_send_assessment_hitl"
down_revision = "066_add_decision_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_needs_input",
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
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=64), nullable=False),
        # Free-form recruiter-facing question (one short paragraph).
        sa.Column("prompt", sa.Text(), nullable=False),
        # Optional structured response shape — ``options`` is a list
        # of {value, label} the recruiter clicks; ``schema`` describes
        # a free-text/numeric input. Mutually exclusive in practice.
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column("schema", sa.JSON(), nullable=True),
        # Where the agent's reasoning came from (per-cycle context;
        # the next cycle re-derives this if the question is still open).
        sa.Column(
            "agent_run_id",
            sa.BigInteger(),
            sa.ForeignKey("agent_runs.id"),
            nullable=True,
        ),
        sa.Column("rationale", sa.Text(), nullable=True),
        # Resolution. ``response`` is whatever shape ``options``/``schema``
        # described. ``resolved_by_user_id`` records the recruiter who
        # answered. ``dismissed_at`` lets the agent give up if it asked
        # too long ago and the question has gone stale.
        sa.Column(
            "resolved_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # "Find open asks for this role" is the hot lookup.
    op.create_index(
        "ix_agent_needs_input_role_open",
        "agent_needs_input",
        ["role_id", "resolved_at", "dismissed_at"],
    )
    op.create_index(
        "ix_agent_needs_input_org_open",
        "agent_needs_input",
        ["organization_id", "resolved_at", "dismissed_at"],
    )

    # Per-role HITL toggle for send_assessment. Default True is the
    # safer cohort-era stance; the existing per-application event path
    # auto-sent silently, which is no longer the right default once
    # the agent is operating role-wide.
    op.add_column(
        "roles",
        sa.Column(
            "agent_send_assessment_requires_approval",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("roles", "agent_send_assessment_requires_approval")
    op.drop_index(
        "ix_agent_needs_input_org_open",
        table_name="agent_needs_input",
    )
    op.drop_index(
        "ix_agent_needs_input_role_open",
        table_name="agent_needs_input",
    )
    op.drop_table("agent_needs_input")
