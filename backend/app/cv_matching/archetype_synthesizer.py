"""On-demand archetype synthesis (agentic, single-provider).

The first time a JD arrives, this module makes one Sonnet call to
*synthesize* an ``ArchetypeRubric`` (substitution rules + seniority
anchors + dimension weights) for that role family, persists it to the
``cv_archetypes`` cache, and returns it.

Subsequent calls with the same JD (after normalisation: lowercase,
whitespace collapsed) reuse the cached rubric — no Sonnet call.

Net effect: zero human curation, zero non-Anthropic dependencies.
Cost: one Sonnet call (~$0.05) per *unique* JD. Same JD scored
against many CVs pays the synthesis cost once.

If you later want similarity-based dedup (so two near-identical JDs
share a rubric), see ``backend/app/cv_matching/README.md`` — that's
a documented future enhancement that needs an embedding provider
(Anthropic doesn't ship one).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger("taali.cv_match.archetype_synthesizer")

_GENERATOR_MODEL = "claude-sonnet-4-6"
_GENERATOR_TEMPERATURE = 0.0
_GENERATOR_MAX_TOKENS = 4000


# ---------------------------------------------------------------------------
# ArchetypeRubric pydantic schema
# ---------------------------------------------------------------------------


class MustHaveArchetype(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cluster: str
    description: str
    exact_matches: list[str] = Field(default_factory=list)
    strong_substitutes: list[str] = Field(default_factory=list)
    weak_substitutes: list[str] = Field(default_factory=list)
    unrelated: list[str] = Field(default_factory=list)


class SeniorityAnchors(BaseModel):
    model_config = ConfigDict(extra="forbid")

    band_100: str = ""
    band_75: str = ""
    band_50: str = ""
    band_25: str = ""
    band_0: str = ""


class ArchetypeRubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archetype_id: str
    description: str
    jd_centroid_text: str
    must_have_archetypes: list[MustHaveArchetype]
    seniority_anchors: SeniorityAnchors = Field(default_factory=SeniorityAnchors)
    dimension_weights: dict[str, float] = Field(default_factory=dict)

    def normalised_dimension_weights(self) -> dict[str, float]:
        defaults = {
            "skills_coverage": 0.25,
            "skills_depth": 0.20,
            "title_trajectory": 0.15,
            "seniority_alignment": 0.15,
            "industry_match": 0.15,
            "tenure_pattern": 0.10,
        }
        merged = {**defaults, **self.dimension_weights}
        total = sum(merged.values())
        if total <= 0:
            return defaults
        return {k: v / total for k, v in merged.items()}


# ---------------------------------------------------------------------------
# Cache (in-process LRU + DB)
# ---------------------------------------------------------------------------


@dataclass
class _CachedRubric:
    cache_key: str
    rubric: ArchetypeRubric


_lru: dict[str, ArchetypeRubric] = {}
_LRU_CAPACITY = 256


def _normalise(text: str) -> str:
    """Lowercase + collapse all whitespace runs to single spaces.

    Trade-off: lighter-touch normalisation gives more cache hits but more
    false-positives (two JDs that look similar but aren't actually the
    same role share a rubric). This is the conservative middle ground.
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip().lower())


def _cache_key(jd_text: str) -> str:
    return hashlib.sha256(_normalise(jd_text).encode("utf-8")).hexdigest()


def _archetype_id_from_jd(jd_text: str) -> str:
    """Stable id derived from JD content. Used as the DB row key."""
    return "auto_" + _cache_key(jd_text)[:12]


def reset_cache() -> None:
    """Drop the in-process LRU. Tests use this to isolate runs."""
    _lru.clear()
    _db_loaded.clear()


_db_loaded: set[str] = set()  # sentinel so we only attempt DB load once


