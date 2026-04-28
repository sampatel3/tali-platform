"""In-process LRU cache for parsed filters.

Caches the (org_id, query) → ``ParsedFilter`` mapping for ``CACHE_TTL``
seconds. We deliberately do NOT cache the resulting candidate id list:
that set churns (new candidates land, scores update, stages move) and
stale ids would surface ghost rows.

Redis would be a better fit for multi-process deployments. The current
production posture is single-worker per Railway service, so a process-
local LRU is sufficient. Graduate to Redis when worker count > 1.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Optional

from . import PROMPT_VERSION
from .schemas import ParsedFilter

logger = logging.getLogger("taali.candidate_search.cache")

CACHE_TTL_SECONDS = 24 * 60 * 60
CACHE_MAX_ENTRIES = 1024


_lock = threading.Lock()
# OrderedDict gives us O(1) insertion and O(1) move-to-end for LRU.
# Values are (expires_at_epoch, payload_dict).
_store: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()


def compute_cache_key(*, organization_id: int, query: str) -> str:
    """Stable SHA256 over (org_id, normalised query, prompt version)."""
    payload = {
        "org": int(organization_id),
        "q": (query or "").strip().lower(),
        "v": PROMPT_VERSION,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def get(cache_key: str) -> Optional[ParsedFilter]:
    now = time.time()
    with _lock:
        entry = _store.get(cache_key)
        if entry is None:
            return None
        expires_at, payload = entry
        if now >= expires_at:
            _store.pop(cache_key, None)
            return None
        _store.move_to_end(cache_key)
    try:
        return ParsedFilter.model_validate(payload)
    except Exception as exc:
        # Schema drift after a deploy that changed the model: drop the entry.
        logger.warning("Parser cache hit failed validation: %s", exc)
        with _lock:
            _store.pop(cache_key, None)
        return None


def set(cache_key: str, parsed: ParsedFilter) -> None:
    expires_at = time.time() + CACHE_TTL_SECONDS
    payload = parsed.model_dump(mode="json")
    with _lock:
        _store[cache_key] = (expires_at, payload)
        _store.move_to_end(cache_key)
        while len(_store) > CACHE_MAX_ENTRIES:
            _store.popitem(last=False)


def clear() -> None:
    """Test helper: empty the cache."""
    with _lock:
        _store.clear()
