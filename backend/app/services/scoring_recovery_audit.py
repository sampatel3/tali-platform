"""Portable JSON predicates and durable fairness markers for scoring recovery."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, case, func, or_

from ..models.background_job_run import BackgroundJobRun


def _json_type(db, key: str):
    if db.get_bind().dialect.name == "postgresql":
        return func.json_typeof(BackgroundJobRun.counters[key])
    return func.json_type(BackgroundJobRun.counters, f"$.{key}")


def _json_value(db, key: str):
    if db.get_bind().dialect.name == "postgresql":
        return BackgroundJobRun.counters[key].as_string()
    return func.json_extract(BackgroundJobRun.counters, f"$.{key}")


def json_boolean_equals(db, key: str, expected: bool):
    """Match an exact JSON boolean without unsafe database casts."""

    kind = _json_type(db, key)
    value = _json_value(db, key)
    if db.get_bind().dialect.name == "postgresql":
        return and_(kind == "boolean", value == ("true" if expected else "false"))
    return and_(kind == ("true" if expected else "false"), value == int(expected))


def json_boolean_false_or_missing(db, key: str):
    """Match a missing/null marker or the exact JSON boolean false."""

    kind = _json_type(db, key)
    return or_(
        kind.is_(None),
        kind == "null",
        json_boolean_equals(db, key, False),
    )


def json_not_boolean_true(db, key: str):
    """Match every value except the exact JSON boolean true."""

    kind = _json_type(db, key)
    return or_(kind.is_(None), kind == "null", ~json_boolean_equals(db, key, True))


def json_integer_equals(db, key: str, expected: int):
    """Match an exact JSON integer without casting corrupt strings."""

    kind = _json_type(db, key)
    value = _json_value(db, key)
    if db.get_bind().dialect.name == "postgresql":
        return and_(kind == "number", value == str(expected))
    return and_(kind == "integer", value == expected)


def json_key_exists(db, key: str):
    """Match an exact top-level key, including one whose JSON value is null."""

    return _json_type(db, key).isnot(None)


def recovery_audit_order(db, key: str, *, current: str):
    """Order unaudited/corrupt rows first, then rotate oldest audits forward."""

    kind = _json_type(db, key)
    value = _json_value(db, key)
    expected_kind = "string" if db.get_bind().dialect.name == "postgresql" else "text"
    urgent = or_(
        kind.is_(None),
        kind != expected_kind,
        value > current,
    )
    return (
        case((urgent, 0), else_=1),
        value.asc(),
        BackgroundJobRun.id.asc(),
    )


def recovery_audit_due(db, key: str, *, current: str, stale_before: str):
    """Select new/corrupt audit markers or rows whose prior audit has expired."""

    kind = _json_type(db, key)
    value = _json_value(db, key)
    expected_kind = "string" if db.get_bind().dialect.name == "postgresql" else "text"
    return or_(
        kind.is_(None),
        kind != expected_kind,
        value > current,
        value <= stale_before,
    )


def mark_recovery_audited(
    run: BackgroundJobRun,
    key: str,
    *,
    now: datetime,
) -> None:
    counters = dict(run.counters) if isinstance(run.counters, dict) else {}
    counters[key] = now.isoformat()
    run.counters = counters


__all__ = [
    "json_boolean_equals",
    "json_boolean_false_or_missing",
    "json_integer_equals",
    "json_key_exists",
    "json_not_boolean_true",
    "mark_recovery_audited",
    "recovery_audit_due",
    "recovery_audit_order",
]
