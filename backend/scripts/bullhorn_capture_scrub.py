"""Fail-closed pseudonymisation for recorded Bullhorn fixtures."""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from typing import Any

from app.components.integrations.bullhorn.event_handlers import (
    MUTATION_EVENT_TYPES,
    SUBSCRIBED_ENTITIES,
)

_TOKEN_KEYS = {
    "bhresttoken",
    "resttoken",
    "access_token",
    "refresh_token",
    "client_secret",
    "password",
    "corptoken",
    "sessionkey",
}
_TOKEN_CANONICAL_KEYS = {re.sub(r"[^a-z0-9]", "", item) for item in _TOKEN_KEYS}
_NAME_KEYS = {"firstname", "lastname", "name"}
_EMAIL_KEYS = {"email"}
_PHONE_KEYS = {"phone", "mobile"}
_FIRST_NAMES = ("Ada", "Grace", "Alan", "Katherine", "Linus", "Radia", "Barbara", "Dennis")
_LAST_NAMES = ("Lovelace", "Hopper", "Turing", "Johnson", "Torvalds", "Perlman", "Liskov", "Ritchie")
_SAFE_GENERATED_VALUE = re.compile(
    r"(?:REDACTED|text-[0-9a-f]{12}|category-[0-9a-f]{12}|id-[0-9a-f]{12}|"
    r"candidate-[0-9a-f]{10}@example\.com|\+10000000000|"
    r"2000-01-01T00:00:00Z)"
)
_CATEGORICAL_KEYS = {
    "action",
    "changetype",
    "contenttype",
    "employmenttype",
    "entity",
    "entityeventtype",
    "entityname",
    "eventtype",
    "status",
    "type",
}
_SAFE_FIXTURE_KEYS = {
    "Candidate", "JobOrder", "JobSubmission", "Note", "categorization",
    "confirmedJobResponseStatus", "data", "events",
    "interviewScheduledJobResponseStatus", "rejectedJobResponseStatus",
    "sessionExpires", "statuses", "action", "address", "address1", "address2",
    "candidate", "candidateId", "categories", "city", "clientCorporation",
    "comments", "commentingPerson", "contentType", "countryID", "countryName",
    "createdAt", "dateAdded", "dateLastModified", "description", "email",
    "employmentType", "firstName", "id", "isDeleted", "isOpen", "jobOrder",
    "lastName", "latitude", "longitude", "mobile", "modifyingUser", "name",
    "occupation", "phone", "publicDescription", "salary", "state", "status",
    "title", "type", "zip", "createdOn", "entityEventType", "entityId",
    "entityName", "eventMetadata", "eventId", "eventID", "eventTimestamp",
    "eventType", "jmsSelector", "lastRequestId", "requestId", "subscriptionId",
    "updatedProperties", "PERSON_ID", "TRANSACTION_ID", "BhRestToken",
    "access_token", "clientSecret", "client_secret", "corpToken", "password",
    "refresh_token", "restToken", "sessionKey",
}
_SAFE_UPDATED_PROPERTIES = {
    "action", "address", "candidate", "categories", "clientCorporation",
    "comments", "dateAdded", "dateLastModified", "description", "email",
    "employmentType", "firstName", "isDeleted", "isOpen", "jobOrder",
    "lastName", "mobile", "name", "occupation", "phone", "publicDescription",
    "status", "title",
}
_SAFE_ENTITLEMENT_VERBS = frozenset({"GET", "POST", "PUT", "DELETE"})


class _SafeInt(int):
    """Numeric scrub output carrying provenance until JSON serialization."""


class _SafeFloat(float):
    """Numeric scrub output carrying provenance until JSON serialization."""


class _SafeProtocolString(str):
    """Reviewed non-client enum/field name safe to retain verbatim."""


def _digest(scrub_key: bytes, value: object) -> str:
    return hmac.new(scrub_key, str(value).encode(), hashlib.sha256).hexdigest()


def _fake_name(seed: str, *, scrub_key: bytes) -> str:
    digest = int(_digest(scrub_key, seed), 16)
    return (
        f"{_FIRST_NAMES[digest % len(_FIRST_NAMES)]} "
        f"{_LAST_NAMES[(digest // 7) % len(_LAST_NAMES)]}"
    )


def _stable_alias(prefix: str, value: object, *, scrub_key: bytes) -> str:
    return f"{prefix}-{_digest(scrub_key, value)[:12]}"


def _stable_number(value: int | float, *, key: str | None, scrub_key: bytes) -> int | float:
    canonical_key = re.sub(r"[^a-z0-9]", "", (key or "").lower())
    pseudonym = int(_digest(scrub_key, f"{canonical_key}:{value}")[:12], 16)
    if canonical_key in {"latitude", "lat"}:
        if isinstance(value, int):
            return _SafeInt(pseudonym % 179 - 89)
        return _SafeFloat((pseudonym % 1_780_001) / 10_000 - 89)
    if canonical_key in {"longitude", "lon", "lng"}:
        if isinstance(value, int):
            return _SafeInt(pseudonym % 359 - 179)
        return _SafeFloat((pseudonym % 3_580_001) / 10_000 - 179)
    if canonical_key in {"salary", "salaryamount", "compensation"}:
        salary = 40_000 + (pseudonym % 33) * 5_000
        return _SafeInt(salary) if isinstance(value, int) else _SafeFloat(salary)
    if isinstance(value, int):
        return _SafeInt(pseudonym % 9_000_000 + 1_000_000)
    return _SafeFloat((pseudonym % 9_000_000 + 1_000_000) / 1_000)


