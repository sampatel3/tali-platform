"""Enforce one active decision per logical role/candidate subject.

Revision ID: 188_enforce_active_decision_slot
Revises: 187_candidate_capability_indexes
Create Date: 2026-07-22

Physical source and ATS applications are evidence/transport only. The durable
subject is ``(organization_id, role_id, candidate_id)``: one candidate can have
independent cards in different roles, but owner and direct application rows can
never create two cards in the same role. Decision type deliberately does not
participate in the slot.

The compatibility trigger is installed and committed before the data repair.
It serializes every active-slot write while the populated table is repaired and
the unique index is built, including writes from older workers that omit
``candidate_id``. A retry repairs any pre-trigger duplicate and replaces only an
invalid or contract-incompatible prior build. Once valid, the trigger and index
jointly protect every writer, including older workers and direct SQL producers.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "188_enforce_active_decision_slot"
down_revision = "187_candidate_capability_indexes"
branch_labels = None
depends_on = None


_INDEX = "uq_agent_decisions_active_org_role_candidate"
_LEGACY_INDEX = "uq_agent_decisions_active_role_application"
_CANDIDATE_INDEX = "ix_agent_decisions_candidate_id"
_CANDIDATE_TRIGGER = "trg_agent_decisions_resolve_candidate"
_CANDIDATE_FUNCTION = "resolve_agent_decision_candidate_id"
_CANDIDATE_PRESENT_CHECK = "ck_agent_decisions_candidate_id_present"
_ACTIVE_PREDICATE = "status IN ('pending', 'processing', 'reverted_for_feedback')"


def _index_state() -> dict[str, object] | None:
    """Describe a same-named index so interrupted/older builds are detectable."""

    row = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT index_state.indisvalid,
                   index_state.indisunique,
                   pg_get_indexdef(index_state.indexrelid, 1, TRUE) AS key_1,
                   pg_get_indexdef(index_state.indexrelid, 2, TRUE) AS key_2,
                   pg_get_indexdef(index_state.indexrelid, 3, TRUE) AS key_3,
                   pg_get_expr(
                       index_state.indpred,
                       index_state.indrelid
                   ) AS predicate
            FROM pg_class AS table_class
            JOIN pg_namespace AS namespace
              ON namespace.oid = table_class.relnamespace
            JOIN pg_index AS index_state
              ON index_state.indrelid = table_class.oid
            JOIN pg_class AS index_class
              ON index_class.oid = index_state.indexrelid
            WHERE namespace.nspname = current_schema()
              AND table_class.relname = 'agent_decisions'
              AND index_class.relname = :index_name
            """
            ),
            {"index_name": _INDEX},
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


def _index_matches_contract(state: dict[str, object]) -> bool:
    predicate = str(state.get("predicate") or "")
    return bool(
        state.get("indisvalid")
        and state.get("indisunique")
        and str(state.get("key_1") or "").strip() == "organization_id"
        and str(state.get("key_2") or "").strip() == "role_id"
        and str(state.get("key_3") or "").strip() == "candidate_id"
        and all(
            status in predicate
            for status in (
                "pending",
                "processing",
                "reverted_for_feedback",
            )
        )
    )


def _run_concurrently(sql: str) -> None:
    with op.get_context().autocommit_block():
        op.execute(sql)


