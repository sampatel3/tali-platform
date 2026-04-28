"""Per-role-family archetype rubrics.

Each YAML file in this directory describes one role archetype the
matcher knows about (e.g. ``aws_glue_data_engineer``,
``genai_engineer``, ``senior_swe_backend``). At runtime the v4.2
pipeline:

1. Embeds the JD via ``embeddings.embed_jd``.
2. Cosine-matches against the archetype centroid embedding to pick
   the best-fitting rubric (or no rubric, in which case the generic
   v4.1 prompt is used).
3. Injects the archetype's substitution rules and seniority anchors
   into the prompt so per-requirement ``match_tier`` classification
   is grounded in role-family-specific equivalences (e.g. FastAPI ↔
   Django REST is ``strong_substitute`` for an archetype that names
   FastAPI, ``weak_substitute`` for one that names "Python web
   framework" generically).

Schema: see ``schema.py`` and ``README.md``.

Generation: rubrics are generated *once* offline by a Sonnet call
against 3-5 anonymised JDs from the role family. They are not a
runtime dependency on Sonnet — runtime reads the cached YAML.
"""

from .schema import (
    ArchetypeRubric,
    DepthSignals,
    MustHaveArchetype,
    SeniorityAnchors,
    list_rubrics,
    load_rubric,
)

__all__ = [
    "ArchetypeRubric",
    "DepthSignals",
    "MustHaveArchetype",
    "SeniorityAnchors",
    "list_rubrics",
    "load_rubric",
]
