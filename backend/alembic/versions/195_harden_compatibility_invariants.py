"""Harden compatibility evidence and SQLite history retention.

Revision ID: 195_compatibility_invariant_hardening
Revises: 194_scoring_recovery_index
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
import json
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

from app.scripts.database_schema_validation import MigrationValidationError


revision = "195_compatibility_invariant_hardening"
down_revision = "194_scoring_recovery_index"
branch_labels = None
depends_on = None

_SUPPORTED_DIALECTS = frozenset({"postgresql", "sqlite"})
_WORKSPACE_PAUSE_EVIDENCE_ERROR_CODE = "workspace_pause_exact_evidence_invalid"
_SQLITE_RELATED_HISTORY_GUARD_ERROR_CODE = "sqlite_related_history_guard_invalid"
_WORKSPACE_PAUSE_AUDIT_REVISION = "182_workspace_pause_compat_audit"
_WORKSPACE_PAUSE_EVIDENCE_SOURCE = "published_175_role_events"
_PUBLISHED_EVENT_REASON = "workspace pause migrated to role bulk control"
_PUBLISHED_ROLE_PAUSE_REASON = "paused by workspace control"
_EVENT_ID_BATCH_SIZE = 500

_SQLITE_RELATED_HISTORY_TRIGGER_SQL = {
    "preserve_owner_role_related_history_v195": """
        CREATE TRIGGER preserve_owner_role_related_history_v195
        BEFORE DELETE ON roles
        FOR EACH ROW
        WHEN EXISTS (
            SELECT 1 FROM roles AS related
            WHERE related.ats_owner_role_id = OLD.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'owner role has preserved related-role history');
        END
    """,
    "preserve_related_role_evaluations_v195": """
        CREATE TRIGGER preserve_related_role_evaluations_v195
        BEFORE DELETE ON roles
        FOR EACH ROW
        WHEN EXISTS (
            SELECT 1 FROM sister_role_evaluations AS evaluation
            WHERE evaluation.role_id = OLD.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'related role has preserved evaluation history');
        END
    """,
}


def _normalized_sql(value: object) -> str:
    return " ".join(str(value or "").strip().rstrip(";").lower().split())


def _fail(code: str) -> None:
    raise MigrationValidationError(
        f"Compatibility invariant validation failed (code={code}); manual "
        "data-policy review is required before migration can continue."
    )


def _stored_json(value: object, *, sqlite_storage: bool) -> Any | None:
    if not sqlite_storage:
        return value
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _integer_list(value: object, *, sqlite_storage: bool) -> list[int] | None:
    decoded = _stored_json(value, sqlite_storage=sqlite_storage)
    if not isinstance(decoded, list) or any(type(item) is not int for item in decoded):
        return None
    return decoded


def _event_id_batches(values: Iterable[int]) -> Iterator[list[int]]:
    """Yield deterministic batches below even legacy SQLite bind limits."""

    ordered = sorted(values)
    for offset in range(0, len(ordered), _EVENT_ID_BATCH_SIZE):
        yield ordered[offset : offset + _EVENT_ID_BATCH_SIZE]


def _canonical_published_pause_changes(
    value: object,
    *,
    sqlite_storage: bool,
) -> bool:
    changes = _stored_json(value, sqlite_storage=sqlite_storage)
    if not isinstance(changes, dict) or set(changes) != {
        "agent_paused_at",
        "agent_paused_reason",
    }:
        return False
    paused_at = changes["agent_paused_at"]
    pause_reason = changes["agent_paused_reason"]
    if (
        not isinstance(paused_at, dict)
        or set(paused_at) != {"before", "after"}
        or paused_at["before"] is not None
        or not isinstance(pause_reason, dict)
        or set(pause_reason) != {"before", "after"}
        or pause_reason["before"] is not None
        or pause_reason["after"] != _PUBLISHED_ROLE_PAUSE_REASON
    ):
        return False
    timestamp = paused_at["after"]
    if not isinstance(timestamp, str) or "T" not in timestamp or not timestamp.strip():
        return False
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _validate_workspace_pause_exact_evidence(
    connection: sa.engine.Connection,
) -> None:
    sqlite_storage = connection.dialect.name == "sqlite"
    audits = (
        connection.execute(
            sa.text(
                """
            SELECT
                organization_id,
                evidence_quality,
                converted_role_count,
                source_role_event_ids,
                source_role_ids,
                source_workspace_event_id,
                recorded_workspace_event_id,
                compatibility_applied,
                control_version_before,
                control_version_after
            FROM workspace_pause_migration_audits
            WHERE migration_revision = :migration_revision
              AND evidence_source = :evidence_source
            ORDER BY id
            """
            ),
            {
                "migration_revision": _WORKSPACE_PAUSE_AUDIT_REVISION,
                "evidence_source": _WORKSPACE_PAUSE_EVIDENCE_SOURCE,
            },
        )
        .mappings()
        .all()
    )
    expected_role_events: dict[int, tuple[int, int]] = {}
    expected_workspace_events: dict[int, tuple[int, int, int]] = {}
    for audit in audits:
        event_ids = _integer_list(
            audit["source_role_event_ids"],
            sqlite_storage=sqlite_storage,
        )
        role_ids = _integer_list(
            audit["source_role_ids"],
            sqlite_storage=sqlite_storage,
        )
        converted_role_count = audit["converted_role_count"]
        compatibility_value = audit["compatibility_applied"]
        valid_compatibility_value = bool(
            type(compatibility_value) is bool
            or (type(compatibility_value) is int and compatibility_value in {0, 1})
        )
        compatibility_applied = bool(compatibility_value)
        before_version = audit["control_version_before"]
        after_version = audit["control_version_after"]
        recorded_event_id = audit["recorded_workspace_event_id"]
        if (
            audit["evidence_quality"] != "exact"
            or audit["source_workspace_event_id"] is not None
            or type(converted_role_count) is not int
            or event_ids is None
            or role_ids is None
            or not event_ids
            or len(event_ids) != len(role_ids)
            or len(event_ids) != converted_role_count
            or len(set(event_ids)) != len(event_ids)
            or len(set(role_ids)) != len(role_ids)
            or not valid_compatibility_value
            or type(before_version) is not int
            or type(after_version) is not int
            or before_version < 1
            or (
                compatibility_applied
                and (
                    type(recorded_event_id) is not int
                    or after_version != before_version + 1
                )
            )
            or (
                not compatibility_applied
                and (recorded_event_id is not None or after_version != before_version)
            )
        ):
            _fail(_WORKSPACE_PAUSE_EVIDENCE_ERROR_CODE)

        for event_id, role_id in zip(event_ids, role_ids, strict=True):
            if event_id in expected_role_events:
                _fail(_WORKSPACE_PAUSE_EVIDENCE_ERROR_CODE)
            expected_role_events[event_id] = (audit["organization_id"], role_id)
        if compatibility_applied:
            assert type(recorded_event_id) is int
            if recorded_event_id in expected_workspace_events:
                _fail(_WORKSPACE_PAUSE_EVIDENCE_ERROR_CODE)
            expected_workspace_events[recorded_event_id] = (
                audit["organization_id"],
                before_version,
                after_version,
            )

    if expected_role_events:
        statement = sa.text(
            """
            SELECT
                id,
                organization_id,
                role_id,
                action,
                from_version,
                to_version,
                changes,
                reason,
                request_id
            FROM role_change_events
            WHERE id IN :event_ids
            """
        ).bindparams(sa.bindparam("event_ids", expanding=True))
        for event_id_batch in _event_id_batches(expected_role_events):
            role_events = (
                connection.execute(
                    statement,
                    {"event_ids": event_id_batch},
                )
                .mappings()
                .all()
            )
            if len(role_events) != len(event_id_batch):
                _fail(_WORKSPACE_PAUSE_EVIDENCE_ERROR_CODE)
            for event in role_events:
                expected_organization_id, expected_role_id = expected_role_events[
                    int(event["id"])
                ]
                from_version = event["from_version"]
                to_version = event["to_version"]
                if (
                    type(from_version) is not int
                    or type(to_version) is not int
                    or from_version < 1
                    or to_version != from_version + 1
                    or event["organization_id"] != expected_organization_id
                    or event["role_id"] != expected_role_id
                    or event["action"] != "agent_paused"
                    or event["reason"] != _PUBLISHED_EVENT_REASON
                    or event["request_id"] is not None
                    or not _canonical_published_pause_changes(
                        event["changes"],
                        sqlite_storage=sqlite_storage,
                    )
                ):
                    _fail(_WORKSPACE_PAUSE_EVIDENCE_ERROR_CODE)

    if expected_workspace_events:
        statement = sa.text(
            """
            SELECT id, organization_id, action, from_version, to_version, request_id
            FROM workspace_agent_control_events
            WHERE id IN :event_ids
            """
        ).bindparams(sa.bindparam("event_ids", expanding=True))
        for event_id_batch in _event_id_batches(expected_workspace_events):
            workspace_events = (
                connection.execute(
                    statement,
                    {"event_ids": event_id_batch},
                )
                .mappings()
                .all()
            )
            if len(workspace_events) != len(event_id_batch):
                _fail(_WORKSPACE_PAUSE_EVIDENCE_ERROR_CODE)
            for event in workspace_events:
                organization_id, before_version, after_version = (
                    expected_workspace_events[int(event["id"])]
                )
                if (
                    event["organization_id"] != organization_id
                    or event["action"] != "migrated"
                    or event["from_version"] != before_version
                    or event["to_version"] != after_version
                    or event["request_id"]
                    != f"migration:{_WORKSPACE_PAUSE_AUDIT_REVISION}:{organization_id}"
                ):
                    _fail(_WORKSPACE_PAUSE_EVIDENCE_ERROR_CODE)


def _install_sqlite_related_history_guards() -> None:
    context = op.get_context()
    if context.as_sql:
        for definition in _SQLITE_RELATED_HISTORY_TRIGGER_SQL.values():
            op.execute(
                definition.replace(
                    "CREATE TRIGGER",
                    "CREATE TRIGGER IF NOT EXISTS",
                    1,
                )
            )
        return

    connection = op.get_bind()
    for name, definition in _SQLITE_RELATED_HISTORY_TRIGGER_SQL.items():
        existing = connection.execute(
            sa.text(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = :name"
            ),
            {"name": name},
        ).scalar_one_or_none()
        if existing is None:
            op.execute(definition)
            existing = connection.execute(
                sa.text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'trigger' AND name = :name"
                ),
                {"name": name},
            ).scalar_one_or_none()
        if _normalized_sql(existing) != _normalized_sql(definition):
            _fail(_SQLITE_RELATED_HISTORY_GUARD_ERROR_CODE)


def upgrade() -> None:
    connection = op.get_bind()
    dialect = str(connection.dialect.name)
    if dialect not in _SUPPORTED_DIALECTS:
        raise RuntimeError(
            "Revision 195 supports only PostgreSQL and SQLite; refusing to "
            f"harden compatibility invariants on {dialect!r}."
        )
    _validate_workspace_pause_exact_evidence(connection)
    if dialect == "sqlite":
        _install_sqlite_related_history_guards()


def downgrade() -> None:
    raise RuntimeError(
        "Revision 195 is intentionally irreversible: downgrading would remove "
        "additive related-history protection or bypass evidence validation."
    )
