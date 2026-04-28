"""Per-organization rate limit for natural-language search.

Keeps NL queries from blowing up Anthropic spend if a recruiter (or a
runaway frontend) hammers the search box. Uses an in-process sliding
window — fine for single-worker Railway deploys; for multi-worker we'd
move this to Redis.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

# 60 NL queries per 60 seconds per organization. Tuned for "human typing"
# rates — well below the cost per query (≤$0.05) × cap = $3/min/org.
WINDOW_SEC = 60
MAX_PER_WINDOW = 60


_lock = threading.Lock()
_buckets: "defaultdict[int, deque[float]]" = defaultdict(deque)


def check_and_record(organization_id: int) -> bool:
    """Return True if the request is allowed; record it and prune the window.

    Always records when allowed. Test code can call ``reset`` to flush state.
    """
    if not organization_id:
        return True
    now = time.time()
    cutoff = now - WINDOW_SEC
    with _lock:
        bucket = _buckets[int(organization_id)]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= MAX_PER_WINDOW:
            return False
        bucket.append(now)
        return True


def reset() -> None:
    """Test helper: empty all org buckets."""
    with _lock:
        _buckets.clear()
