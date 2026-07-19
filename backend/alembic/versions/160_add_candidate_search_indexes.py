"""Add low-cost Postgres indexes for candidate rediscovery.

No new service or copied search database: taxonomy-aware skills/profile search
uses trigram GIN indexes over the existing enriched JSON/text, while CV
requirements use native Postgres full-text GIN indexes.

Revision ID: 160_add_candidate_search_indexes
Revises: 159_add_sister_roles
Create Date: 2026-07-14
"""
from __future__ import annotations

from alembic import op


revision = "160_add_candidate_search_indexes"
down_revision = "159_add_sister_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # These are PostgreSQL-specific GIN/trigram/full-text acceleration indexes.
    # SQLite has neither pg_trgm nor compatible operator classes; its existing
    # search queries remain functional without these optional physical indexes.
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # Concurrent builds keep live candidate ingestion/search writable. IF NOT
    # EXISTS makes a retry safe if a deployment is interrupted between indexes.
    with op.get_context().autocommit_block():
        op.execute(
            """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_candidates_search_skills_trgm
        ON candidates USING gin (
          lower(coalesce(skills::jsonb::text, '')) gin_trgm_ops
        )
            """
        )
        op.execute(
            """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_candidates_search_experience_trgm
        ON candidates USING gin (
          lower(coalesce(experience_entries::jsonb::text, '')) gin_trgm_ops
        )
            """
        )
        op.execute(
            """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_candidates_search_profile_trgm
        ON candidates USING gin (
          lower(coalesce(position, '') || ' ' || coalesce(headline, '') ||
                ' ' || coalesce(summary, '')) gin_trgm_ops
        )
            """
        )
        op.execute(
            """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_candidate_applications_cv_fts
        ON candidate_applications USING gin (
          to_tsvector('english', coalesce(cv_text, ''))
        )
            """
        )
        op.execute(
            """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_candidates_cv_fts
        ON candidates USING gin (
          to_tsvector('english', coalesce(cv_text, ''))
        )
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_candidates_cv_fts")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_candidate_applications_cv_fts")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_candidates_search_profile_trgm")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_candidates_search_experience_trgm")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_candidates_search_skills_trgm")
