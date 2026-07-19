"""Canonical secret-safe identities for paid provider requests."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def provider_request_sha256(request: dict[str, Any]) -> str:
    """Hash one exact JSON provider request without persisting its content."""

    if type(request) is not dict:
        raise ValueError("provider request identity requires an object")
    try:
        canonical = json.dumps(
            request,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("provider request must be canonical JSON") from exc
    return hashlib.sha256(canonical).hexdigest()


__all__ = ["provider_request_sha256"]
