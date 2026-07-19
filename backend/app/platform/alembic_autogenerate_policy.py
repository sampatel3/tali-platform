"""Fail-closed ownership policy for Alembic autogeneration.

The canonical migration chain deliberately owns a small number of database
objects that are not represented by SQLAlchemy model metadata.  Without an
explicit policy, ``alembic check`` proposes destructive drops for those
objects.  Every exception below is an exact ``(table, object)`` pair; an
unknown reflected table, column, or index remains visible as schema drift.

Primary keys already provide an index on PostgreSQL.  Historical models still
carry ``index=True`` on some primary-key columns, so metadata-only PK indexes
are ignored semantically instead of growing a brittle per-table allowlist.
Non-PK metadata indexes remain fail-closed.
"""

from __future__ import annotations

from typing import Any


# ``assessment_sessions`` is part of the pre-Alembic schema captured by
# revision 000.  It is retained to preserve historical data even though no
# current ORM model writes it.
PRESERVED_DATABASE_TABLES = frozenset({"assessment_sessions"})

# These role columns are retained as readable legacy data.  Their successors
# are role criteria and score-threshold policy; autogenerate must never turn
# model retirement into an implicit data-deletion migration.
PRESERVED_DATABASE_COLUMNS = frozenset(
    {
        ("roles", "reject_threshold"),
        ("roles", "scoring_criteria"),
    }
)

# Query-tuned, expression, partial, and operational indexes created explicitly
# by reviewed migrations.  They remain database-owned because reproducing all
# PostgreSQL predicates/operator classes in every ORM model would couple
# runtime model imports to migration implementation details.
MIGRATION_MANAGED_INDEXES = frozenset(
    {
        ("agent_decisions", "ix_agent_decisions_org_status_created"),
        ("agent_decisions", "ix_agent_decisions_org_status_type_created"),
        ("agent_exemplars", "ix_agent_exemplars_retrieval"),
        ("agent_needs_input", "ix_agent_needs_input_org_open"),
        ("agent_needs_input", "ix_agent_needs_input_role_kind_subject"),
        ("agent_needs_input", "ix_agent_needs_input_role_open"),
        ("agent_runs", "ix_agent_runs_org_status"),
        ("agent_runs", "ix_agent_runs_role_started"),
        (
            "anthropic_batch_jobs",
            "ix_anthropic_batch_jobs_known_accepted_recovery",
        ),
        ("anthropic_batch_jobs", "ix_anthropic_batch_jobs_status"),
        ("anthropic_wire_log", "ix_anthropic_wire_log_created"),
        ("anthropic_wire_log", "ix_anthropic_wire_log_request_id"),
        (
            "candidate_application_events",
            "ix_candidate_application_events_org_app_created",
        ),
        ("candidate_applications", "ix_candidate_applications_cv_fts"),
        (
            "candidate_applications",
            "ix_candidate_applications_org_outcome_pre_screen_sort",
        ),
        (
            "candidate_applications",
            "ix_candidate_applications_org_outcome_stage",
        ),
        (
            "candidate_applications",
            "ix_candidate_applications_org_outcome_taali_sort",
        ),
        (
            "candidate_applications",
            "ix_candidate_applications_org_role_outcome_pre_screen_sort",
        ),
        (
            "candidate_applications",
            "ix_candidate_applications_org_role_outcome_stage",
        ),
        (
            "candidate_applications",
            "ix_candidate_applications_org_role_outcome_taali_sort",
        ),
        ("candidates", "ix_candidates_cv_fts"),
        ("candidates", "ix_candidates_search_experience_trgm"),
        ("candidates", "ix_candidates_search_profile_trgm"),
        ("candidates", "ix_candidates_search_skills_trgm"),
        ("chat_command_receipts", "ix_chat_command_receipts_conversation"),
        ("cv_parse_cache", "ix_cv_parse_cache_prompt_version"),
        ("cv_score_cache", "ix_cv_score_cache_prompt_version"),
        ("cv_score_jobs", "ix_cv_score_jobs_status"),
        ("decision_feedback", "ix_decision_feedback_org_created"),
        ("decision_policies", "ix_decision_policies_org_role_active"),
        ("decision_policies", "ix_decision_policies_revision"),
        ("graph_writeback_queue", "ix_graph_writeback_queue_active"),
        ("policy_versions", "ix_policy_versions_active"),
        ("role_feedback_notes", "ix_role_feedback_notes_role_created"),
        ("role_intents", "ix_role_intents_active_lookup"),
        ("rubric_revisions", "ix_rubric_revisions_org_created"),
        ("rubric_revisions", "ix_rubric_revisions_role_id"),
        (
            "taali_chat_conversations",
            "ix_taali_chat_conversations_org_user_recent",
        ),
        ("taali_chat_messages", "ix_taali_chat_messages_conversation_created"),
        (
            "threshold_calibrations",
            "ix_threshold_calibrations_org_role_status",
        ),
        ("threshold_calibrations", "ix_threshold_calibrations_org_status"),
    }
)


def _owning_table_name(obj: Any) -> str | None:
    table = getattr(obj, "table", None)
    name = getattr(table, "name", None)
    return str(name) if name is not None else None


def _is_redundant_primary_key_index(obj: Any) -> bool:
    columns = tuple(getattr(obj, "columns", ()))
    return bool(columns) and all(
        bool(getattr(column, "primary_key", False)) for column in columns
    )


def include_object(
    obj: Any,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    """Return whether Alembic should compare an object.

    Only exact reviewed database-owned objects and redundant metadata-only
    primary-key indexes are excluded.  Unknown objects are deliberately
    returned to Alembic so ``alembic check`` fails closed.
    """

    if reflected and compare_to is None:
        if type_ == "table":
            return name not in PRESERVED_DATABASE_TABLES

        table_name = _owning_table_name(obj)
        key = (table_name, str(name)) if table_name is not None and name else None
        if type_ == "column" and key in PRESERVED_DATABASE_COLUMNS:
            return False
        if type_ == "index" and key in MIGRATION_MANAGED_INDEXES:
            return False

    if (
        type_ == "index"
        and not reflected
        and compare_to is None
        and _is_redundant_primary_key_index(obj)
    ):
        return False

    return True


__all__ = [
    "MIGRATION_MANAGED_INDEXES",
    "PRESERVED_DATABASE_COLUMNS",
    "PRESERVED_DATABASE_TABLES",
    "include_object",
]
