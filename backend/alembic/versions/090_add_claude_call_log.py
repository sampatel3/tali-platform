"""Add ``claude_call_log`` — the source-of-truth table for every Anthropic call.

The original metering design wrote ``UsageEvent`` rows from
application-layer code (cv_score_orchestrator, pre_screening_service,
agent_runtime.orchestrator, etc.) at the *site* of each Claude call.
That was fragile by construction: a developer had to remember to put
``record_event`` in the right place, after the call, in every branch.
The 2026-05-21 reconciliation showed three bugs from this pattern
alone — duplicate records on the agent loop, retry tokens overwritten
on cv_match, error-path early-returns dropping tokens on pre-screen
— that combined to 73% of Anthropic's bill going unrecorded.

``claude_call_log`` is the structural fix. Every call through
``MeteredAnthropicClient`` writes one row here, *before* the response
is handed back to the caller. No early-return, no exception, no
``metering={"skip": True}`` can prevent it. The wrapper writes the
row; the application layer can no longer forget.

``UsageEvent`` stays as the enrichment layer — feature attribution,
role_id, entity_id, agent_run_id, etc. The reconciliation now compares
Anthropic's billing against ``claude_call_log`` (ground truth on
*what* was called) and surfaces any call_log row without a matching
UsageEvent as a "metering attribution gap".
"""
from alembic import op
import sqlalchemy as sa


revision = "090_add_claude_call_log"
down_revision = "089_add_pre_screen_error_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "claude_call_log",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        # Nullable — the shared-key client (admin tooling, archetype
        # synthesis, etc.) may have no org context at call time. The
        # reconciliation matches NULL workspace_id from Anthropic to
        # the union of NULL-org rows here.
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=True,
        ),
        # The full model id the SDK was called with. May be the dated
        # snapshot (claude-haiku-4-5-20251001) or the short alias
        # (claude-sonnet-4-5); the reconciliation handles both via
        # ``_model_match_filter``.
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd_micro", sa.BigInteger(), nullable=False, server_default="0"),
        # The ``feature`` value the caller hinted via the metering kwarg.
        # NULL when the caller passed nothing (= a bug — surfaces via
        # a low-cost reconciliation query). NOT mapped to the Feature
        # enum at write time because we want to record what the caller
        # actually claimed, even if it's wrong.
        sa.Column("feature_hint", sa.String(), nullable=True),
        # ``ok`` = call succeeded, tokens captured.
        # ``sdk_error`` = client.messages.create raised (network/4xx/5xx);
        #   tokens are zero, no $ charge.
        # ``no_usage_on_response`` = response succeeded but had no usage
        #   block (shouldn't happen on Anthropic but defensive).
        sa.Column("status", sa.String(), nullable=False, server_default="ok"),
        sa.Column("error_reason", sa.Text(), nullable=True),
        # Anthropic's request_id (from response headers) when available
        # — invaluable for cross-referencing the Anthropic admin Console
        # Logs page during incident response.
        sa.Column("anthropic_request_id", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # ``usage_event_id`` is the FK back to the enrichment row, if
        # the application layer also called record_event. NULL means
        # "we made the call but nobody attributed it" — the metering
        # gap the user keeps asking about, now queryable directly.
        sa.Column(
            "usage_event_id",
            sa.Integer(),
            sa.ForeignKey("usage_events.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_claude_call_log_org_created",
        "claude_call_log",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_claude_call_log_model_created",
        "claude_call_log",
        ["model", "created_at"],
    )
    op.create_index(
        "ix_claude_call_log_usage_event_id",
        "claude_call_log",
        ["usage_event_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_claude_call_log_usage_event_id", table_name="claude_call_log")
    op.drop_index("ix_claude_call_log_model_created", table_name="claude_call_log")
    op.drop_index("ix_claude_call_log_org_created", table_name="claude_call_log")
    op.drop_table("claude_call_log")
