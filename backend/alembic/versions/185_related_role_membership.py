"""Make related-role candidate membership and local state explicit.

Revision ID: 185_related_role_membership
Revises: 184_ai_routing_telemetry
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "185_related_role_membership"
down_revision = "184_ai_routing_telemetry"
branch_labels = None
depends_on = None


_CANDIDATE_TRIGGER = "trg_sister_evaluations_resolve_candidate"
_CANDIDATE_FUNCTION = "resolve_sister_evaluation_candidate_id"
_ATS_OWNER_TRIGGER = "trg_roles_clear_invalid_sister_ats_links"
_ATS_OWNER_FUNCTION = "clear_invalid_sister_evaluation_ats_links"


def _drop_ats_owner_foreign_key() -> None:
    """Drop the existing owner FK without assuming a generated DB name."""
    foreign_keys = sa.inspect(op.get_bind()).get_foreign_keys("roles")
    for foreign_key in foreign_keys:
        if foreign_key.get("constrained_columns") == ["ats_owner_role_id"]:
            constraint_name = foreign_key.get("name")
            if not constraint_name:
                raise RuntimeError("roles.ats_owner_role_id foreign key is unnamed")
            op.drop_constraint(constraint_name, "roles", type_="foreignkey")
            return
    raise RuntimeError("roles.ats_owner_role_id foreign key was not found")


def _create_candidate_compatibility_trigger() -> None:
    """Keep pre-185 writers safe during the additive compatibility phase.

    Old workers infer related membership from the ATS owner's entire roster:
    they omit ``candidate_id`` on insert and hard-delete evaluations when an
    owner application leaves that inferred roster. Candidate membership is
    explicit after 185, so legacy fan-out inserts fail closed and legacy deletes
    cannot erase it while old processes drain. Parent-table cascades remain
    permitted; ordinary application deletes are represented by ``deleted_at``.
    """

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_CANDIDATE_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            resolved_candidate_id INTEGER;
            resolved_organization_id INTEGER;
            role_organization_id INTEGER;
            declared_ats_owner_role_id INTEGER;
            ats_owner_organization_id INTEGER;
            ats_candidate_id INTEGER;
            ats_organization_id INTEGER;
            ats_role_id INTEGER;
            skip_live_identity_check BOOLEAN := FALSE;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                -- Pre-185 rows have no candidate_id. Resolve it before deciding
                -- whether this is a legacy inferred-roster delete or a genuine
                -- parent cascade. A parent already absent from the statement's
                -- snapshot must be allowed to cascade; an ordinary worker DELETE
                -- must not erase explicit role membership.
                resolved_candidate_id := OLD.candidate_id;
                IF resolved_candidate_id IS NULL THEN
                    SELECT application.candidate_id
                    INTO resolved_candidate_id
                    FROM candidate_applications AS application
                    WHERE application.id = OLD.source_application_id;
                END IF;
                PERFORM 1 FROM roles WHERE id = OLD.role_id;
                IF NOT FOUND THEN
                    RETURN OLD;
                END IF;
                PERFORM 1 FROM candidates WHERE id = resolved_candidate_id;
                IF NOT FOUND THEN
                    RETURN OLD;
                END IF;
                PERFORM 1
                FROM candidate_applications
                WHERE id = OLD.source_application_id;
                IF NOT FOUND THEN
                    RETURN OLD;
                END IF;
                -- A pre-185 worker may try to infer membership deletion from
                -- its owner roster. Preserve the explicit membership instead.
                RETURN NULL;
            END IF;

            -- Pre-185 workers can still write owner-projected stage fields but
            -- do not know the membership version. New role-local transitions
            -- increment version atomically with their state change. Preserve
            -- the independent role state when an old writer omits that cursor.
            IF TG_OP = 'UPDATE'
               AND COALESCE(NEW.version, 1) <= COALESCE(OLD.version, 1)
               AND (
                   NEW.pipeline_stage IS DISTINCT FROM OLD.pipeline_stage
                   OR NEW.pipeline_stage_updated_at
                        IS DISTINCT FROM OLD.pipeline_stage_updated_at
                   OR NEW.pipeline_stage_source
                        IS DISTINCT FROM OLD.pipeline_stage_source
                   OR NEW.application_outcome
                        IS DISTINCT FROM OLD.application_outcome
                   OR NEW.application_outcome_updated_at
                        IS DISTINCT FROM OLD.application_outcome_updated_at
                   OR NEW.application_outcome_source
                        IS DISTINCT FROM OLD.application_outcome_source
               ) THEN
                NEW.pipeline_stage := OLD.pipeline_stage;
                NEW.pipeline_stage_updated_at := OLD.pipeline_stage_updated_at;
                NEW.pipeline_stage_source := OLD.pipeline_stage_source;
                NEW.application_outcome := OLD.application_outcome;
                NEW.application_outcome_updated_at := OLD.application_outcome_updated_at;
                NEW.application_outcome_source := OLD.application_outcome_source;
            END IF;

            -- A pre-185 owner reconciliation converts a related evaluation to
            -- ``excluded`` when the shared ATS application closes. Closure is
            -- now only an action restriction; it cannot become this role's
            -- lifecycle or cancel its scoring state. Match the legacy marker
            -- narrowly so current role-owned scoring transitions remain valid.
            IF TG_OP = 'UPDATE'
               AND COALESCE(NEW.version, 1) <= COALESCE(OLD.version, 1)
               AND NEW.status = 'excluded'
               AND NEW.last_error_code = 'shared_application_closed'
            THEN
                NEW.status := OLD.status;
                NEW.error_message := OLD.error_message;
                NEW.last_error_code := OLD.last_error_code;
                NEW.next_attempt_at := OLD.next_attempt_at;
                NEW.dispatch_attempted_at := OLD.dispatch_attempted_at;
                NEW.started_at := OLD.started_at;
            END IF;

            SELECT application.candidate_id
                 , application.organization_id
            INTO resolved_candidate_id
               , resolved_organization_id
            FROM candidate_applications AS application
            WHERE application.id = NEW.source_application_id;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'source application % does not exist', NEW.source_application_id
                    USING ERRCODE = '23503';
            END IF;

            -- The pre-185 INSERT shape is an inferred ATS-owner fan-out, not an
            -- explicit membership decision. Keep the old writer healthy during
            -- a rolling deployment, but archive its row immediately so it
            -- cannot silently grow the independent logical-role pool or enter
            -- the scoring queue. Current code can later restore/rebind this
            -- audit shadow through the explicit membership workflow.
            IF TG_OP = 'INSERT' AND NEW.candidate_id IS NULL THEN
                NEW.candidate_id := resolved_candidate_id;
                NEW.deleted_at := COALESCE(NEW.deleted_at, CURRENT_TIMESTAMP);
                NEW.membership_source := 'legacy_compat_shadow';
                NEW.status := 'excluded';
                NEW.error_message :=
                    'Ignored legacy inferred membership during rolling migration';
                NEW.last_error_code := 'legacy_inferred_membership_ignored';
            END IF;

            IF NEW.candidate_id IS NULL THEN
                NEW.candidate_id := resolved_candidate_id;
            ELSIF NEW.candidate_id <> resolved_candidate_id THEN
                RAISE EXCEPTION
                    'candidate % does not own source application %',
                    NEW.candidate_id,
                    NEW.source_application_id
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.organization_id <> resolved_organization_id THEN
                RAISE EXCEPTION
                    'membership organization % does not own source application %',
                    NEW.organization_id,
                    NEW.source_application_id
                    USING ERRCODE = '23514';
            END IF;
            SELECT role.organization_id,
                   role.ats_owner_role_id,
                   owner.organization_id
            INTO role_organization_id,
                 declared_ats_owner_role_id,
                 ats_owner_organization_id
            FROM roles AS role
            LEFT JOIN roles AS owner ON owner.id = role.ats_owner_role_id
            WHERE role.id = NEW.role_id;
            IF NOT FOUND OR role_organization_id <> NEW.organization_id THEN
                RAISE EXCEPTION
                    'role % does not belong to membership organization %',
                    NEW.role_id,
                    NEW.organization_id
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.ats_application_id IS NOT NULL THEN
                SELECT application.candidate_id,
                       application.organization_id,
                       application.role_id
                INTO ats_candidate_id, ats_organization_id, ats_role_id
                FROM candidate_applications AS application
                WHERE application.id = NEW.ats_application_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'ATS application % does not exist',
                        NEW.ats_application_id
                        USING ERRCODE = '23503';
                END IF;
                IF declared_ats_owner_role_id IS NULL
                   OR ats_owner_organization_id IS DISTINCT FROM NEW.organization_id
                   OR ats_candidate_id <> NEW.candidate_id
                   OR ats_organization_id <> NEW.organization_id
                   OR ats_role_id <> declared_ats_owner_role_id
                THEN
                    RAISE EXCEPTION
                        'ATS application % is not the declared transport for '
                        'role %, organization %, candidate %',
                        NEW.ats_application_id,
                        NEW.role_id,
                        NEW.organization_id,
                        NEW.candidate_id
                        USING ERRCODE = '23514';
                END IF;
            END IF;

            -- Existing pre-185 duplicates are resolved by the migration after
            -- candidate hydration. Do not reject that one-time UPDATE merely
            -- because another legacy row has not been hydrated/archived yet.
            IF TG_OP = 'UPDATE' AND OLD.candidate_id IS NULL THEN
                skip_live_identity_check := TRUE;
            END IF;

            -- Serialize the only identity that represents live membership.
            -- The advisory lock closes the short rolling-deploy window before
            -- revision 187 can build its partial unique index: two old/new
            -- writers must not both observe an empty (role, candidate) slot.
            IF NEW.deleted_at IS NULL AND NOT skip_live_identity_check THEN
                PERFORM pg_advisory_xact_lock(NEW.role_id, NEW.candidate_id);
                PERFORM 1
                FROM sister_role_evaluations AS existing
                JOIN candidate_applications AS existing_source
                  ON existing_source.id = existing.source_application_id
                WHERE existing.role_id = NEW.role_id
                  AND COALESCE(
                        existing.candidate_id,
                        existing_source.candidate_id
                      ) = NEW.candidate_id
                  AND existing.deleted_at IS NULL
                  AND existing.id IS DISTINCT FROM NEW.id;
                IF FOUND THEN
                    RAISE EXCEPTION
                        'candidate % already has a live membership in role %',
                        NEW.candidate_id,
                        NEW.role_id
                        USING ERRCODE = '23505';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"DROP TRIGGER IF EXISTS {_CANDIDATE_TRIGGER} "
        "ON sister_role_evaluations"
    )
    op.execute(
        f"""
        CREATE TRIGGER {_CANDIDATE_TRIGGER}
        BEFORE INSERT OR DELETE OR UPDATE OF
            candidate_id, source_application_id, organization_id, role_id,
            ats_application_id,
            pipeline_stage, pipeline_stage_updated_at, pipeline_stage_source,
            application_outcome, application_outcome_updated_at,
            application_outcome_source, status, error_message, last_error_code,
            next_attempt_at, dispatch_attempted_at, started_at, version,
            deleted_at
        ON sister_role_evaluations
        FOR EACH ROW
        EXECUTE FUNCTION {_CANDIDATE_FUNCTION}()
        """
    )


def _create_ats_owner_change_trigger() -> None:
    """Null transport links that no longer match a role's declared owner."""

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_ATS_OWNER_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.ats_owner_role_id IS NOT DISTINCT FROM OLD.ats_owner_role_id THEN
                RETURN NEW;
            END IF;
            UPDATE sister_role_evaluations AS membership
            SET ats_application_id = NULL
            WHERE membership.role_id = NEW.id
              AND membership.ats_application_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM candidate_applications AS application
                  JOIN roles AS owner
                    ON owner.id = NEW.ats_owner_role_id
                   AND owner.organization_id = membership.organization_id
                  WHERE application.id = membership.ats_application_id
                    AND application.organization_id = membership.organization_id
                    AND application.candidate_id = membership.candidate_id
                    AND application.role_id = NEW.ats_owner_role_id
              );
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"DROP TRIGGER IF EXISTS {_ATS_OWNER_TRIGGER} ON roles"
    )
    op.execute(
        f"""
        CREATE TRIGGER {_ATS_OWNER_TRIGGER}
        AFTER UPDATE OF ats_owner_role_id ON roles
        FOR EACH ROW
        EXECUTE FUNCTION {_ATS_OWNER_FUNCTION}()
        """
    )


