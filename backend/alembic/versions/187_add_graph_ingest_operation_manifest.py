"""Retain exact graph-ingest operation payload identity.

Revision ID: 187_graph_ingest_manifest
Revises: 186_graph_ingest_reconciliation
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "187_graph_ingest_manifest"
down_revision = "186_graph_ingest_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("graph_ingest_dispatches") as batch_op:
        batch_op.add_column(
            sa.Column(
                "operation_manifest",
                sa.JSON(none_as_null=True),
                nullable=True,
            ),
        )
        batch_op.add_column(
            sa.Column(
                "operation_manifest_sha256",
                sa.String(length=64),
                nullable=True,
            ),
        )
        batch_op.create_check_constraint(
            "ck_graph_ingest_dispatches_manifest_pair",
            "(operation_manifest IS NULL AND operation_manifest_sha256 IS NULL) "
            "OR (operation_manifest IS NOT NULL AND "
            "operation_manifest_sha256 IS NOT NULL)",
        )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION prevent_graph_ingest_manifest_mutation_v187()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF OLD.operation_manifest IS NOT NULL
                   OR OLD.operation_manifest_sha256 IS NOT NULL THEN
                    IF NEW.operation_manifest::jsonb IS DISTINCT FROM
                       OLD.operation_manifest::jsonb
                       OR NEW.operation_manifest_sha256 IS DISTINCT FROM
                          OLD.operation_manifest_sha256 THEN
                        RAISE EXCEPTION
                            'graph ingest operation manifest is immutable';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_graph_ingest_manifest_immutable
            BEFORE UPDATE OF operation_manifest, operation_manifest_sha256
            ON graph_ingest_dispatches
            FOR EACH ROW
            EXECUTE FUNCTION prevent_graph_ingest_manifest_mutation_v187()
            """
        )


def downgrade() -> None:
    raise RuntimeError(
        "Revision 187 is intentionally irreversible: graph-ingest operation "
        "manifests are retained evidence and must not be deleted."
    )
