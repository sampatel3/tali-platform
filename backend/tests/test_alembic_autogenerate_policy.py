from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import UniqueConstraint

from app.models import CvEmbedding
from app.models.application_created_outbox import ApplicationCreatedOutbox
from app.models.assessment import Assessment
from app.models.candidate import Candidate
from app.models.organization import Organization
from app.models.taali_chat_conversation import TaaliChatConversation
from app.models.user import User
from app.models.workable_sync_run import WorkableSyncRun
from app.platform.alembic_autogenerate_policy import (
    MIGRATION_MANAGED_INDEXES,
    PRESERVED_DATABASE_COLUMNS,
    PRESERVED_DATABASE_TABLES,
    include_object,
)
from app.platform.database import Base


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    BACKEND_ROOT / "alembic/versions/179_restore_schema_metadata_invariants.py"
)


def _schema_object(table: str, *, primary_key: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        table=SimpleNamespace(name=table),
        columns=(SimpleNamespace(primary_key=primary_key),),
    )


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("migration_179_contract", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_autogenerate_excludes_only_exact_reviewed_database_objects() -> None:
    assert PRESERVED_DATABASE_TABLES == frozenset({"assessment_sessions"})
    assert PRESERVED_DATABASE_COLUMNS == frozenset(
        {("roles", "reject_threshold"), ("roles", "scoring_criteria")}
    )
    assert len(MIGRATION_MANAGED_INDEXES) == 40

    assert not include_object(
        SimpleNamespace(), "assessment_sessions", "table", True, None
    )
    assert not include_object(
        _schema_object("roles"), "scoring_criteria", "column", True, None
    )
    assert not include_object(
        _schema_object("candidates"),
        "ix_candidates_cv_fts",
        "index",
        True,
        None,
    )


def test_autogenerate_fails_closed_for_unknown_destructive_suggestions() -> None:
    assert include_object(SimpleNamespace(), "unknown_table", "table", True, None)
    assert include_object(
        _schema_object("roles"), "unknown_column", "column", True, None
    )
    assert include_object(
        _schema_object("roles"), "ix_unknown", "index", True, None
    )
    assert include_object(
        _schema_object("roles"),
        "ix_candidates_cv_fts",
        "index",
        True,
        None,
    )


def test_autogenerate_ignores_only_redundant_metadata_primary_key_indexes() -> None:
    assert not include_object(
        _schema_object("new_table", primary_key=True),
        "ix_new_table_id",
        "index",
        False,
        None,
    )
    assert include_object(
        _schema_object("new_table"),
        "ix_new_table_business_key",
        "index",
        False,
        None,
    )


def test_every_database_managed_index_is_owned_by_a_migration() -> None:
    migration_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (BACKEND_ROOT / "alembic/versions").glob("*.py")
    )
    missing = sorted(
        name for _table, name in MIGRATION_MANAGED_INDEXES if name not in migration_source
    )
    assert missing == []


def test_live_embedding_model_is_in_the_canonical_registry() -> None:
    assert Base.metadata.tables["cv_embeddings"] is CvEmbedding.__table__
    assert {index.name for index in CvEmbedding.__table__.indexes} == {
        "ix_cv_embeddings_provider_model"
    }


def test_model_metadata_matches_existing_database_invariants() -> None:
    not_nullable = (
        Assessment.__table__.c.completed_due_to_timeout,
        Candidate.__table__.c.marketing_consent,
        Candidate.__table__.c.workable_enriched,
        Organization.__table__.c.billing_provider,
        Organization.__table__.c.credits_balance,
        Organization.__table__.c.sso_enforced,
        Organization.__table__.c.saml_enabled,
        WorkableSyncRun.__table__.c.created_at,
    )
    assert all(not column.nullable for column in not_nullable)
    assert User.__table__.c.created_at.nullable

    unique_constraints = {
        constraint.name
        for constraint in ApplicationCreatedOutbox.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "application_created_outbox_application_id_key" in unique_constraints

    role_fk = next(iter(TaaliChatConversation.__table__.c.role_id.foreign_keys))
    assert role_fk.constraint.name == "fk_taali_chat_conversations_role_id_roles"
    assert role_fk.ondelete == "SET NULL"


class _ScalarResult:
    def __init__(self, value: int):
        self.value = value

    def scalar_one(self) -> int:
        return self.value


class _MigrationRecorder:
    def __init__(self, orphan_count: int = 0):
        self.orphan_count = orphan_count
        self.events: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.preflight_sql = ""

    def get_bind(self) -> _MigrationRecorder:
        return self

    def execute(self, statement: Any) -> _ScalarResult | None:
        sql = str(statement)
        if "SELECT COUNT(*)" in sql:
            self.preflight_sql = sql
            return _ScalarResult(self.orphan_count)
        self.events.append(("execute", (sql,), {}))
        return None

    def alter_column(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("alter_column", args, kwargs))

    def create_foreign_key(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("create_foreign_key", args, kwargs))

    def drop_constraint(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("drop_constraint", args, kwargs))


def test_migration_179_preserves_rows_and_hardens_only_null_auth_booleans() -> None:
    migration = _load_migration()
    recorder = _MigrationRecorder()
    migration.op = recorder

    migration.upgrade()

    assert "child.superseded_id IS NOT NULL" in recorder.preflight_sql
    writes = [args[0] for kind, args, _kwargs in recorder.events if kind == "execute"]
    assert writes == [
        "UPDATE users SET is_active = false WHERE is_active IS NULL",
        "UPDATE users SET is_superuser = false WHERE is_superuser IS NULL",
    ]
    assert all("DELETE" not in statement.upper() for statement in writes)
    altered = [
        (args[0], args[1], kwargs["nullable"])
        for kind, args, kwargs in recorder.events
        if kind == "alter_column"
    ]
    assert altered == [
        ("users", "is_active", False),
        ("users", "is_superuser", False),
    ]
    fk_events = [event for event in recorder.events if event[0] == "create_foreign_key"]
    assert len(fk_events) == 1
    assert fk_events[0][1][:3] == (
        "fk_role_intents_superseded_id",
        "role_intents",
        "role_intents",
    )


def test_migration_179_fails_before_writes_when_role_intents_are_dangling(
    capsys: pytest.CaptureFixture[str],
) -> None:
    migration = _load_migration()
    recorder = _MigrationRecorder(orphan_count=3)
    migration.op = recorder

    with pytest.raises(RuntimeError, match="found 3 dangling"):
        migration.upgrade()

    assert recorder.events == []
    assert "found 3 dangling role_intents.superseded_id" in capsys.readouterr().err


def test_migration_179_never_deletes_rows_columns_tables_or_indexes() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "DELETE" not in source.upper()
    assert "drop_table" not in source
    assert "drop_column" not in source
    assert "drop_index" not in source

    migration = _load_migration()
    recorder = _MigrationRecorder()
    migration.op = recorder
    migration.downgrade()
    assert [event[0] for event in recorder.events] == [
        "drop_constraint",
        "alter_column",
        "alter_column",
    ]
    assert all(
        kwargs.get("nullable") is True
        for kind, _args, kwargs in recorder.events
        if kind == "alter_column"
    )
