"""Request/response schemas for the Bullhorn sync domain routes.

Split out of ``routes.py`` to keep each module under the 500-LOC gate. Mirrors
the shape of the inline Pydantic models in ``workable_sync.routes``.

SECURITY: no schema here ever echoes ``client_secret``, ``password``, or the
``refresh_token`` — the connect body ACCEPTS the secret + password (one-time,
in-memory) but route responses never carry them, the API username, or the
corp-token-bearing REST URL back to a caller.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ConnectRequest(BaseModel):
    """One-time Bullhorn connect payload.

    ``password`` and ``client_secret`` are used in-memory ONLY for the automated
    OAuth exchange at connect time. The password is never persisted or logged;
    the client secret is stored as Fernet ciphertext (never echoed).
    """

    username: str = Field(..., min_length=1, max_length=200)
    client_id: str = Field(..., min_length=1, max_length=200)
    client_secret: str = Field(..., min_length=1, max_length=400)
    password: str = Field(..., min_length=1, max_length=400)


class StageMapRow(BaseModel):
    """One remote-status → Taali-stage mapping row (list + replace payloads)."""

    remote_status: str = Field(..., min_length=1, max_length=400)
    taali_stage: str = Field(..., min_length=1, max_length=100)
    is_reject: bool = False


class StageMapReplaceRequest(BaseModel):
    """Replace ALL of this org's Bullhorn stage-map rows with ``mappings``."""

    mappings: list[StageMapRow] = Field(default_factory=list)


class SyncRequest(BaseModel):
    """Trigger a Bullhorn full sync. ``mode`` mirrors Workable's metadata/full."""

    mode: Literal["metadata", "full"] = "full"


class SyncCancelRequest(BaseModel):
    """Optional body for POST /sync/cancel (parity with Workable)."""

    # Bullhorn has no per-run table — cancellation is keyed off the org's live
    # progress marker, so there's nothing to pass. Kept for request-shape parity.
    confirm: Optional[bool] = None
