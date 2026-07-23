"""Expand the immutable candidate-action ledger with logical provenance.

Revision ID: 186_candidate_action_provenance
Revises: 185_related_role_membership
Create Date: 2026-07-22

The event ledger has been database-enforced append-only since revision 143.
Consequently this revision never rewrites historical rows.  New and mixed-
version inserts are normalized by an insert-only trigger; readers resolve the
nullable legacy rows from their immutable metadata, linked decision, and
application at query time.  A later, separately deployable contraction may
materialize that projection only if the audit-retention policy explicitly
allows it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "186_candidate_action_provenance"
down_revision = "185_related_role_membership"
branch_labels = None
depends_on = None


_EVENT_TRIGGER = "trg_candidate_events_resolve_provenance"
_EVENT_FUNCTION = "resolve_candidate_event_provenance"


def _create_event_insert_trigger() -> None:
    """Normalize every post-expand insert without weakening append-only audit."""

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_EVENT_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            application_candidate_id INTEGER;
            application_role_id INTEGER;
            application_organization_id INTEGER;
            metadata_role_text TEXT;
            metadata_decision_text TEXT;
            metadata_role_id INTEGER;
            requested_decision_id BIGINT;
            resolved_decision_id BIGINT;
            decision_role_id INTEGER;
            resolved_role_id INTEGER;
            role_is_authorized BOOLEAN;
            normalized_effect_status TEXT;
        BEGIN
            SELECT application.candidate_id,
                   application.role_id,
                   application.organization_id
            INTO application_candidate_id,
                 application_role_id,
                 application_organization_id
            FROM candidate_applications AS application
            WHERE application.id = NEW.application_id;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'application % does not exist', NEW.application_id
                    USING ERRCODE = '23503';
            END IF;
            IF application_organization_id <> NEW.organization_id THEN
                RAISE EXCEPTION
                    'event organization % does not own application %',
                    NEW.organization_id,
                    NEW.application_id
                    USING ERRCODE = '23514';
            END IF;

            metadata_role_text := COALESCE(
                NEW.metadata ->> 'acting_role_id',
                NEW.metadata ->> 'role_id',
                ''
            );
            IF metadata_role_text ~ '^[1-9][0-9]{{0,9}}$'
               AND metadata_role_text::NUMERIC <= 2147483647 THEN
                SELECT role.id
                INTO metadata_role_id
                FROM roles AS role
                WHERE role.id = metadata_role_text::INTEGER
                  AND role.organization_id = NEW.organization_id
                  AND (
                      role.id = application_role_id
                      OR EXISTS (
                          SELECT 1
                          FROM sister_role_evaluations AS membership
                          WHERE membership.organization_id = NEW.organization_id
                            AND membership.role_id = role.id
                            AND membership.candidate_id = application_candidate_id
                            AND membership.deleted_at IS NULL
                            AND NEW.application_id IN (
                                membership.source_application_id,
                                membership.ats_application_id
                            )
                      )
                  );
            END IF;

            metadata_decision_text := COALESCE(
                NEW.metadata ->> 'agent_decision_id',
                NEW.metadata ->> 'decision_id',
                ''
            );
            IF NEW.agent_decision_id IS NOT NULL THEN
                requested_decision_id := NEW.agent_decision_id;
            ELSIF metadata_decision_text ~ '^[1-9][0-9]{{0,18}}$'
                  AND metadata_decision_text::NUMERIC <= 9223372036854775807 THEN
                requested_decision_id := metadata_decision_text::BIGINT;
            END IF;

            IF requested_decision_id IS NOT NULL THEN
                SELECT decision.id,
                       decision.role_id
                INTO resolved_decision_id,
                     decision_role_id
                FROM agent_decisions AS decision
                JOIN candidate_applications AS decision_application
                  ON decision_application.id = decision.application_id
                WHERE decision.id = requested_decision_id
                  AND decision.organization_id = NEW.organization_id
                  AND decision_application.organization_id = NEW.organization_id
                  AND decision_application.candidate_id = application_candidate_id;
            END IF;

            -- A first-class FK is an explicit current-writer contract.  Legacy
            -- JSON hints remain best-effort, but an invalid normalized value is
            -- never silently erased.
            IF NEW.agent_decision_id IS NOT NULL
               AND resolved_decision_id IS NULL THEN
                RAISE EXCEPTION
                    'decision % is not valid provenance for application %',
                    NEW.agent_decision_id,
                    NEW.application_id
                    USING ERRCODE = '23514';
            END IF;

            resolved_role_id := COALESCE(
                NEW.role_id,
                metadata_role_id,
                decision_role_id,
                application_role_id
            );
            SELECT (
                role.organization_id = NEW.organization_id
                AND (
                    role.id = application_role_id
                    OR EXISTS (
                        SELECT 1
                        FROM sister_role_evaluations AS membership
                        WHERE membership.organization_id = NEW.organization_id
                          AND membership.role_id = role.id
                          AND membership.candidate_id = application_candidate_id
                          AND membership.deleted_at IS NULL
                          AND NEW.application_id IN (
                              membership.source_application_id,
                              membership.ats_application_id
                          )
                    )
                )
            )
            INTO role_is_authorized
            FROM roles AS role
            WHERE role.id = resolved_role_id;
            IF NOT COALESCE(role_is_authorized, FALSE) THEN
                RAISE EXCEPTION
                    'candidate % is not a live member of event role %',
                    application_candidate_id,
                    resolved_role_id
                    USING ERRCODE = '23514';
            END IF;

            IF resolved_decision_id IS NOT NULL
               AND decision_role_id <> resolved_role_id THEN
                IF NEW.agent_decision_id IS NOT NULL THEN
                    RAISE EXCEPTION
                        'decision % belongs to role %, not event role %',
                        resolved_decision_id,
                        decision_role_id,
                        resolved_role_id
                        USING ERRCODE = '23514';
                END IF;
                resolved_decision_id := NULL;
            END IF;

            normalized_effect_status := LOWER(BTRIM(COALESCE(NEW.effect_status, '')));
            IF normalized_effect_status = '' THEN
                normalized_effect_status := CASE
                    WHEN LOWER(NEW.event_type) LIKE '%failed%'
                      OR LOWER(NEW.event_type) LIKE '%error%' THEN 'failed'
                    WHEN LOWER(NEW.event_type) LIKE '%skipped%' THEN 'skipped'
                    WHEN LOWER(NEW.event_type) IN (
                        'pipeline_stage_changed',
                        'application_outcome_changed',
                        'role_pipeline_stage_changed',
                        'role_application_outcome_changed',
                        'workable_moved',
                        'bullhorn_moved',
                        'workable_disqualified',
                        'bullhorn_rejected',
                        'assessment_invite_sent',
                        'assessment_invite_resent'
                    ) THEN 'confirmed'
                    ELSE NULL
                END;
            END IF;

            NEW.role_id := resolved_role_id;
            NEW.agent_decision_id := resolved_decision_id;
            -- Requested ATS transport targets often travel in the metadata of
            -- the preceding Tali pipeline transition.  They are intent, not
            -- proof that the provider move happened.  Keep each event's target
            -- tied to the system whose effect it actually confirms.
            NEW.target_stage := CASE
                WHEN LOWER(NEW.event_type) IN (
                    'pipeline_stage_changed',
                    'role_pipeline_stage_changed'
                ) THEN NULLIF(BTRIM(NEW.to_stage), '')
                WHEN LOWER(NEW.event_type) IN (
                    'application_outcome_changed',
                    'role_application_outcome_changed'
                ) THEN NULLIF(BTRIM(NEW.to_outcome), '')
                ELSE COALESCE(
                    NULLIF(BTRIM(NEW.target_stage), ''),
                    NULLIF(BTRIM(NEW.metadata ->> 'target_stage'), ''),
                    NULLIF(BTRIM(NEW.metadata ->> 'workable_target_stage'), ''),
                    NULLIF(BTRIM(NEW.metadata ->> 'bullhorn_status'), ''),
                    NULLIF(BTRIM(NEW.to_stage), ''),
                    NULLIF(BTRIM(NEW.to_outcome), '')
                )
            END;
            NEW.effect_status := normalized_effect_status;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_EVENT_TRIGGER}
        BEFORE INSERT ON candidate_application_events
        FOR EACH ROW
        EXECUTE FUNCTION {_EVENT_FUNCTION}()
        """
    )


