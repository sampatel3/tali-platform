"""On-demand archetype synthesis (agentic replacement for the static rubric library).

The first time a JD arrives that doesn't cosine-match any cached archetype,
this module makes one Sonnet call to *synthesize* an ``ArchetypeRubric``
(substitution rules + seniority anchors + dimension weights) for that role
family, persists it to the ``cv_archetypes`` cache table, and returns it.

Subsequent JDs whose embeddings cosine-match a cached centroid above the
threshold reuse the cached rubric — no Sonnet call.

Net effect: zero human curation, self-extending library. Cost: one Sonnet
call (~$0.05) per truly novel role family. Hit rate approaches 100%
within a few weeks of operation.

Public surface:

    synthesize_archetype(jd_text, requirements=None) -> ArchetypeRubric | None

Returns ``None`` when synthesis fails (model error, key missing, etc.) so
the caller can render the prompt without an archetype block.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .embeddings import cosine_similarity, embed_jd

logger = logging.getLogger("taali.cv_match.archetype_synthesizer")

# Cosine floor for "this cached archetype matches well enough to reuse".
# Below this, we synthesize a fresh one.
DEFAULT_REUSE_THRESHOLD = 0.78

_GENERATOR_MODEL = "claude-sonnet-4-6"
_GENERATOR_TEMPERATURE = 0.0
_GENERATOR_MAX_TOKENS = 4000


# ---------------------------------------------------------------------------
# ArchetypeRubric pydantic schema (was previously in rubrics/schema.py)
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
# In-process LRU + DB-backed cache
# ---------------------------------------------------------------------------


@dataclass
class _CachedRubric:
    archetype_id: str
    centroid: list[float]
    rubric: ArchetypeRubric


_lru: list[_CachedRubric] = []
_LRU_CAPACITY = 256


def _archetype_id_from_jd(jd_text: str) -> str:
    """Stable id derived from JD content. Used as both filename and cache key."""
    return "auto_" + hashlib.sha256((jd_text or "").encode("utf-8")).hexdigest()[:12]


def _read_cache_from_db() -> list[_CachedRubric]:
    """Pull every cached archetype out of the DB into the LRU. Empty on failure."""
    try:
        from ..models.cv_embeddings import CvEmbedding
        from ..platform.database import SessionLocal
    except Exception as exc:
        logger.debug("Archetype cache DB read skipped: %s", exc)
        return []

    out: list[_CachedRubric] = []
    session = SessionLocal()
    try:
        rows = (
            session.query(CvEmbedding)
            .filter(CvEmbedding.provider == "archetype_rubric")
            .all()
        )
        for row in rows:
            try:
                blob = row.embedding  # we hijack this column to store the rubric JSON
                if isinstance(blob, dict) and "rubric" in blob and "centroid" in blob:
                    rubric = ArchetypeRubric.model_validate(blob["rubric"])
                    centroid = [float(x) for x in blob["centroid"]]
                    out.append(
                        _CachedRubric(
                            archetype_id=rubric.archetype_id,
                            centroid=centroid,
                            rubric=rubric,
                        )
                    )
            except (ValidationError, TypeError, KeyError) as exc:
                logger.debug("Skipping malformed archetype row %s: %s", row.content_hash, exc)
                continue
        return out
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Archetype cache DB read failed: %s", exc)
        return []
    finally:
        session.close()


def _persist_to_db(archetype_id: str, centroid: list[float], rubric: ArchetypeRubric) -> None:
    """Write a synthesized archetype to the cv_embeddings table.

    We reuse the existing table to avoid another migration. The
    ``provider`` column distinguishes "archetype_rubric" rows from real
    embedding rows; ``embedding`` carries the full rubric JSON
    (centroid + rubric).
    """
    try:
        from ..models.cv_embeddings import CvEmbedding
        from ..platform.database import SessionLocal
    except Exception as exc:
        logger.debug("Archetype persist skipped (no DB): %s", exc)
        return

    session = SessionLocal()
    try:
        existing = (
            session.query(CvEmbedding)
            .filter_by(content_hash=archetype_id)
            .one_or_none()
        )
        payload = {
            "centroid": list(centroid),
            "rubric": rubric.model_dump(mode="json"),
        }
        if existing is not None:
            existing.embedding = payload
        else:
            session.add(
                CvEmbedding(
                    content_hash=archetype_id,
                    provider="archetype_rubric",
                    model="synthesizer_v1",
                    embedding=payload,
                )
            )
        session.commit()
    except Exception as exc:
        logger.warning("Archetype persist failed: %s", exc)
        session.rollback()
    finally:
        session.close()


def _ensure_lru_loaded() -> None:
    if _lru:
        return
    cached = _read_cache_from_db()
    if cached:
        _lru.extend(cached[-_LRU_CAPACITY:])


def reset_cache() -> None:
    """Drop the in-process LRU. Tests use this to isolate runs."""
    _lru.clear()


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
    reuse_threshold: float = DEFAULT_REUSE_THRESHOLD,
    client=None,
) -> ArchetypeRubric | None:
    """Return an ArchetypeRubric for this JD.

    1. Embed the JD.
    2. Cosine-match against every cached archetype's centroid.
    3. If best match >= reuse_threshold, return that cached rubric.
    4. Otherwise, fire one Sonnet call to synthesize a fresh rubric,
       persist it to the cache, return it.

    Returns ``None`` when synthesis fails (e.g. no Anthropic key, model
    error). Caller proceeds without an archetype block.
    """
    if not jd_text or not jd_text.strip():
        return None

    _ensure_lru_loaded()

    try:
        jd_vec = embed_jd(jd_text, list(requirements or []))
    except Exception as exc:
        logger.warning("JD embedding failed; skipping archetype routing: %s", exc)
        return None

    best: _CachedRubric | None = None
    best_sim = -1.0
    for entry in _lru:
        try:
            sim = cosine_similarity(jd_vec, entry.centroid)
        except ValueError:
            # Provider mix → dim mismatch. Skip stale entries.
            continue
        if sim > best_sim:
            best = entry
            best_sim = sim

    if best is not None and best_sim >= reuse_threshold:
        logger.info(
            "Archetype cache hit: %s (cosine=%.4f)",
            best.archetype_id,
            best_sim,
        )
        return best.rubric

    rubric = _synthesize_via_sonnet(jd_text, client=client)
    if rubric is None:
        return None

    archetype_id = rubric.archetype_id or _archetype_id_from_jd(jd_text)
    rubric = rubric.model_copy(update={"archetype_id": archetype_id})

    centroid = jd_vec
    cached = _CachedRubric(archetype_id=archetype_id, centroid=centroid, rubric=rubric)
    _lru.append(cached)
    while len(_lru) > _LRU_CAPACITY:
        _lru.pop(0)
    _persist_to_db(archetype_id, centroid, rubric)
    return rubric


__all__ = [
    "ArchetypeRubric",
    "MustHaveArchetype",
    "SeniorityAnchors",
    "DEFAULT_REUSE_THRESHOLD",
    "reset_cache",
    "synthesize_archetype",
]
