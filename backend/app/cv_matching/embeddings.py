"""Embedding wrapper for Phase 2 pre-filter and archetype routing.

Public surface:

    embed_cv(text) -> list[float]
    embed_jd(text, requirements) -> list[float]
    cosine_similarity(a, b) -> float

Pluggable providers, selected via ``settings.EMBEDDING_PROVIDER``:

- ``voyage``   — Voyage-3.5-lite via the ``voyageai`` SDK (production default)
- ``openai``   — text-embedding-3-large via the ``openai`` SDK
- ``mock``     — deterministic hash-based vectors for tests / local dev

The caller never imports a provider directly — always go through
``embed_cv`` / ``embed_jd`` so the provider can be swapped centrally.

Cache: keyed on ``sha256(text + provider + model)``. Persists to the
``cv_embeddings`` table when the DB is wired (Phase 2.2); falls back to
an in-process LRU when the DB is unavailable (tests / lightweight
contexts).

Vectors are emitted as plain ``list[float]`` to keep the module
numpy-free. The pre-filter does linear-time cosine on small batches
(~hundreds of CVs), so list arithmetic is fast enough. If batch sizes
grow into the tens of thousands, swap the math layer to numpy at that
point — no caller will need to change.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    from .schemas import RequirementInput

logger = logging.getLogger("taali.cv_match.embeddings")

# Cache layer — bounded LRU. Each entry is keyed by sha256 of normalized
# (text, provider, model) and stored as a list[float]. The DB cache (Phase
# 2.2) supplements this; we read DB first, then fall back to LRU.
_LRU_CAPACITY = 1024
_lru: "OrderedDict[str, list[float]]" = OrderedDict()


def _content_hash(text: str, provider: str, model: str) -> str:
    payload = json.dumps(
        {"text": text or "", "provider": provider, "model": model},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _lru_get(key: str) -> list[float] | None:
    if key not in _lru:
        return None
    value = _lru.pop(key)
    _lru[key] = value  # mark most-recently-used
    return list(value)


def _lru_set(key: str, value: list[float]) -> None:
    if key in _lru:
        _lru.pop(key)
    _lru[key] = list(value)
    while len(_lru) > _LRU_CAPACITY:
        _lru.popitem(last=False)


# ----------------------------------------------------------------------------
# Providers
# ----------------------------------------------------------------------------


def _resolve_provider() -> tuple[str, str]:
    """Return (provider_name, model_name) per settings, with safe defaults.

    Phase 2 default is ``voyage`` + ``voyage-3.5-lite``. Tests typically
    set ``EMBEDDING_PROVIDER=mock`` so they never hit the network.
    """
    try:
        from ..platform.config import settings

        provider = getattr(settings, "EMBEDDING_PROVIDER", "voyage") or "voyage"
        model = getattr(settings, "EMBEDDING_MODEL", "")
    except Exception:
        provider, model = "mock", ""

    if not model:
        model = {
            "voyage": "voyage-3.5-lite",
            "openai": "text-embedding-3-large",
            "mock": "mock-embed-v1",
        }.get(provider, "mock-embed-v1")
    return provider, model


def _voyage_embed(text: str, model: str) -> list[float]:
    try:
        import voyageai
    except ImportError as exc:  # pragma: no cover — depends on env
        raise RuntimeError(
            "voyageai not installed; pip install voyageai or set "
            "EMBEDDING_PROVIDER=mock for local dev"
        ) from exc

    from ..platform.config import settings

    api_key = getattr(settings, "VOYAGE_API_KEY", "")
    if not api_key:
        raise RuntimeError("VOYAGE_API_KEY not configured")
    client = voyageai.Client(api_key=api_key)
    result = client.embed([text or ""], model=model, input_type="document")
    vec = result.embeddings[0]
    return [float(x) for x in vec]


def _openai_embed(text: str, model: str) -> list[float]:
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover — depends on env
        raise RuntimeError(
            "openai not installed; pip install openai or set "
            "EMBEDDING_PROVIDER=mock for local dev"
        ) from exc

    from ..platform.config import settings

    api_key = getattr(settings, "OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not configured")
    client = OpenAI(api_key=api_key)
    resp = client.embeddings.create(input=text or "", model=model)
    return [float(x) for x in resp.data[0].embedding]


def _mock_embed(text: str, model: str, *, dim: int = 64) -> list[float]:
    """Deterministic hash-based vector. Same text → same vector across runs.

    Used for tests and for offline dev where calling a real embedding
    provider is undesirable. The vector is L2-normalised so cosine
    similarities are sensible.
    """
    h = hashlib.sha256((text or "").encode("utf-8")).digest()
    # Expand the hash by chained sha256 if we need more dims than 32 bytes.
    raw_bytes = bytearray(h)
    while len(raw_bytes) < dim * 2:
        raw_bytes.extend(hashlib.sha256(bytes(raw_bytes)).digest())
    # Pack two bytes per dim into [-1, 1] approx, then normalise.
    vec: list[float] = []
    for i in range(dim):
        b0 = raw_bytes[2 * i]
        b1 = raw_bytes[2 * i + 1]
        v = ((b0 << 8) | b1) / 65535.0  # [0, 1]
        vec.append(v * 2.0 - 1.0)  # [-1, 1]
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _persist_to_db(content_hash: str, provider: str, model: str, vec: list[float]) -> None:
    """Write the embedding to ``cv_embeddings`` if the DB layer is wired.

    Failures here are logged at DEBUG and swallowed: the embedding is still
    available in the LRU, and the next call will hit the LRU.
    """
    try:
        from ..models.cv_embeddings import CvEmbedding
        from ..platform.database import SessionLocal
    except Exception as exc:
        logger.debug("Embedding DB persist skipped: %s", exc)
        return

    session = SessionLocal()
    try:
        existing = (
            session.query(CvEmbedding).filter_by(content_hash=content_hash).one_or_none()
        )
        if existing is not None:
            return
        row = CvEmbedding(
            content_hash=content_hash,
            provider=provider,
            model=model,
            embedding=vec,
        )
        session.add(row)
        session.commit()
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("Embedding DB write failed: %s", exc)
        session.rollback()
    finally:
        session.close()


def _read_from_db(content_hash: str) -> list[float] | None:
    try:
        from ..models.cv_embeddings import CvEmbedding
        from ..platform.database import SessionLocal
    except Exception as exc:
        logger.debug("Embedding DB read skipped: %s", exc)
        return None

    session = SessionLocal()
    try:
        row = (
            session.query(CvEmbedding).filter_by(content_hash=content_hash).one_or_none()
        )
        if row is None:
            return None
        vec = row.embedding
        if isinstance(vec, list):
            return [float(x) for x in vec]
        return None
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("Embedding DB read failed: %s", exc)
        return None
    finally:
        session.close()


# ----------------------------------------------------------------------------
# Public surface
# ----------------------------------------------------------------------------


def _embed(text: str) -> list[float]:
    """Internal: embed any string via the configured provider, with caching."""
    provider, model = _resolve_provider()
    key = _content_hash(text or "", provider, model)

    cached = _lru_get(key)
    if cached is not None:
        return cached

    db_cached = _read_from_db(key)
    if db_cached is not None:
        _lru_set(key, db_cached)
        return db_cached

    if provider == "voyage":
        vec = _voyage_embed(text or "", model)
    elif provider == "openai":
        vec = _openai_embed(text or "", model)
    elif provider == "mock":
        vec = _mock_embed(text or "", model)
    else:
        raise ValueError(f"Unknown EMBEDDING_PROVIDER: {provider!r}")

    _lru_set(key, vec)
    _persist_to_db(key, provider, model, vec)
    return vec


def embed_cv(cv_text: str) -> list[float]:
    """Embed a CV. The whole text is sent — provider handles truncation.

    Returns a list of floats (L2-normalised on the mock provider; provider-
    dependent on real backends — Voyage-3 returns unit-norm vectors out of
    the box).
    """
    return _embed(cv_text)


def embed_jd(
    jd_text: str, requirements: "Sequence[RequirementInput] | None" = None
) -> list[float]:
    """Embed a JD plus its recruiter requirements.

    Concatenates the JD with the recruiter requirements (priority + text)
    so the embedding captures *what the recruiter actually cares about*,
    not just the public-facing JD prose.
    """
    parts = [jd_text or ""]
    for r in requirements or []:
        prio = getattr(r.priority, "value", str(r.priority))
        parts.append(f"[{prio}] {r.requirement}")
        if r.evidence_hints:
            parts.append("hints: " + ", ".join(r.evidence_hints))
        if r.acceptable_alternatives:
            parts.append("equiv: " + ", ".join(r.acceptable_alternatives))
    blob = "\n".join(parts)
    return _embed(blob)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine of the angle between two vectors. Range [-1, 1].

    Returns 0.0 when either vector has zero norm.
    """
    if len(a) != len(b):
        raise ValueError(
            f"vector dims differ: {len(a)} vs {len(b)} — likely a provider mix"
        )
    dot = math.fsum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(math.fsum(x * x for x in a))
    norm_b = math.sqrt(math.fsum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def clear_cache() -> None:
    """Drop the in-process LRU. Tests use this to isolate runs."""
    _lru.clear()


__all__ = [
    "embed_cv",
    "embed_jd",
    "cosine_similarity",
    "clear_cache",
]
