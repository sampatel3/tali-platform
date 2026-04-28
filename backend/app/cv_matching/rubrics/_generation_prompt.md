# Sonnet rubric-generation prompt

This is the offline prompt that produces a starter
`{archetype_id}.yaml` from 3-5 anonymised JDs in a role family.
**Run once per archetype, manually review the YAML, then commit.**
This is NOT a runtime dependency on Sonnet — runtime reads cached
YAMLs only.

## Why Sonnet, not Haiku

Rubric synthesis is a one-shot, multi-document distillation task.
Haiku 4.5 produces shallower substitution lists and softer seniority
anchors than Sonnet 4.6 in head-to-head pilot tests (informal). Cost
is negligible (a few cents per archetype × ~10 archetypes total).

## How to run

```python
# backend/app/cv_matching/rubrics/_generate.py (run from backend/)
from app.cv_matching.rubrics import _generate
_generate.generate_archetype(
    archetype_id="genai_engineer",
    anonymised_jd_paths=[
        "tools/sample_jds/genai_engineer_1.txt",
        "tools/sample_jds/genai_engineer_2.txt",
        "tools/sample_jds/genai_engineer_3.txt",
    ],
    out_path="backend/app/cv_matching/rubrics/genai_engineer.yaml",
)
```

The script enforces:
- Sonnet 4.6 only (cost discipline plus quality reasoning)
- Output validates against `ArchetypeRubric` before write
- Refuses to overwrite an existing YAML (manual delete first)

## The prompt

```
You are a senior recruiter and engineering hiring manager.

I will give you {N} anonymised job descriptions for the same role
family. Your job is to produce a single ArchetypeRubric YAML that
captures (a) what the role family really evaluates against, (b)
which substitutions a hiring manager would consider equivalent vs
require ramp-up, and (c) what seniority signals distinguish the
bands.

Constraints:
1. Output ONLY valid YAML matching the ArchetypeRubric schema below.
   No prose, no commentary, no markdown fences.
2. Substitution lists should err toward MORE entries — a human
   reviewer will prune. It is easier to remove a wrong substitute
   than to invent a missing one.
3. Anchored verbal tiers (band_100/75/50/25/0) must describe a
   concrete candidate profile, not abstract quality language. Bad:
   "very strong candidate". Good: "led a multi-team Glue migration,
   owns tier-1 pipeline SLA".
4. dimension_weights must sum to 1.0 within rounding error. If a
   dimension genuinely doesn't matter for this archetype, weight it
   at 0.05 (not 0) so calibration still has signal.

Schema (Pydantic, see backend/app/cv_matching/rubrics/schema.py for
the source of truth):

archetype_id: <snake_case>
description: <2-3 sentence prose>
jd_centroid_text: <canonical JD prose, 4-8 sentences — used for
                   cosine routing>
must_have_archetypes:
  - cluster: <snake_case cluster name>
    description: <one sentence>
    exact_matches: [<exact tool/skill names>]
    strong_substitutes: [<closely interchangeable equivalents>]
    weak_substitutes: [<loosely related capabilities>]
    unrelated: [<same broad area but not relevant>]
depth_signals:
  skills_coverage: [<concrete depth phrase>]
  skills_depth: [...]
  title_trajectory: [...]
  seniority_alignment: [...]
  industry_match: [...]
  tenure_pattern: [...]
seniority_anchors:
  band_100: |
    <prose anchor>
  band_75: |
    <prose anchor>
  band_50: |
    <prose anchor>
  band_25: |
    <prose anchor>
  band_0: |
    <prose anchor>
dimension_weights:
  skills_coverage: <float>
  skills_depth: <float>
  title_trajectory: <float>
  seniority_alignment: <float>
  industry_match: <float>
  tenure_pattern: <float>

Anonymised JDs follow:

<JD_1>
{jd_1_text}
</JD_1>

<JD_2>
{jd_2_text}
</JD_2>

<JD_3>
{jd_3_text}
</JD_3>
```

## Manual review checklist

Before committing the generated YAML, Sam reviews:

- [ ] `must_have_archetypes` clusters match how a hiring manager
      would group requirements (not how the JD literally lists them).
- [ ] `strong_substitutes` are *truly* interchangeable in a hiring
      decision (e.g. FastAPI ↔ Flask: yes; FastAPI ↔ Django: yes;
      FastAPI ↔ Express.js: marginal).
- [ ] `seniority_anchors` band_75 reads like a "hire at this level
      with confidence" candidate, band_50 like "borderline, advance
      only if pipeline is shallow".
- [ ] `dimension_weights` sum to 1.0.
- [ ] `archetype_id` matches the filename (loader assumes this).