def _create_non_unique_index_concurrently(name: str, sql: str) -> None:
    invalid = op.get_bind().execute(
        sa.text(
            """
            SELECT NOT (index_state.indisvalid AND index_state.indisready)
            FROM pg_index AS index_state
            WHERE index_state.indexrelid = to_regclass(:index_name)
            """
        ),
        {"index_name": name},
    ).scalar_one_or_none()
    if invalid:
        _run_concurrently(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    _run_concurrently(sql)


def _create_candidate_identity_trigger() -> None:
    """Populate identity and protect the active slot for every SQL writer."""

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_CANDIDATE_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            application_candidate_id INTEGER;
            application_organization_id INTEGER;
            candidate_organization_id INTEGER;
            role_organization_id INTEGER;
            current_decision_id BIGINT;
            conflicting_decision_id BIGINT;
        BEGIN
            SELECT application.candidate_id,
                   application.organization_id,
                   candidate.organization_id
            INTO application_candidate_id,
                 application_organization_id,
                 candidate_organization_id
            FROM candidate_applications AS application
            JOIN candidates AS candidate
              ON candidate.id = application.candidate_id
            WHERE application.id = NEW.application_id;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'application % does not exist', NEW.application_id
                    USING ERRCODE = '23503';
            END IF;
            IF application_organization_id <> NEW.organization_id
               OR candidate_organization_id <> NEW.organization_id THEN
                RAISE EXCEPTION
                    'decision organization % does not own application % and candidate %',
                    NEW.organization_id,
                    NEW.application_id,
                    application_candidate_id
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.candidate_id IS NULL THEN
                NEW.candidate_id := application_candidate_id;
            ELSIF NEW.candidate_id <> application_candidate_id THEN
                RAISE EXCEPTION
                    'candidate % does not own decision application %',
                    NEW.candidate_id,
                    NEW.application_id
                    USING ERRCODE = '23514';
            END IF;

            SELECT role.organization_id
            INTO role_organization_id
            FROM roles AS role
            WHERE role.id = NEW.role_id;
            IF NOT FOUND OR role_organization_id <> NEW.organization_id THEN
                RAISE EXCEPTION
                    'decision organization % does not own role %',
                    NEW.organization_id,
                    NEW.role_id
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.status IN (
                'pending', 'processing', 'reverted_for_feedback'
            ) THEN
                -- This lock closes the gap before the partial unique index is
                -- available. It also makes two old writers, both of which omit
                -- candidate_id, observe one another in commit order.
                PERFORM pg_advisory_xact_lock(
                    NEW.role_id,
                    application_candidate_id
                );
                IF TG_OP = 'UPDATE' THEN
                    current_decision_id := OLD.id;
                END IF;
                SELECT existing.id
                INTO conflicting_decision_id
                FROM agent_decisions AS existing
                LEFT JOIN candidate_applications AS source_application
                  ON source_application.id = existing.application_id
                WHERE existing.organization_id = NEW.organization_id
                  AND existing.role_id = NEW.role_id
                  AND COALESCE(
                        existing.candidate_id,
                        source_application.candidate_id
                      ) = application_candidate_id
                  AND existing.status IN (
                        'pending',
                        'processing',
                        'reverted_for_feedback'
                      )
                  AND (
                        current_decision_id IS NULL
                        OR existing.id <> current_decision_id
                      )
                LIMIT 1;
                IF conflicting_decision_id IS NOT NULL THEN
                    RAISE EXCEPTION
                        'active decision % already occupies role % candidate %',
                        conflicting_decision_id,
                        NEW.role_id,
                        application_candidate_id
                        USING
                            ERRCODE = '23505',
                            CONSTRAINT =
                                'uq_agent_decisions_active_org_role_candidate';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"DROP TRIGGER IF EXISTS {_CANDIDATE_TRIGGER} ON agent_decisions"
    )
    op.execute(
        f"""
        CREATE TRIGGER {_CANDIDATE_TRIGGER}
        BEFORE INSERT OR UPDATE OF
            organization_id, role_id, application_id, candidate_id, status
        ON agent_decisions
        FOR EACH ROW
        EXECUTE FUNCTION {_CANDIDATE_FUNCTION}()
        """
    )


def _commit_pre_repair_guard() -> None:
    """Make the rolling-writer guard visible before any populated-data work."""

    # Alembic's autocommit block commits the transaction on entry. The harmless
    # statement makes the boundary explicit and starts no long-lived work.
    with op.get_context().autocommit_block():
        op.execute("SELECT 1")


def _repair_active_decision_slots() -> None:
    """Discard duplicate active rows using source identity before backfill."""

    # Preserve every audit row. Processing wins because its candidate-facing
    # action is already in flight only within the current membership lifecycle;
    # a direct/current source beats an obsolete owner transport first. A taught
    # card then wins over an ordinary pending card because it carries explicit
    # human feedback. All losing rows remain as discarded audit records.
    op.execute(
        """
        WITH ranked AS (
            SELECT decision.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY decision.organization_id,
                                    decision.role_id,
                                    application.candidate_id
                       ORDER BY CASE
                                    WHEN application.role_id = decision.role_id
                                      OR membership.source_application_id =
                                            decision.application_id
                                    THEN 0
                                    ELSE 1
                                END,
                                CASE decision.status
                                    WHEN 'processing' THEN 0
                                    WHEN 'reverted_for_feedback' THEN 1
                                    ELSE 2
                                END,
                                decision.created_at DESC,
                                decision.id DESC
                   ) AS active_rank
            FROM agent_decisions AS decision
            JOIN candidate_applications AS application
              ON application.id = decision.application_id
            LEFT JOIN sister_role_evaluations AS membership
              ON membership.organization_id = decision.organization_id
             AND membership.role_id = decision.role_id
             AND membership.candidate_id = application.candidate_id
             AND membership.deleted_at IS NULL
            WHERE decision.status IN (
                'pending', 'processing', 'reverted_for_feedback'
            )
        )
        UPDATE agent_decisions AS decision
        SET status = 'discarded',
            resolved_at = COALESCE(decision.resolved_at, CURRENT_TIMESTAMP),
            resolution_note = COALESCE(
                NULLIF(BTRIM(decision.resolution_note), ''),
                'Superseded while enforcing one active decision per role candidate'
            )
        FROM ranked
        WHERE decision.id = ranked.id
          AND ranked.active_rank > 1
        """
    )


