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


def upgrade() -> None:
    op.add_column(
        "share_links",
        sa.Column("view_role_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_share_links_view_role_id",
        "share_links",
        "roles",
        ["view_role_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_share_links_view_role_id",
        "share_links",
        ["view_role_id"],
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

    op.add_column(
        "roles",
        sa.Column("related_source_role_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_roles_related_source_role_id",
        "roles",
        "roles",
        ["related_source_role_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_roles_related_source_role_id",
        "roles",
        ["related_source_role_id"],
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

    op.add_column(
        "sister_role_evaluations",
        sa.Column("candidate_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sister_role_evaluations",
        sa.Column("ats_application_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sister_role_evaluations",
        sa.Column(
            "application_outcome",
            sa.String(length=32),
            nullable=False,
            server_default="open",
        ),
    )
    op.add_column(
        "sister_role_evaluations",
        sa.Column(
            "application_outcome_updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "sister_role_evaluations",
        sa.Column(
            "application_outcome_source",
            sa.String(length=16),
            nullable=False,
            server_default="system",
        ),
    )
    op.add_column(
        "sister_role_evaluations",
        sa.Column(
            "membership_source",
            sa.String(length=32),
            nullable=False,
            server_default="initial_snapshot",
        ),
    )
    op.add_column(
        "sister_role_evaluations",
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "sister_role_evaluations",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_foreign_key(
        "fk_sister_evaluations_candidate_id",
        "sister_role_evaluations",
        "candidates",
        ["candidate_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_sister_evaluations_ats_application_id",
        "sister_role_evaluations",
        "candidate_applications",
        ["ats_application_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Existing evaluation rows are already explicit memberships. Preserve
    # their genuinely local stage, but remove legacy state that was copied from
    # the owner application: owner close/advance is an ATS restriction after
    # this revision, never the related role's local outcome or stage.
    op.execute(
        """
        UPDATE sister_role_evaluations AS sre
        SET candidate_id = app.candidate_id,
            ats_application_id = CASE
                WHEN app.role_id = role.ats_owner_role_id THEN app.id
                ELSE NULL
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
            pipeline_stage = CASE
                WHEN LOWER(TRIM(COALESCE(app.pipeline_stage, ''))) = 'advanced'
                 AND LOWER(TRIM(COALESCE(sre.pipeline_stage, ''))) = 'advanced'
                 AND LOWER(TRIM(COALESCE(sre.pipeline_stage_source, 'system'))) = 'system'
                 AND app.role_id = role.ats_owner_role_id
                    THEN 'applied'
                ELSE COALESCE(sre.pipeline_stage, 'applied')
            END,
            pipeline_stage_updated_at = CASE
                WHEN LOWER(TRIM(COALESCE(app.pipeline_stage, ''))) = 'advanced'
                 AND LOWER(TRIM(COALESCE(sre.pipeline_stage, ''))) = 'advanced'
                 AND LOWER(TRIM(COALESCE(sre.pipeline_stage_source, 'system'))) = 'system'
                 AND app.role_id = role.ats_owner_role_id
                    THEN CURRENT_TIMESTAMP
                ELSE sre.pipeline_stage_updated_at
            END,
            status = CASE
                WHEN app.role_id = role.ats_owner_role_id
                 AND (
                  sre.status = 'excluded'
                  OR (
                    sre.status = 'done'
                    AND sre.role_fit_score IS NULL
                    AND LOWER(TRIM(COALESCE(app.pipeline_stage, ''))) = 'advanced'
                    AND LOWER(TRIM(COALESCE(sre.pipeline_stage, ''))) = 'advanced'
                    AND LOWER(TRIM(COALESCE(sre.pipeline_stage_source, 'system'))) = 'system'
                  )
                 )
                THEN CASE
                    WHEN sre.role_fit_score IS NOT NULL THEN 'done'
                    WHEN LENGTH(TRIM(COALESCE(app.cv_text, candidate.cv_text, ''))) > 0
                        THEN 'stale_held'
                    ELSE 'unscorable'
                END
                ELSE sre.status
            END,
            error_message = CASE
                WHEN app.role_id = role.ats_owner_role_id
                 AND (
                  sre.status = 'excluded'
                  OR (
                    sre.status = 'done'
                    AND sre.role_fit_score IS NULL
                    AND LOWER(TRIM(COALESCE(app.pipeline_stage, ''))) = 'advanced'
                    AND LOWER(TRIM(COALESCE(sre.pipeline_stage, ''))) = 'advanced'
                    AND LOWER(TRIM(COALESCE(sre.pipeline_stage_source, 'system'))) = 'system'
                  )
                 )
                THEN CASE
                    WHEN sre.role_fit_score IS NOT NULL THEN NULL
                    WHEN LENGTH(TRIM(COALESCE(app.cv_text, candidate.cv_text, ''))) > 0
                        THEN 'Explicit re-evaluation is required after membership migration'
                    ELSE 'No CV text available'
                END
                ELSE sre.error_message
            END,
            last_error_code = CASE
                WHEN app.role_id = role.ats_owner_role_id
                 AND sre.status = 'excluded' THEN NULL
                ELSE sre.last_error_code
            END
        FROM candidate_applications AS app, roles AS role, candidates AS candidate
        WHERE app.id = sre.source_application_id
          AND role.id = sre.role_id
          AND candidate.id = app.candidate_id
        """
    )

    # Old readers treated every owner application as an implicit member even
    # when its scoring row had not been created. Materialize that current pool
    # exactly once so the cutover does not make candidates disappear. Future
    # owner applications are not fanned out automatically.
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
        WHERE role.role_kind = 'sister'
          AND role.deleted_at IS NULL
          AND existing.id IS NULL
        """
    )

    # A few legacy entry points could create a direct application on a related
    # role. Such an explicit local application wins as the evidence source;
    # retain any owner application solely as the optional ATS restriction link.
    # Consolidate any legacy duplicate rows for the same logical member before
    # changing the evidence source, preferring a scored row and then a row that
    # was already backed by the direct application.
    op.execute(
        """
        WITH ranked_memberships AS (
            SELECT
                sre.id,
                ROW_NUMBER() OVER (
                    PARTITION BY sre.role_id, sre.candidate_id
                    ORDER BY
                        (sre.role_fit_score IS NOT NULL) DESC,
                        (source_app.role_id = sre.role_id) DESC,
                        sre.id ASC
                ) AS membership_rank
            FROM sister_role_evaluations AS sre
            JOIN candidate_applications AS source_app
              ON source_app.id = sre.source_application_id
        )
        DELETE FROM sister_role_evaluations AS sre
        USING ranked_memberships AS ranked
        WHERE ranked.id = sre.id
          AND ranked.membership_rank > 1
        """
    )
    op.execute(
        """
        UPDATE sister_role_evaluations AS sre
        SET source_application_id = direct_app.id,
            ats_application_id = (
                SELECT owner_app.id
                FROM roles AS owner_link
                JOIN candidate_applications AS owner_app
                  ON owner_app.organization_id = owner_link.organization_id
                 AND owner_app.role_id = owner_link.ats_owner_role_id
                 AND owner_app.candidate_id = direct_app.candidate_id
                 AND owner_app.deleted_at IS NULL
                WHERE owner_link.id = sre.role_id
                LIMIT 1
            ),
            membership_source = 'direct'
        FROM candidate_applications AS direct_app
        WHERE direct_app.role_id = sre.role_id
          AND direct_app.candidate_id = sre.candidate_id
          AND direct_app.deleted_at IS NULL
        """
    )
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
            direct_app.candidate_id,
            direct_app.id,
            owner_app.id,
            CASE
                WHEN LENGTH(TRIM(COALESCE(direct_app.cv_text, candidate.cv_text, ''))) > 0
                    THEN 'stale_held'
                ELSE 'unscorable'
            END,
            COALESCE(direct_app.pipeline_stage, 'applied'),
            COALESCE(direct_app.pipeline_stage_updated_at, direct_app.created_at, CURRENT_TIMESTAMP),
            COALESCE(direct_app.pipeline_stage_source, 'system'),
            COALESCE(direct_app.application_outcome, 'open'),
            COALESCE(
                direct_app.application_outcome_updated_at,
                direct_app.updated_at,
                direct_app.created_at,
                CURRENT_TIMESTAMP
            ),
            'system',
            'direct',
            MD5(COALESCE(role.job_spec_text, '')) || MD5(COALESCE(role.job_spec_text, '')),
            CASE
                WHEN LENGTH(TRIM(COALESCE(direct_app.cv_text, candidate.cv_text, ''))) > 0
                    THEN MD5(COALESCE(direct_app.cv_text, candidate.cv_text, ''))
                         || MD5(COALESCE(direct_app.cv_text, candidate.cv_text, ''))
                ELSE NULL
            END,
            CASE
                WHEN LENGTH(TRIM(COALESCE(direct_app.cv_text, candidate.cv_text, ''))) > 0
                    THEN 'Explicit re-evaluation is required after membership migration'
                ELSE 'No CV text available'
            END,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        FROM roles AS role
        JOIN candidate_applications AS direct_app
          ON direct_app.organization_id = role.organization_id
         AND direct_app.role_id = role.id
         AND direct_app.deleted_at IS NULL
        JOIN candidates AS candidate ON candidate.id = direct_app.candidate_id
        LEFT JOIN candidate_applications AS owner_app
          ON owner_app.organization_id = role.organization_id
         AND owner_app.role_id = role.ats_owner_role_id
         AND owner_app.candidate_id = direct_app.candidate_id
         AND owner_app.deleted_at IS NULL
        LEFT JOIN sister_role_evaluations AS existing
          ON existing.role_id = role.id
         AND existing.candidate_id = direct_app.candidate_id
        WHERE role.role_kind = 'sister'
          AND role.deleted_at IS NULL
          AND existing.id IS NULL
        """
    )

    op.create_unique_constraint(
        "uq_sister_evaluations_role_candidate",
        "sister_role_evaluations",
        ["role_id", "candidate_id"],
    )
    op.create_index(
        "ix_sister_role_evaluations_candidate_id",
        "sister_role_evaluations",
        ["candidate_id"],
    )
    op.create_index(
        "ix_sister_role_evaluations_ats_application_id",
        "sister_role_evaluations",
        ["ats_application_id"],
    )
    op.create_index(
        "ix_sister_role_evaluations_deleted_at",
        "sister_role_evaluations",
        ["deleted_at"],
    )
    op.create_index(
        "ix_sister_evaluations_role_membership_state",
        "sister_role_evaluations",
        ["role_id", "deleted_at", "application_outcome", "pipeline_stage"],
    )

    op.drop_constraint(
        "sister_role_evaluations_source_application_id_fkey",
        "sister_role_evaluations",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "sister_role_evaluations_source_application_id_fkey",
        "sister_role_evaluations",
        "candidate_applications",
        ["source_application_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "sister_role_evaluations_source_application_id_fkey",
        "sister_role_evaluations",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "sister_role_evaluations_source_application_id_fkey",
        "sister_role_evaluations",
        "candidate_applications",
        ["source_application_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_index(
        "ix_sister_evaluations_role_membership_state",
        table_name="sister_role_evaluations",
    )
    op.drop_index(
        "ix_sister_role_evaluations_deleted_at",
        table_name="sister_role_evaluations",
    )
    op.drop_index(
        "ix_sister_role_evaluations_ats_application_id",
        table_name="sister_role_evaluations",
    )
    op.drop_index(
        "ix_sister_role_evaluations_candidate_id",
        table_name="sister_role_evaluations",
    )
    op.drop_constraint(
        "uq_sister_evaluations_role_candidate",
        "sister_role_evaluations",
        type_="unique",
    )
    op.drop_constraint(
        "fk_sister_evaluations_ats_application_id",
        "sister_role_evaluations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_sister_evaluations_candidate_id",
        "sister_role_evaluations",
        type_="foreignkey",
    )
    op.drop_column("sister_role_evaluations", "deleted_at")
    op.drop_column("sister_role_evaluations", "membership_source")
    op.drop_column("sister_role_evaluations", "version")
    op.drop_column("sister_role_evaluations", "application_outcome_source")
    op.drop_column("sister_role_evaluations", "application_outcome_updated_at")
    op.drop_column("sister_role_evaluations", "application_outcome")
    op.drop_column("sister_role_evaluations", "ats_application_id")
    op.drop_column("sister_role_evaluations", "candidate_id")
    op.drop_index("ix_roles_related_source_role_id", table_name="roles")
    op.drop_constraint(
        "fk_roles_related_source_role_id",
        "roles",
        type_="foreignkey",
    )
    op.drop_column("roles", "related_source_role_id")
    op.drop_constraint(
        "fk_roles_ats_owner_role_id",
        "roles",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "roles_ats_owner_role_id_fkey",
        "roles",
        "roles",
        ["ats_owner_role_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_index("ix_share_links_view_role_id", table_name="share_links")
    op.drop_constraint(
        "fk_share_links_view_role_id",
        "share_links",
        type_="foreignkey",
    )
    op.drop_column("share_links", "view_role_id")