def _load_from_db(cache_key: str) -> ArchetypeRubric | None:
    """Pull one cached archetype out of the DB by cache_key. None on any failure."""
    try:
        from ..models.cv_embeddings import CvEmbedding
        from ..platform.database import SessionLocal
    except Exception as exc:
        logger.debug("Archetype DB read skipped (import): %s", exc)
        return None

    try:
        session = SessionLocal()
    except Exception as exc:
        logger.debug("Archetype DB read skipped (session): %s", exc)
        return None

    try:
        row = (
            session.query(CvEmbedding)
            .filter_by(content_hash=cache_key, provider="archetype_rubric")
            .one_or_none()
        )
        if row is None:
            return None
        blob = row.embedding
        if isinstance(blob, dict) and "rubric" in blob:
            return ArchetypeRubric.model_validate(blob["rubric"])
        return None
    except Exception as exc:
        # Catches OperationalError ("no such table") in lightweight test
        # contexts where the cv_embeddings migration hasn't been applied,
        # plus pydantic validation errors on malformed rows.
        logger.debug("Archetype DB read failed: %s", exc)
        return None
    finally:
        try:
            session.close()
        except Exception:  # pragma: no cover — defensive
            pass


def _persist_to_db(cache_key: str, rubric: ArchetypeRubric) -> None:
    """Write a synthesized archetype to the cv_archetypes-shaped table."""
    try:
        from ..models.cv_embeddings import CvEmbedding
        from ..platform.database import SessionLocal
    except Exception as exc:
        logger.debug("Archetype persist skipped (import): %s", exc)
        return

    try:
        session = SessionLocal()
    except Exception as exc:
        logger.debug("Archetype persist skipped (session): %s", exc)
        return

    try:
        existing = (
            session.query(CvEmbedding).filter_by(content_hash=cache_key).one_or_none()
        )
        payload = {"rubric": rubric.model_dump(mode="json")}
        if existing is not None:
            existing.embedding = payload
        else:
            session.add(
                CvEmbedding(
                    content_hash=cache_key,
                    provider="archetype_rubric",
                    model="synthesizer_v1",
                    embedding=payload,
                )
            )
        session.commit()
    except Exception as exc:
        logger.debug("Archetype persist failed: %s", exc)
        try:
            session.rollback()
        except Exception:  # pragma: no cover — defensive
            pass
    finally:
        try:
            session.close()
        except Exception:  # pragma: no cover — defensive
            pass


# ---------------------------------------------------------------------------
# Sonnet synthesis
# ---------------------------------------------------------------------------


_SYNTH_PROMPT = """You are a senior recruiter and engineering hiring manager.

You will read one job description. Your task: produce an ArchetypeRubric
JSON that captures (a) what this role family really evaluates against,
(b) which substitutions a hiring manager would consider equivalent vs
require ramp-up, (c) what seniority signals distinguish the bands.

Constraints:
1. Output ONLY valid JSON matching the schema below. No prose, no markdown
   fences, no commentary.
2. Substitution lists should err toward MORE entries — a downstream system
   uses these to classify match_tier per requirement.
3. Anchored verbal tiers (band_100/75/50/25/0) must describe a CONCRETE
   candidate profile, not abstract quality language. Bad: "very strong
   candidate". Good: "led a multi-team Glue migration, owns tier-1
   pipeline SLA".
4. dimension_weights must sum to ~1.0. If a dimension genuinely doesn't
   matter for this role, weight it at 0.05 (not 0).
5. Pick a snake_case archetype_id that captures the role family
   (e.g. "aws_glue_data_engineer", "genai_engineer",
   "senior_swe_backend"). Do NOT include the company name.

Schema:

{
  "archetype_id": "<snake_case>",
  "description": "<2-3 sentence prose>",
  "jd_centroid_text": "<canonical 4-8 sentence JD prose for this role family>",
  "must_have_archetypes": [
    {
      "cluster": "<snake_case cluster name>",
      "description": "<one sentence>",
      "exact_matches": ["<exact tool/skill names>"],
      "strong_substitutes": ["<closely interchangeable equivalents>"],
      "weak_substitutes": ["<loosely related capabilities>"],
      "unrelated": ["<same broad area but not relevant>"]
    }
  ],
  "seniority_anchors": {
    "band_100": "<prose anchor>",
    "band_75":  "<prose anchor>",
    "band_50":  "<prose anchor>",
    "band_25":  "<prose anchor>",
    "band_0":   "<prose anchor>"
  },
  "dimension_weights": {
    "skills_coverage": <float>,
    "skills_depth": <float>,
    "title_trajectory": <float>,
    "seniority_alignment": <float>,
    "industry_match": <float>,
    "tenure_pattern": <float>
  }
}

Job description:

<JD>
{jd_text}
</JD>
"""


