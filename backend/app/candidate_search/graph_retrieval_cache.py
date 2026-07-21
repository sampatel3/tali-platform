"""Small process-local cache for paid Graphiti retrieval results.

The cache stores raw typed results so backend status, cap state, and episode
provenance survive unchanged. Keys include every scope value that affects a
Graphiti search; a result can never be reused across organizations or roles.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from ..candidate_graph.search import GraphEvidenceSearchResult

DEFAULT_GRAPH_CACHE_MAX_ENTRIES = 256
DEFAULT_GRAPH_CACHE_TTL_SECONDS = 60.0
MAX_SEARCH_QUERY_LENGTH = 500


def validate_search_query(value: object, *, field_name: str = "query") -> str:
    """Normalize bounded search text before parsing or paid retrieval."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    text = value.strip()
    if len(text) > MAX_SEARCH_QUERY_LENGTH:
        raise ValueError(
            f"{field_name} must be at most {MAX_SEARCH_QUERY_LENGTH} characters"
        )
    return text


@dataclass(frozen=True, slots=True)
class GraphRetrievalCacheKey:
    organization_id: int
    role_id: int | None
    query: str
    limit: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.organization_id, bool)
            or not isinstance(self.organization_id, int)
            or self.organization_id <= 0
        ):
            raise ValueError("organization_id must be a positive integer")
        if self.role_id is not None and (
            isinstance(self.role_id, bool)
            or not isinstance(self.role_id, int)
            or self.role_id <= 0
        ):
            raise ValueError("role_id must be a positive integer when provided")
        if not isinstance(self.query, str) or not self.query:
            raise ValueError("query must be non-empty")
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or self.limit <= 0
        ):
            raise ValueError("limit must be a positive integer")


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    value: GraphEvidenceSearchResult
    expires_at: float


class GraphRetrievalCache:
    """Thread-safe TTL/LRU cache with single-flight loading per scoped key."""

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_GRAPH_CACHE_MAX_ENTRIES,
        ttl_seconds: float = DEFAULT_GRAPH_CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            isinstance(max_entries, bool)
            or not isinstance(max_entries, int)
            or max_entries < 1
        ):
            raise ValueError("max_entries must be a positive integer")
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not math.isfinite(float(ttl_seconds))
            or ttl_seconds <= 0
        ):
            raise ValueError("ttl_seconds must be finite and positive")
        self._max_entries = max_entries
        self._ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._entries: OrderedDict[GraphRetrievalCacheKey, _CacheEntry] = OrderedDict()
        self._inflight: dict[GraphRetrievalCacheKey, threading.Event] = {}
        self._lock = threading.Lock()

    def get_or_load(
        self,
        key: GraphRetrievalCacheKey,
        loader: Callable[[], GraphEvidenceSearchResult],
    ) -> GraphEvidenceSearchResult:
        """Return a fresh entry or load once while same-key callers wait."""

        while True:
            now = float(self._clock())
            with self._lock:
                self._prune_expired(now)
                entry = self._entries.get(key)
                if entry is not None:
                    self._entries.move_to_end(key)
                    return entry.value
                pending = self._inflight.get(key)
                if pending is None:
                    pending = threading.Event()
                    self._inflight[key] = pending
                    break
            pending.wait()

        try:
            value = loader()
            if not isinstance(value, GraphEvidenceSearchResult):
                raise TypeError("graph cache loader must return GraphEvidenceSearchResult")
        except BaseException:
            self._finish_load(key)
            raise

        try:
            with self._lock:
                self._entries[key] = _CacheEntry(
                    value=value,
                    expires_at=float(self._clock()) + self._ttl_seconds,
                )
                self._entries.move_to_end(key)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
        finally:
            self._finish_load(key)
        return value

    @property
    def size(self) -> int:
        with self._lock:
            self._prune_expired(float(self._clock()))
            return len(self._entries)

    def clear(self) -> None:
        """Drop completed entries without disrupting active loaders."""

        with self._lock:
            self._entries.clear()

    def _finish_load(self, key: GraphRetrievalCacheKey) -> None:
        with self._lock:
            pending = self._inflight.pop(key, None)
            if pending is not None:
                pending.set()

    def _prune_expired(self, now: float) -> None:
        expired = [
            key for key, entry in self._entries.items() if entry.expires_at <= now
        ]
        for key in expired:
            self._entries.pop(key, None)


graph_retrieval_cache = GraphRetrievalCache()


__all__ = [
    "GraphRetrievalCache",
    "GraphRetrievalCacheKey",
    "graph_retrieval_cache",
    "MAX_SEARCH_QUERY_LENGTH",
    "validate_search_query",
]
