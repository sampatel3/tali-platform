"""Add role-level cache for AI-generated tech screening questions.

Replaces the per-candidate ``tech_interview_pack`` regeneration that
was firing the ``interview_tech`` Anthropic call on every CV scoring
event. ~302 calls/day → ~1-5 calls/day (one per role when its job spec
or criteria change). Drops candidate-specific personalisation; every
candidate on the same role gets the same screening questions, which
matches how recruiters actually run screening interviews.

Columns:
- ``tech_questions_cached`` — JSON, the generated question payload
- ``tech_questions_cached_at`` — when it was last regenerated
- ``tech_questions_signature`` — sha256 of (job_spec_text + criteria
  ids+text+priority). Compared against the live computed signature to
  decide when to regenerate. Set on every successful regeneration so
  the system is self-healing: a role that's never been touched gets
  its first cache populated lazily on the first scoring run after this
  ships.
"""
from alembic import op
import sqlalchemy as sa


revision = "091_add_role_tech_questions_cache"
down_revision = "090_add_claude_call_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column("tech_questions_cached", sa.JSON(), nullable=True),
    )
    op.add_column(
        "roles",
        sa.Column(
            "tech_questions_cached_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "roles",
        sa.Column("tech_questions_signature", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("roles", "tech_questions_signature")
    op.drop_column("roles", "tech_questions_cached_at")
    op.drop_column("roles", "tech_questions_cached")
