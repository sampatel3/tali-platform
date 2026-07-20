"""Bind candidate proof keys and persist one-use request nonces.

Revision ID: 181_bind_candidate_request_proof
Revises: 180_harden_runtime_state
"""

from alembic import op
import sqlalchemy as sa


revision = "181_bind_candidate_request_proof"
down_revision = "180_harden_runtime_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("assessments") as batch:
        batch.add_column(sa.Column("candidate_proof_key_id", sa.String(length=43), nullable=True))
        batch.add_column(sa.Column("candidate_proof_public_jwk", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column("candidate_proof_key_bound_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_check_constraint(
            "ck_assessments_candidate_proof_key_complete",
            "((candidate_proof_key_id IS NULL AND candidate_proof_public_jwk IS NULL AND candidate_proof_key_bound_at IS NULL) OR "
            "(candidate_proof_key_id IS NOT NULL AND candidate_proof_public_jwk IS NOT NULL AND candidate_proof_key_bound_at IS NOT NULL))",
        )

    op.create_table(
        "candidate_assessment_proof_nonces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("nonce", sa.String(length=128), nullable=False),
        sa.Column("key_id", sa.String(length=43), nullable=False),
        sa.Column("proof_timestamp", sa.BigInteger(), nullable=False),
        sa.Column(
            "consumed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["assessments.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "assessment_id",
            "nonce",
            name="uq_candidate_assessment_proof_nonce",
        ),
    )
    op.create_index(
        "ix_candidate_assessment_proof_nonces_assessment_id",
        "candidate_assessment_proof_nonces",
        ["assessment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_assessment_proof_nonces_assessment_id",
        table_name="candidate_assessment_proof_nonces",
    )
    op.drop_table("candidate_assessment_proof_nonces")
    with op.batch_alter_table("assessments") as batch:
        batch.drop_constraint("ck_assessments_candidate_proof_key_complete", type_="check")
        batch.drop_column("candidate_proof_key_bound_at")
        batch.drop_column("candidate_proof_public_jwk")
        batch.drop_column("candidate_proof_key_id")