def _drop_candidate_compatibility_trigger() -> None:
    op.execute(
        f"DROP TRIGGER IF EXISTS {_CANDIDATE_TRIGGER} "
        "ON sister_role_evaluations"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {_CANDIDATE_FUNCTION}()")


def _add_constraint_if_missing(
    table: str,
    name: str,
    definition: str,
) -> None:
    """Add one PostgreSQL constraint without breaking an unstamped retry."""

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = '{name}'
                  AND conrelid = '{table}'::regclass
            ) THEN
                ALTER TABLE {table}
                ADD CONSTRAINT {name} {definition};
            END IF;
        END;
        $$
        """
    )


def _commit_additive_schema_phase() -> None:
    """Expose compatibility DDL before the populated-table backfill starts."""

    # Alembic otherwise holds every ACCESS EXCLUSIVE lock from the first ALTER
    # TABLE until revision 187 enters its first concurrent-index autocommit
    # block. Commit the retry-safe additive phase now: old workers immediately
    # see the compatibility trigger, and ordinary production traffic is not
    # blocked for the duration of the data repair.
    with op.get_context().autocommit_block():
        pass


def _legacy_implicit_snapshot_boundary(phase: str) -> None:
    """Name the atomic snapshot boundaries for concurrency contract tests."""

    if phase not in {"before", "after"}:
        raise ValueError(f"unknown legacy snapshot boundary: {phase}")


def upgrade() -> None:
    # Fail instead of waiting indefinitely for a busy production table. The
    # deployment can retry without leaving a half-applied additive phase.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE share_links "
        "ADD COLUMN IF NOT EXISTS view_role_id INTEGER"
    )
    _add_constraint_if_missing(
        "share_links",
        "fk_share_links_view_role_id",
        "FOREIGN KEY (view_role_id) REFERENCES roles(id) ON DELETE CASCADE",
    )
    # ATS ownership is optional transport metadata, not lifecycle ownership.
    # Deleting the transport role must preserve every independent related role.
    _drop_ats_owner_foreign_key()
    op.create_foreign_key(
        "fk_roles_ats_owner_role_id",
        "roles",
        "roles",
        ["ats_owner_role_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute(
        "ALTER TABLE roles "
        "ADD COLUMN IF NOT EXISTS related_source_role_id INTEGER"
    )
    _add_constraint_if_missing(
        "roles",
        "fk_roles_related_source_role_id",
        "FOREIGN KEY (related_source_role_id) "
        "REFERENCES roles(id) ON DELETE SET NULL",
    )
    # Historical code could persist an ATS-owner marker without updating the
    # discriminator. Normalize those rows before any membership backfill so
    # every downstream path sees one related-role identity.
    op.execute(
        """
        UPDATE roles
        SET role_kind = 'sister'
        WHERE ats_owner_role_id IS NOT NULL
           OR related_source_role_id IS NOT NULL
        """
    )
    # Before this revision a related role could only be created from its ATS
    # owner, so that link is the correct historical source snapshot.
    op.execute(
        """
        UPDATE roles
        SET related_source_role_id = ats_owner_role_id
        WHERE role_kind = 'sister'
          AND related_source_role_id IS NULL
        """
    )
    _add_constraint_if_missing(
        "roles",
        "ck_roles_related_identity_kind",
        """CHECK (
            (ats_owner_role_id IS NULL AND related_source_role_id IS NULL)
            OR role_kind = 'sister'
        ) NOT VALID""",
    )
    op.execute(
        """
        ALTER TABLE sister_role_evaluations
            ADD COLUMN IF NOT EXISTS candidate_id INTEGER,
            ADD COLUMN IF NOT EXISTS ats_application_id INTEGER,
            ADD COLUMN IF NOT EXISTS application_outcome VARCHAR(32)
                NOT NULL DEFAULT 'open',
            ADD COLUMN IF NOT EXISTS application_outcome_updated_at TIMESTAMPTZ
                NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ADD COLUMN IF NOT EXISTS application_outcome_source VARCHAR(16)
                NOT NULL DEFAULT 'system',
            ADD COLUMN IF NOT EXISTS membership_source VARCHAR(32)
                NOT NULL DEFAULT 'initial_snapshot',
            ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1,
            ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS manual_decision JSON
        """
    )

    # NOT VALID avoids a full validation scan while holding the stronger lock
    # required to add a foreign key. Validation below permits normal DML.
    _add_constraint_if_missing(
        "sister_role_evaluations",
        "fk_sister_evaluations_candidate_id",
        "FOREIGN KEY (candidate_id) REFERENCES candidates(id) "
        "ON DELETE CASCADE NOT VALID",
    )
    _add_constraint_if_missing(
        "sister_role_evaluations",
        "fk_sister_evaluations_ats_application_id",
        "FOREIGN KEY (ats_application_id) "
        "REFERENCES candidate_applications(id) ON DELETE SET NULL NOT VALID",
    )
    _create_candidate_compatibility_trigger()
    _create_ats_owner_change_trigger()
    _commit_additive_schema_phase()
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE roles VALIDATE CONSTRAINT ck_roles_related_identity_kind"
    )
    # Existing evaluation rows are already explicit memberships. Populate only
    # additive identity/lifecycle columns during this compatibility release.
    # Some historical stages were copied from the owner application, but old
    # workers can still rewrite those fields; cleaning them is therefore part of
    # the deferred contraction after those workers have drained.
    op.execute(
        """
        UPDATE sister_role_evaluations AS sre
        SET candidate_id = app.candidate_id,
            ats_application_id = CASE
                WHEN app.role_id = role.ats_owner_role_id THEN app.id
                ELSE (
                    SELECT owner_app.id
                    FROM candidate_applications AS owner_app
                    WHERE owner_app.organization_id = role.organization_id
                      AND owner_app.role_id = role.ats_owner_role_id
                      AND owner_app.candidate_id = app.candidate_id
                      AND owner_app.deleted_at IS NULL
                    ORDER BY owner_app.id DESC
                    LIMIT 1
                )
            END,
            application_outcome = CASE
                WHEN app.role_id = role.id
                    THEN COALESCE(app.application_outcome, 'open')
                ELSE 'open'
            END,
            application_outcome_updated_at = CASE
                WHEN app.role_id = role.id THEN COALESCE(
                    app.application_outcome_updated_at,
                    app.updated_at,
                    app.created_at,
                    CURRENT_TIMESTAMP
                )
                ELSE CURRENT_TIMESTAMP
            END,
            application_outcome_source = 'system',
            membership_source = 'legacy_explicit',
            version = COALESCE(sre.version, 1) + 1
        FROM candidate_applications AS app, roles AS role, candidates AS candidate
        WHERE app.id = sre.source_application_id
          AND role.id = sre.role_id
          AND candidate.id = app.candidate_id
          AND sre.candidate_id IS NULL
        """
    )

    # Old readers treated every owner application as an implicit member even
    # when its scoring row had not been created. Materialize that current pool
    # exactly once so the cutover does not make candidates disappear. Future
    # owner applications are not fanned out automatically. Candidate/role is
    # unique on candidate_applications, so each snapshot identity has one
    # deterministic owner application. If an old writer inserted that exact
    # row after the compatibility trigger committed, the conflict update
    # atomically converts its archived audit shadow into the snapshot member.
    _legacy_implicit_snapshot_boundary("before")
    op.execute(
        """
        INSERT INTO sister_role_evaluations (
            organization_id,
            role_id,
            candidate_id,
            source_application_id,
            ats_application_id,
            status,
            pipeline_stage,
            pipeline_stage_updated_at,
            pipeline_stage_source,
            application_outcome,
            application_outcome_updated_at,
            application_outcome_source,
            membership_source,
            spec_fingerprint,
            cv_fingerprint,
            error_message,
            queued_at,
            created_at
        )
        SELECT
            role.organization_id,
            role.id,
            app.candidate_id,
            app.id,
            app.id,
            CASE
                WHEN LENGTH(TRIM(COALESCE(app.cv_text, candidate.cv_text, ''))) > 0
                    THEN 'stale_held'
                ELSE 'unscorable'
            END,
            'applied',
            CURRENT_TIMESTAMP,
            'system',
            'open',
            CURRENT_TIMESTAMP,
            'system',
            'legacy_implicit_snapshot',
            MD5(COALESCE(role.job_spec_text, '')) || MD5(COALESCE(role.job_spec_text, '')),
            CASE
                WHEN LENGTH(TRIM(COALESCE(app.cv_text, candidate.cv_text, ''))) > 0
                    THEN MD5(COALESCE(app.cv_text, candidate.cv_text, ''))
                         || MD5(COALESCE(app.cv_text, candidate.cv_text, ''))
                ELSE NULL
            END,
            CASE
                WHEN LENGTH(TRIM(COALESCE(app.cv_text, candidate.cv_text, ''))) > 0
                    THEN 'Explicit re-evaluation is required after membership migration'
                ELSE 'No CV text available'
            END,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        FROM roles AS role
        JOIN candidate_applications AS app
          ON app.organization_id = role.organization_id
         AND app.role_id = role.ats_owner_role_id
         AND app.deleted_at IS NULL
        JOIN candidates AS candidate ON candidate.id = app.candidate_id
        LEFT JOIN sister_role_evaluations AS existing
          ON existing.role_id = role.id
         AND existing.candidate_id = app.candidate_id
         AND existing.membership_source IS DISTINCT FROM 'legacy_compat_shadow'
        WHERE role.role_kind = 'sister'
          AND role.deleted_at IS NULL
          AND existing.id IS NULL
        ON CONFLICT (role_id, source_application_id) DO UPDATE
        SET organization_id = EXCLUDED.organization_id,
            candidate_id = EXCLUDED.candidate_id,
            ats_application_id = EXCLUDED.ats_application_id,
            status = EXCLUDED.status,
            pipeline_stage = EXCLUDED.pipeline_stage,
            pipeline_stage_updated_at = EXCLUDED.pipeline_stage_updated_at,
            pipeline_stage_source = EXCLUDED.pipeline_stage_source,
            application_outcome = EXCLUDED.application_outcome,
            application_outcome_updated_at =
                EXCLUDED.application_outcome_updated_at,
            application_outcome_source = EXCLUDED.application_outcome_source,
            membership_source = EXCLUDED.membership_source,
            spec_fingerprint = EXCLUDED.spec_fingerprint,
            cv_fingerprint = EXCLUDED.cv_fingerprint,
            role_fit_score = NULL,
            summary = NULL,
            details = NULL,
            history = NULL,
            model_version = NULL,
            prompt_version = NULL,
            trace_id = NULL,
            cache_hit = FALSE,
            error_message = EXCLUDED.error_message,
            attempts = 0,
            next_attempt_at = NULL,
            dispatch_attempted_at = NULL,
            last_error_code = NULL,
            queued_at = EXCLUDED.queued_at,
            started_at = NULL,
            scored_at = NULL,
            updated_at = CURRENT_TIMESTAMP,
            version = COALESCE(sister_role_evaluations.version, 1) + 1,
            deleted_at = NULL,
            manual_decision = NULL
        WHERE sister_role_evaluations.membership_source =
                  'legacy_compat_shadow'
          AND sister_role_evaluations.deleted_at IS NOT NULL
          AND sister_role_evaluations.organization_id =
                  EXCLUDED.organization_id
          AND sister_role_evaluations.candidate_id = EXCLUDED.candidate_id
        """
    )
    _legacy_implicit_snapshot_boundary("after")

    # A direct application is the related role's own lifecycle record and must
    # win over an ATS-owner-backed compatibility row. Preserve all other rows as
    # immutable audit shadows; old workers may still update the owner-keyed row,
    # but current readers only admit the direct live membership.
    # Rows created by the rolling-deploy compatibility trigger are not existing
    # memberships: exclude them from both ranking passes so an insert after the
    # additive commit can never become the sole winner and be unarchived.

    # Archive losing legacy duplicates first. The compatibility trigger is
    # already visible to live writers, so restoring the winner in a second
    # statement cannot momentarily collide with another live membership.
    op.execute(
        """
        WITH ranked_memberships AS (
            SELECT
                sre.id,
                ROW_NUMBER() OVER (
                    PARTITION BY sre.role_id, sre.candidate_id
                    ORDER BY
                        (source_app.role_id = sre.role_id) DESC,
                        (sre.role_fit_score IS NOT NULL) DESC,
                        COALESCE(sre.scored_at, sre.updated_at, sre.created_at) DESC
                            NULLS LAST,
                        sre.id ASC
                ) AS membership_rank
            FROM sister_role_evaluations AS sre
            JOIN candidate_applications AS source_app
              ON source_app.id = sre.source_application_id
            WHERE sre.membership_source IS DISTINCT FROM 'legacy_compat_shadow'
        )
        UPDATE sister_role_evaluations AS sre
        SET deleted_at = COALESCE(sre.deleted_at, CURRENT_TIMESTAMP),
            membership_source = 'legacy_compat_shadow'
        FROM ranked_memberships AS ranked
        WHERE ranked.id = sre.id
          AND ranked.membership_rank > 1
        """
    )
    op.execute(
        """
        WITH ranked_memberships AS (
            SELECT
                sre.id,
                ROW_NUMBER() OVER (
                    PARTITION BY sre.role_id, sre.candidate_id
                    ORDER BY
                        (source_app.role_id = sre.role_id) DESC,
                        (sre.role_fit_score IS NOT NULL) DESC,
                        COALESCE(sre.scored_at, sre.updated_at, sre.created_at) DESC
                            NULLS LAST,
                        sre.id ASC
                ) AS membership_rank
            FROM sister_role_evaluations AS sre
            JOIN candidate_applications AS source_app
              ON source_app.id = sre.source_application_id
            WHERE sre.membership_source IS DISTINCT FROM 'legacy_compat_shadow'
        )
        UPDATE sister_role_evaluations AS sre
        SET deleted_at = NULL,
            membership_source = CASE
                WHEN source_app.role_id = sre.role_id THEN 'direct'
                ELSE sre.membership_source
            END,
            ats_application_id = CASE
                WHEN source_app.role_id = sre.role_id THEN (
                    SELECT owner_app.id
                    FROM roles AS membership_role
                    JOIN candidate_applications AS owner_app
                      ON owner_app.organization_id = membership_role.organization_id
                     AND owner_app.role_id = membership_role.ats_owner_role_id
                     AND owner_app.candidate_id = sre.candidate_id
                     AND owner_app.deleted_at IS NULL
                    WHERE membership_role.id = sre.role_id
                    ORDER BY owner_app.id DESC
                    LIMIT 1
                )
                ELSE sre.ats_application_id
            END
        FROM ranked_memberships AS ranked,
             candidate_applications AS source_app
        WHERE ranked.id = sre.id
          AND ranked.membership_rank = 1
          AND source_app.id = sre.source_application_id
        """
    )

    # Treat the optional ATS application as a typed transport reference, not a
    # generic application foreign key. Historical code could only persist the
    # source application; however, deployments may already contain partially
    # backfilled rows from an interrupted rehearsal. Preserve a valid link,
    # otherwise select the latest live application for the same tenant,
    # candidate, and declared ATS owner. If none exists, NULL is the only safe
    # value: canonical reads must never borrow another candidate's ATS state.
    op.execute(
        """
        UPDATE sister_role_evaluations AS membership
        SET ats_application_id = (
            SELECT replacement.id
            FROM candidate_applications AS replacement
            WHERE replacement.organization_id = membership.organization_id
              AND replacement.candidate_id = membership.candidate_id
              AND replacement.role_id = role.ats_owner_role_id
              AND replacement.deleted_at IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM roles AS transport_owner
                  WHERE transport_owner.id = role.ats_owner_role_id
                    AND transport_owner.organization_id = membership.organization_id
              )
            ORDER BY replacement.id DESC
            LIMIT 1
        )
        FROM roles AS role
        WHERE role.id = membership.role_id
          AND membership.ats_application_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM candidate_applications AS linked
              JOIN roles AS transport_owner
                ON transport_owner.id = role.ats_owner_role_id
               AND transport_owner.organization_id = membership.organization_id
              WHERE linked.id = membership.ats_application_id
                AND linked.organization_id = membership.organization_id
                AND linked.candidate_id = membership.candidate_id
                AND linked.role_id = role.ats_owner_role_id
          )
        """
    )

    # Remove only state known to have been copied from the ATS owner's generic
    # advanced/excluded projection. A role-owned direct application was selected
    # above and is never reset. Existing role-fit evidence remains available but
    # is held for explicit re-evaluation where no completed score exists.
    op.execute(
        """
        UPDATE sister_role_evaluations AS sre
        SET pipeline_stage = 'applied',
            pipeline_stage_updated_at = CURRENT_TIMESTAMP,
            application_outcome = 'open',
            application_outcome_updated_at = CURRENT_TIMESTAMP,
            status = CASE
                WHEN sre.role_fit_score IS NOT NULL THEN 'done'
                WHEN LENGTH(TRIM(COALESCE(source_app.cv_text, candidate.cv_text, ''))) > 0
                    THEN 'stale_held'
                ELSE 'unscorable'
            END,
            error_message = CASE
                WHEN sre.role_fit_score IS NOT NULL THEN NULL
                WHEN LENGTH(TRIM(COALESCE(source_app.cv_text, candidate.cv_text, ''))) > 0
                    THEN 'Explicit re-evaluation is required after membership migration'
                ELSE 'No CV text available'
            END,
            last_error_code = NULL,
            version = COALESCE(sre.version, 1) + 1
        FROM roles AS role,
             candidate_applications AS source_app,
             candidates AS candidate
        WHERE sre.role_id = role.id
          AND sre.source_application_id = source_app.id
          AND source_app.candidate_id = candidate.id
          AND source_app.role_id = role.ats_owner_role_id
          AND sre.deleted_at IS NULL
          AND LOWER(TRIM(COALESCE(sre.pipeline_stage_source, 'system'))) = 'system'
          AND (
              LOWER(TRIM(COALESCE(sre.pipeline_stage, ''))) = 'advanced'
              OR LOWER(TRIM(COALESCE(sre.status, ''))) = 'excluded'
          )
        """
    )

    # The compatibility trigger validates explicit current-writer identity
    # before database constraints run. Legacy inferred fan-out inserts are
    # archived without entering the logical pool. Canonical role/candidate
    # uniqueness remains serialized by the trigger until revision 187 installs
    # the partial unique index.
    op.execute(
        """
        ALTER TABLE sister_role_evaluations
        ADD CONSTRAINT ck_sister_evaluations_candidate_id_present
        CHECK (candidate_id IS NOT NULL) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE sister_role_evaluations "
        "VALIDATE CONSTRAINT ck_sister_evaluations_candidate_id_present"
    )
    op.execute(
        "ALTER TABLE sister_role_evaluations "
        "VALIDATE CONSTRAINT fk_sister_evaluations_candidate_id"
    )
    op.execute(
        "ALTER TABLE sister_role_evaluations "
        "VALIDATE CONSTRAINT fk_sister_evaluations_ats_application_id"
    )
    op.alter_column(
        "sister_role_evaluations",
        "candidate_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_constraint(
        "ck_sister_evaluations_candidate_id_present",
        "sister_role_evaluations",
        type_="check",
    )
    op.drop_constraint(
        "sister_role_evaluations_source_application_id_fkey",
        "sister_role_evaluations",
        type_="foreignkey",
    )
    op.execute(
        """
        ALTER TABLE sister_role_evaluations
        ADD CONSTRAINT sister_role_evaluations_source_application_id_fkey
        FOREIGN KEY (source_application_id) REFERENCES candidate_applications(id)
        ON DELETE RESTRICT NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE sister_role_evaluations VALIDATE CONSTRAINT "
        "sister_role_evaluations_source_application_id_fkey"
    )
    # Lookup indexes are built by revision 187, whose only operations are
    # idempotent CREATE INDEX CONCURRENTLY statements. Keeping them out of this
    # data revision makes 185 atomic and safely rerunnable after any failure.


def downgrade() -> None:
    raise RuntimeError(
        "related-role membership contains independent lifecycle and outcome "
        "truth that the pre-185 schema cannot represent; roll application "
        "code back against the forward-compatible schema instead"
    )
