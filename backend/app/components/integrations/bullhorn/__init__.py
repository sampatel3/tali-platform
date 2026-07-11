"""Bullhorn ATS integration client (staging-only until flag-off).

Public surface:
* :class:`BullhornService` — typed REST client (reads, writes, files, events).
* :class:`BullhornAuth` — discovery + OAuth + REST-session lifecycle, holding the
  single-use refresh-token rotation invariant.
* Typed errors: :class:`BullhornError`, :class:`BullhornAuthError`,
  :class:`BullhornRateLimitError`, :class:`BullhornApiError`.
"""

from __future__ import annotations

from .auth import BullhornAuth
from .errors import (
    BullhornApiError,
    BullhornAuthError,
    BullhornError,
    BullhornRateLimitError,
)
from .service import BullhornService

__all__ = [
    "BullhornAuth",
    "BullhornService",
    "BullhornError",
    "BullhornAuthError",
    "BullhornRateLimitError",
    "BullhornApiError",
]