def upgrade() -> None:
    # This migration crosses an autocommit boundary for concurrent indexes. All
    # pre-index DDL is therefore deliberately retry-safe if Alembic did not stamp
    # the revision after an interrupted build.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE agent_decisions "
        "ADD COLUMN IF NOT EXISTS candidate_id INTEGER"
    )
    # ALTER TABLE prevents concurrent old statements while this transaction is
    # open. Install the compatibility guard immediately, then commit both DDL
    # changes before reading or repairing populated rows.
    _create_candidate_identity_trigger()
    _commit_pre_repair_guard()
    op.execute("SET LOCAL lock_timeout = '5s'")
    _repair_active_decision_slots()
    op.execute(
        """
        UPDATE agent_decisions AS decision
        SET candidate_id = application.candidate_id
        FROM candidate_applications AS application
        WHERE application.id = decision.application_id
          AND decision.candidate_id IS DISTINCT FROM application.candidate_id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM agent_decisions AS decision
                LEFT JOIN candidate_applications AS application
                  ON application.id = decision.application_id
                LEFT JOIN candidates AS candidate
                  ON candidate.id = application.candidate_id
                LEFT JOIN roles AS role ON role.id = decision.role_id
                WHERE application.id IS NULL
                   OR candidate.id IS NULL
                   OR role.id IS NULL
                   OR application.organization_id <> decision.organization_id
                   OR candidate.organization_id <> decision.organization_id
                   OR role.organization_id <> decision.organization_id
                   OR decision.candidate_id <> application.candidate_id
            ) THEN
                RAISE EXCEPTION
                    'agent decision identity cannot be resolved safely';
            END IF;
        END;
        $$
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = '{_CANDIDATE_PRESENT_CHECK}'
                  AND conrelid = 'agent_decisions'::regclass
            ) THEN
                ALTER TABLE agent_decisions
                ADD CONSTRAINT {_CANDIDATE_PRESENT_CHECK}
                CHECK (candidate_id IS NOT NULL) NOT VALID;
            END IF;
        END;
        $$
        """
    )
    op.execute(
        "ALTER TABLE agent_decisions "
        f"VALIDATE CONSTRAINT {_CANDIDATE_PRESENT_CHECK}"
    )
    # PostgreSQL can prove NOT NULL from the validated check without scanning
    # the populated table while holding ACCESS EXCLUSIVE.
    op.execute(
        "ALTER TABLE agent_decisions ALTER COLUMN candidate_id SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE agent_decisions "
        f"DROP CONSTRAINT {_CANDIDATE_PRESENT_CHECK}"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_agent_decisions_candidate_id'
                  AND conrelid = 'agent_decisions'::regclass
            ) THEN
                ALTER TABLE agent_decisions
                ADD CONSTRAINT fk_agent_decisions_candidate_id
                FOREIGN KEY (candidate_id) REFERENCES candidates(id) NOT VALID;
            END IF;
        END;
        $$
        """
    )
    op.execute(
        "ALTER TABLE agent_decisions "
        "VALIDATE CONSTRAINT fk_agent_decisions_candidate_id"
    )

    _create_non_unique_index_concurrently(
        _CANDIDATE_INDEX,
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_CANDIDATE_INDEX} "
        "ON agent_decisions (candidate_id)",
    )

    state = _index_state()
    if state is not None and _index_matches_contract(state):
        if _LEGACY_INDEX != _INDEX:
            _run_concurrently(
                f"DROP INDEX CONCURRENTLY IF EXISTS {_LEGACY_INDEX}"
            )
        return
    if state is not None:
        _run_concurrently(f"DROP INDEX CONCURRENTLY {_INDEX}")
    _run_concurrently(
        f"CREATE UNIQUE INDEX CONCURRENTLY {_INDEX} "
        "ON agent_decisions (organization_id, role_id, candidate_id) "
        f"WHERE {_ACTIVE_PREDICATE}"
    )
    if _LEGACY_INDEX != _INDEX:
        _run_concurrently(
            f"DROP INDEX CONCURRENTLY IF EXISTS {_LEGACY_INDEX}"
        )


def downgrade() -> None:
    raise RuntimeError(
        "canonical decision candidate identity and its active-slot audit repair "
        "cannot be removed safely; run older application code against the "
        "forward-compatible schema instead"
    )
