"""Backfill provenance for criteria created with legacy requisitions.

Before requisition criteria had their own source literal, the materializer used
the generic ``recruiter`` default.  A request-time exact-match adoption cannot
identify a brief that was edited before its first post-upgrade publish.  Rows
created in the same transaction/window as their requisition Role are the safe
historical ownership signal, so mark those once during deployment.

Revision ID: 160_backfill_requisition_criteria
Revises: 159_add_agent_bootstrap_state
Create Date: 2026-07-14
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "160_backfill_requisition_criteria"
down_revision = "159_add_agent_bootstrap_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Adopt only an exact, uncustomized legacy brief-shaped criterion set.

    Creation-time proximity is not ownership: a recruiter can add criteria in
    the same minute a role is published. Comparing the complete ordered shape
    mirrors the request-time adoption guard in ``role_brief_service`` and
    deliberately leaves every ambiguous row recruiter-owned.
    """
    bind = op.get_bind()

    def _items(value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                value = []
        return value if isinstance(value, list) else []

    def _text(item) -> str:
        if isinstance(item, str):
            return item.strip()
        if isinstance(item, dict):
            return str(item.get("text") or item.get("label") or "").strip()
        return str(item).strip()

    briefs = bind.execute(
        sa.text(
            """
            SELECT role.id AS role_id,
                   brief.must_haves,
                   brief.preferred,
                   brief.dealbreakers
              FROM roles AS role
              JOIN role_briefs AS brief ON brief.role_id = role.id
             WHERE role.source = 'requisition'
               AND NOT EXISTS (
                    SELECT 1
                      FROM role_criteria AS owned
                     WHERE owned.role_id = role.id
                       AND owned.source = 'requisition'
               )
            """
        )
    ).mappings()
    for brief in briefs:
        desired: list[tuple[str, str, bool]] = []
        for value, bucket, must_have in (
            (brief["must_haves"], "must", True),
            (brief["preferred"], "preferred", False),
            (brief["dealbreakers"], "constraint", False),
        ):
            desired.extend(
                (text, bucket, must_have)
                for text in (_text(item) for item in _items(value))
                if text
            )
        if not desired:
            continue

        rows = list(
            bind.execute(
                sa.text(
                    """
                    SELECT id, text, bucket, must_have
                      FROM role_criteria
                     WHERE role_id = :role_id
                       AND source = 'recruiter'
                       AND deleted_at IS NULL
                       AND org_criterion_id IS NULL
                       AND customized_at IS NULL
                     ORDER BY ordering, id
                    """
                ),
                {"role_id": int(brief["role_id"])},
            ).mappings()
        )
        shape = [
            (str(row["text"] or "").strip(), str(row["bucket"]), bool(row["must_have"]))
            for row in rows
        ]
        if shape != desired:
            continue
        for row in rows:
            bind.execute(
                sa.text(
                    "UPDATE role_criteria SET source = 'requisition' WHERE id = :id"
                ),
                {"id": int(row["id"])},
            )


def downgrade() -> None:
    # Provenance correction is intentionally retained: after upgrade, new and
    # reconciled requisition rows are indistinguishable from the backfilled
    # rows, so reverting the literal would destroy ownership information.
    pass
