"""Bounded JSON and secret checks for retained result-delivery evidence."""

from __future__ import annotations

import json
import math
from typing import Any

from fastapi import HTTPException

_MAX_JSON_NODES = 20_000
_MAX_JSON_DEPTH = 32


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def preflight_json(value: Any, *, max_bytes: int, label: str) -> int:
    """Bound a JSON tree before recursion, concatenation, or deepcopy."""

    pending: list[tuple[Any, int]] = [(value, 0)]
    seen_containers: set[int] = set()
    inspected = 0
    while pending:
        current, depth = pending.pop()
        inspected += 1
        if inspected > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
            raise _conflict(
                f"Stored {label} is too large to reconcile safely; "
                "no evidence was overwritten."
            )
        if isinstance(current, dict):
            identity = id(current)
            if identity in seen_containers:
                raise _conflict(
                    f"Stored {label} is malformed; no evidence was overwritten."
                )
            seen_containers.add(identity)
            if not all(isinstance(key, str) for key in current):
                raise _conflict(
                    f"Stored {label} is malformed; no evidence was overwritten."
                )
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            identity = id(current)
            if identity in seen_containers:
                raise _conflict(
                    f"Stored {label} is malformed; no evidence was overwritten."
                )
            seen_containers.add(identity)
            pending.extend((item, depth + 1) for item in current)
        elif current is None or isinstance(current, (str, bool, int)):
            continue
        elif isinstance(current, float) and math.isfinite(current):
            continue
        else:
            raise _conflict(
                f"Stored {label} is malformed; no evidence was overwritten."
            )

    encoded_bytes = 0
    try:
        encoder = json.JSONEncoder(
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        for chunk in encoder.iterencode(value):
            encoded_bytes += len(chunk.encode("utf-8"))
            if encoded_bytes > max_bytes:
                raise _conflict(
                    f"Stored {label} is too large to reconcile safely; "
                    "no evidence was overwritten."
                )
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise _conflict(
            f"Stored {label} is malformed; no evidence was overwritten."
        ) from exc
    return encoded_bytes


def assert_secret_free_receipt_evidence(receipt: dict[str, Any]) -> None:
    """Reject unsafe legacy extras instead of dropping or copying secrets."""

    pending: list[Any] = [receipt]
    inspected = 0
    while pending:
        current = pending.pop()
        inspected += 1
        if inspected > 10_000:
            raise _conflict(
                "Stored result-delivery evidence is too large to reconcile "
                "safely; no evidence was overwritten."
            )
        if isinstance(current, dict):
            for key, value in current.items():
                normalized_key = str(key).strip().lower().replace("-", "_")
                if (
                    normalized_key == "token"
                    or normalized_key.endswith("_token")
                    or "secret" in normalized_key
                    or "authorization" in normalized_key
                    or "password" in normalized_key
                    or "credential" in normalized_key
                    or normalized_key == "api_key"
                    or normalized_key.endswith("_api_key")
                ):
                    raise _conflict(
                        "Stored result-delivery evidence requires support review; "
                        "no evidence was overwritten."
                    )
                pending.append(value)
        elif isinstance(current, list):
            pending.extend(current)


__all__ = [
    "_MAX_JSON_DEPTH",
    "_MAX_JSON_NODES",
    "assert_secret_free_receipt_evidence",
    "preflight_json",
]