def upgrade() -> None:
    op.add_column(
        "agent_decisions",
        sa.Column(
            "resolution_metadata",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )
    # Nullable is deliberate: revision 143 made historical events immutable.
    # All new inserts are populated by the trigger below; old rows are resolved
    # from their original evidence by the action-history reader.
    op.add_column(
        "candidate_application_events",
        sa.Column("role_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "candidate_application_events",
        sa.Column("agent_decision_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "candidate_application_events",
        sa.Column("target_stage", sa.String(), nullable=True),
    )
    op.add_column(
        "candidate_application_events",
        sa.Column("effect_status", sa.String(), nullable=True),
    )
    op.execute(
        """
        ALTER TABLE candidate_application_events
        ADD CONSTRAINT fk_candidate_application_events_role_id
        FOREIGN KEY (role_id) REFERENCES roles(id) NOT VALID
        """
    )
    op.execute(
        """
        ALTER TABLE candidate_application_events
        ADD CONSTRAINT fk_candidate_application_events_agent_decision_id
        FOREIGN KEY (agent_decision_id) REFERENCES agent_decisions(id) NOT VALID
        """
    )
    _create_event_insert_trigger()


def downgrade() -> None:
    raise RuntimeError(
        "candidate action provenance is append-only and cannot be removed "
        "without losing logical-role audit truth; roll application code back "
        "against the forward-compatible schema instead"
    )