def _synthesize_via_sonnet(jd_text: str, client=None) -> ArchetypeRubric | None:
    """Call Sonnet to synthesize a fresh archetype rubric. Returns None on failure."""
    if client is None:
        try:
            from .runner import _resolve_anthropic_client

            client = _resolve_anthropic_client()
        except Exception as exc:
            logger.warning("Cannot synthesize archetype — no Anthropic client: %s", exc)
            return None

    prompt = _SYNTH_PROMPT.replace("{jd_text}", jd_text or "")
    try:
        response = client.messages.create(
            model=_GENERATOR_MODEL,
            max_tokens=_GENERATOR_MAX_TOKENS,
            temperature=_GENERATOR_TEMPERATURE,
            system="You are a senior recruiter. Output only JSON.",
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        logger.warning("Sonnet archetype synthesis failed: %s", exc)
        return None

    try:
        raw = response.content[0].text  # type: ignore[attr-defined]
    except (AttributeError, IndexError):
        return None

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0]
    try:
        blob = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Synthesizer returned invalid JSON: %s", exc)
        return None

    try:
        rubric = ArchetypeRubric.model_validate(blob)
    except ValidationError as exc:
        logger.warning("Synthesizer output failed schema validation: %s", exc)
        return None

    logger.info("Synthesized new archetype: %s", rubric.archetype_id)
    return rubric


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def synthesize_archetype(
    jd_text: str,
    requirements: Sequence | None = None,
    *,
    client=None,
) -> ArchetypeRubric | None:
    """Return an ArchetypeRubric for this JD.

    1. Look up cache_key = sha256(normalise(jd_text)).
    2. Hit in LRU → return cached rubric.
    3. Hit in DB → load into LRU, return.
    4. Miss → fire Sonnet synthesis, persist, return.

    ``requirements`` is currently ignored (only the JD text drives the
    cache key). Kept on the signature for forward compatibility.

    Returns ``None`` when synthesis fails (e.g. no Anthropic key, model
    error). Caller proceeds without an archetype block.
    """
    if not jd_text or not jd_text.strip():
        return None

    cache_key = _cache_key(jd_text)

    if cache_key in _lru:
        return _lru[cache_key]

    if cache_key not in _db_loaded:
        _db_loaded.add(cache_key)
        from_db = _load_from_db(cache_key)
        if from_db is not None:
            _lru[cache_key] = from_db
            return from_db

    rubric = _synthesize_via_sonnet(jd_text, client=client)
    if rubric is None:
        return None

    archetype_id = rubric.archetype_id or _archetype_id_from_jd(jd_text)
    rubric = rubric.model_copy(update={"archetype_id": archetype_id})

    _lru[cache_key] = rubric
    while len(_lru) > _LRU_CAPACITY:
        # FIFO eviction (Python dict preserves insertion order).
        oldest = next(iter(_lru))
        del _lru[oldest]

    _persist_to_db(cache_key, rubric)
    return rubric


__all__ = [
    "ArchetypeRubric",
    "MustHaveArchetype",
    "SeniorityAnchors",
    "reset_cache",
    "synthesize_archetype",
]