def _is_identifier_key(key: str | None) -> bool:
    return bool(key and (key == "id" or key.endswith(("Id", "ID", "_id"))))


def _is_date_key(key: str | None) -> bool:
    return bool(
        key
        and (
            key.startswith(("date", "Date"))
            or key.endswith(("Date", "At", "_at"))
            or key.lower() == "createdon"
            or "timestamp" in key.lower()
        )
    )


def _categorical_namespace(canonical_key: str) -> str | None:
    if canonical_key == "statuses" or canonical_key.endswith("status"):
        return "status"
    return canonical_key if canonical_key in _CATEGORICAL_KEYS else None


def _record_seed(record: Mapping[str, object], fallback: str) -> str:
    for field in ("id", "email", "name"):
        candidate = record.get(field)
        if candidate not in (None, ""):
            return f"{field}:{candidate}"
    return fallback


def _preserve_protocol_string(canonical_key: str, value: str) -> str | None:
    normalized = value.strip()
    upper = normalized.upper()
    if canonical_key == "entityname":
        entity_names = {name.lower(): name for name in SUBSCRIBED_ENTITIES}
        canonical_entity = entity_names.get(normalized.lower())
        if canonical_entity is None:
            raise ValueError("unexpected Bullhorn entityName protocol value")
        return _SafeProtocolString(canonical_entity)
    if canonical_key == "eventtype":
        if upper != "ENTITY" and upper not in MUTATION_EVENT_TYPES:
            raise ValueError("unexpected Bullhorn eventType protocol value")
        return _SafeProtocolString(upper)
    if canonical_key == "entityeventtype":
        if upper not in MUTATION_EVENT_TYPES:
            raise ValueError("unexpected Bullhorn entityEventType protocol value")
        return _SafeProtocolString(upper)
    if canonical_key == "type" and upper == "ENTITY":
        return _SafeProtocolString("entity")
    if canonical_key == "updatedproperties" and value in _SAFE_UPDATED_PROPERTIES:
        return _SafeProtocolString(value)
    if canonical_key in {"candidate", "joborder", "jobsubmission", "note"}:
        if upper in _SAFE_ENTITLEMENT_VERBS:
            return _SafeProtocolString(upper)
    return None


def scrub(
    value: Any,
    *,
    scrub_key: bytes,
    key: str | None = None,
    seed: str = "x",
) -> Any:
    """Recursively pseudonymize identifiers and redact every free-text value."""
    lkey = (key or "").lower()
    canonical_key = re.sub(r"[^a-z0-9]", "", lkey)
    if value is None or isinstance(value, bool):
        return value
    if value == "":
        return ""
    if canonical_key in _TOKEN_CANONICAL_KEYS:
        return "REDACTED"
    if _is_identifier_key(key) and isinstance(value, (int, str)):
        alias = _stable_alias("id", value, scrub_key=scrub_key)
        if isinstance(value, int):
            return _SafeInt(int(alias.removeprefix("id-"), 16) % 9_000_000 + 1_000_000)
        return alias
    if _is_date_key(key):
        if isinstance(value, (int, float)):
            return _SafeInt(946684800000)
        return "2000-01-01T00:00:00Z"
    if isinstance(value, dict):
        record_seed = _record_seed(value, seed)
        return {
            nested_key: scrub(
                nested_value,
                scrub_key=scrub_key,
                key=nested_key,
                seed=record_seed,
            )
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [scrub(item, scrub_key=scrub_key, key=key, seed=seed) for item in value]
    if isinstance(value, str):
        preserved = _preserve_protocol_string(canonical_key, value)
        if preserved is not None:
            return preserved
    if lkey in _EMAIL_KEYS and isinstance(value, str) and value:
        return f"candidate-{_digest(scrub_key, seed + value)[:10]}@example.com"
    if lkey in _PHONE_KEYS and value not in (None, ""):
        return "+10000000000"
    if lkey in _NAME_KEYS and isinstance(value, str) and value:
        full = _fake_name(seed, scrub_key=scrub_key)
        if lkey == "firstname":
            return full.split(" ")[0]
        if lkey == "lastname":
            return full.split(" ")[-1]
        return full
    category = _categorical_namespace(canonical_key)
    if category is not None and isinstance(value, str):
        return _stable_alias("category", f"{category}:{value}", scrub_key=scrub_key)
    if isinstance(value, str):
        return _stable_alias("text", f"{key}:{seed}:{value}", scrub_key=scrub_key)
    if isinstance(value, (int, float)):
        return _stable_number(value, key=key, scrub_key=scrub_key)
    return "REDACTED"


def assert_scrubbed_safe(value: Any, *, path: str = "payload") -> None:
    """Fail closed unless every emitted string came from a reviewed generator."""
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str) or key not in _SAFE_FIXTURE_KEYS:
                raise ValueError(f"unsafe fixture key at {path}")
            assert_scrubbed_safe(nested, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            assert_scrubbed_safe(nested, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        if value == "" or isinstance(value, _SafeProtocolString):
            return
        fake_names = set(_FIRST_NAMES) | set(_LAST_NAMES) | {
            f"{first} {last}" for first in _FIRST_NAMES for last in _LAST_NAMES
        }
        if not (_SAFE_GENERATED_VALUE.fullmatch(value) or value in fake_names):
            raise ValueError(f"unsafe fixture string at {path}")
        return
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, (_SafeInt, _SafeFloat)):
        return
    if isinstance(value, (int, float)):
        raise ValueError(f"unsafe fixture number at {path}")
    raise ValueError(f"unsafe fixture value at {path}")
