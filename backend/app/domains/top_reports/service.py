"""Mint + scrub helpers for shareable top-candidate reports."""
from __future__ import annotations

import copy
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ...mcp.urls import _frontend_base
from ...models.top_candidates_report import TopCandidatesReport

REPORT_TTL = timedelta(days=30)
# Candidate fields dropped from the snapshot before it is persisted — a
# shareable, no-auth report should not carry direct contact PII. The recursive
# text scrub also covers contact details repeated inside summaries, notes, or
# cited CV excerpts rather than only the structured candidate fields.
_SCRUB_KEY_MARKERS = frozenset(
    {
        "email",
        "phone",
        "mobile",
        "address",
        "birthdate",
        "dateofbirth",
        "ssn",
        "socialsecurity",
        "passport",
        "nationalid",
        "token",
        "password",
        "secret",
        "credential",
        "authorization",
        "apikey",
        "accesskey",
        "privatekey",
        "signature",
        "cookie",
        "sessionid",
        "ipaddress",
        "taxid",
        "driverlicense",
    }
)
_SCRUB_EXACT_KEYS = frozenset(
    {
        "applicationid",
        "applicationoutcome",
        "atscontext",
        "auth",
        "authentication",
        "autorejectstate",
        "bearer",
        "bullhornstatus",
        "candidateid",
        "createdat",
        "dob",
        "externalstagenormalized",
        "pipelinestage",
        "pipelinestageupdatedat",
        "rescorecandidateids",
        "roleid",
        "session",
        "setcookie",
        "workablestage",
    }
)
_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])",
    re.I,
)
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{5,}\d(?!\w)")
_BEARER_RE = re.compile(r"\bBearer\s+[^\s,;]+", re.I)
_TOKEN_RE = re.compile(
    r"\b(?:sk|rk|pk|api|rpt|ghp|gho|ghu|ghs|github_pat)[-_][A-Za-z0-9_-]{16,}\b",
    re.I,
)
_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
    r"[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?P<label>\b(?:access[_-]?token|refresh[_-]?token|api[_-]?key|"
    r"client[_-]?secret|aws[_-]?secret[_-]?access[_-]?key|private[_-]?key|"
    r"password|signature|authorization|cookie|auth|token)\s*[=:]\s*)"
    r"[^\s&,;]+",
    re.I,
)
_URL_RE = re.compile(r"\b(?:https?|ftp)://[^\s<>\"']+", re.I)


def _compact_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _omit_public_key(key: Any) -> bool:
    """Whether a recursively encountered JSON key is unsafe to publish."""

    compact = _compact_key(key)
    if not compact:
        return False
    # Candidate/recruiter links resolve to authenticated/internal ATS pages and
    # are not capabilities a no-auth report should expose. Treat every URL-like
    # field conservatively, including camelCase variants and nested metadata.
    if compact.endswith(("url", "uri", "link", "href")):
        return True
    if compact in _SCRUB_EXACT_KEYS:
        return True
    return any(marker in compact for marker in _SCRUB_KEY_MARKERS)


def _redact_public_text(value: str) -> str:
    """Remove direct contact details and credential-shaped strings from text."""

    # Drop URLs first. Redacting an email/token inside a URL can introduce a
    # space and accidentally leave the remainder of that URL visible.
    text = _URL_RE.sub("[link redacted]", value)
    text = _EMAIL_RE.sub("[email redacted]", text)

    def _phone(match: re.Match[str]) -> str:
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        # Avoid treating dates/year ranges such as 2026-07-15 or 2018-2024 as
        # phone numbers. International '+' numbers may be shorter; local
        # numbers need at least nine digits to qualify.
        minimum = 7 if raw.lstrip().startswith("+") else 9
        return "[phone redacted]" if minimum <= len(digits) <= 15 else raw

    text = _PHONE_RE.sub(_phone, text)
    text = _BEARER_RE.sub("Bearer [credential redacted]", text)
    text = _TOKEN_RE.sub("[credential redacted]", text)
    text = _JWT_RE.sub("[credential redacted]", text)
    text = _AWS_KEY_RE.sub("[credential redacted]", text)
    text = _CREDENTIAL_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('label')}[credential redacted]", text
    )
    return text


def _scrub_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _scrub_value(item)
            for key, item in value.items()
            if not _omit_public_key(key)
        }
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    if isinstance(value, tuple):
        return [_scrub_value(item) for item in value]
    if isinstance(value, str):
        return _redact_public_text(value)
    return value


def generate_report_token() -> str:
    return f"rpt_{secrets.token_urlsafe(24)}"


def report_public_url(token: str) -> str:
    return f"{_frontend_base()}/report/{token}"


def _scrub(snapshot: dict[str, Any]) -> dict[str, Any]:
    snap = copy.deepcopy(snapshot) if isinstance(snapshot, dict) else {}
    return _scrub_value(snap)


def scrub_public_query(query: str | None) -> str | None:
    """Sanitize a report heading/query, including legacy embedded links."""

    if query is None:
        return None
    return _redact_public_text(str(query))


def scrub_public_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Defense-in-depth scrub for persisted and legacy report snapshots."""

    return _scrub(snapshot or {})


def _ensure_sqlite_outer_transaction(db: Session) -> None:
    """Make SQLite's physical transaction match SQLAlchemy's outer one.

    Python's legacy sqlite3 transaction mode does not emit ``BEGIN`` for a
    SELECT. If the first write is inside a SAVEPOINT, releasing that outermost
    savepoint commits it at the driver level even though the Session still
    believes an outer transaction owns the write. Production Postgres does not
    need this shim; it keeps tests/local SQLite faithful to the same atomicity.
    """

    connection = db.connection()
    if connection.dialect.name != "sqlite":
        return
    proxied = connection.connection
    driver = getattr(proxied, "driver_connection", None)
    if driver is not None and not bool(getattr(driver, "in_transaction", True)):
        connection.exec_driver_sql("BEGIN")


def create_report(
    db: Session,
    *,
    organization_id: int,
    created_by_user_id: int | None,
    role_id: int | None,
    query: str,
    snapshot: dict[str, Any],
) -> TopCandidatesReport:
    """Stage a scrubbed report without owning the caller's transaction.

    The nested transaction contains report-only flush failures (for example a
    rare token collision), keeping the chat session usable. Releasing the
    savepoint is not a session commit: the route/service that owns the outer
    transaction decides whether the report and chat turn persist together, so
    a confirmed action cannot publish a link while leaving its approval reusable.
    """
    report = TopCandidatesReport(
        organization_id=organization_id,
        created_by_user_id=created_by_user_id,
        role_id=role_id,
        token=generate_report_token(),
        query=scrub_public_query(query),
        snapshot=scrub_public_snapshot(snapshot),
        expires_at=datetime.now(timezone.utc) + REPORT_TTL,
    )
    # Ensure SQLite has a real outer transaction so releasing the report-only
    # savepoint cannot become an accidental commit. Session.begin_nested()
    # still performs its normal pre-flush; best-effort callers must explicitly
    # flush their own state before entering their exception boundary.
    _ensure_sqlite_outer_transaction(db)
    with db.begin_nested():
        db.add(report)
        db.flush([report])
    return report
