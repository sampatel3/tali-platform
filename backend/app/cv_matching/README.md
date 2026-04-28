# cv_matching

Single-path CV→JD scoring pipeline. Anthropic-only (no other API providers
required). Iterate by editing the prompt and bumping ``PROMPT_VERSION``.

## Architecture at a glance

```
run_cv_match(cv_text, jd_text, requirements)
    │
    ├─ 1. Cache lookup (sha256 of cv+jd+reqs+prompt_version+model_version)
    │      hit  → return cached output
    │      miss → continue
    │
    ├─ 2. Synthesize archetype rubric for this JD (Sonnet, cached on jd hash)
    │      first time we see this JD → ~$0.05 Sonnet call → persist
    │      next time same JD → instant cache hit → no call
    │
    ├─ 3. Build prompt (UNTRUSTED_CV spotlighting + anchored rubric +
    │     anti-default rule + abstention guidance + archetype context)
    │
    ├─ 4. Call Haiku at temperature=0 (single call, one retry on
    │     validation failure)
    │
    ├─ 5. Parse JSON → schema → ground evidence quotes against CV
    │     (drop hallucinated quotes; downgrade to "unknown" if none survive)
    │     → cross-field consistency check
    │
    ├─ 6. Aggregate (deterministic):
    │     - requirements_match_score: priority × status × match_tier
    │     - cv_fit_score: archetype-weighted six-dimension average
    │     - role_fit_score: 0.4·cv_fit + 0.6·requirements_match
    │     - recommendation: thresholds + constraint/must-have caps
    │
    ├─ 7. Apply calibrator (if a snapshot exists for this archetype):
    │     attach P(advance | recruiter) alongside the raw score
    │
    └─ 8. Cache + telemetry
```

## Why no embeddings

Anthropic doesn't ship an embedding model. To stay single-provider, the
archetype cache uses **exact-match hashing** on the JD text:

```python
cache_key = sha256(normalise(jd_text))   # lowercase, whitespace collapsed
```

Same JD always shares one rubric. Two JDs that differ in any meaningful
character get separate Sonnet syntheses. For a single-user system
iterating on a small set of roles, that's fine.

If you later want **near-duplicate dedup** (two JDs that look 95%
similar share a rubric, saving Sonnet calls), you have three options:

| Option | What you'd do | Cost / week (rough) |
|---|---|---|
| **Voyage** ([voyageai.com](https://voyageai.com)) | Set `EMBEDDING_PROVIDER=voyage` + `VOYAGE_API_KEY`. Wire `embeddings.py` back in. ~$0.02/1M tokens for `voyage-3.5-lite`. Topped MTEB retrieval at the time I wrote this. | <$1 |
| **OpenAI embeddings** | Same shape — `EMBEDDING_PROVIDER=openai` + `OPENAI_API_KEY`. `text-embedding-3-large` runs ~$0.13/1M tokens. Use this if you already have an OpenAI key for other things. | <$5 |
| **Cohere** / Jina / sentence-transformers (self-hosted) | Same shape, different SDK. | Varies |

To re-enable any of those, you'd:
1. Re-add `embeddings.py` (a wrapper module — was deleted in commit
   that introduced this README; recoverable from git history).
2. Re-add the `EMBEDDING_PROVIDER` settings to `platform/config.py`.
3. Switch `archetype_synthesizer.synthesize_archetype` from
   `_cache_key(jd_text) = sha256(normalise(jd_text))` to a centroid-
   embedding cosine lookup against cached archetypes.

The wiring for that is small — a few hours of work — and worth it once
your JD volume crosses ~50 distinct JDs/month.

## Files

| File | Role |
|---|---|
| [`__init__.py`](__init__.py) | `PROMPT_VERSION`, `MODEL_VERSION`, public re-exports |
| [`prompts.py`](prompts.py) | Single canonical prompt + builder |
| [`schemas.py`](schemas.py) | `CVMatchOutput`, `RequirementAssessment`, `DimensionScores`, `MatchTier` |
| [`runner.py`](runner.py) | `run_cv_match()` — linear pipeline |
| [`aggregation.py`](aggregation.py) | Pure scoring math (no LLM, no DB) |
| [`validation.py`](validation.py) | Evidence grounding + consistency + injection scan |
| [`cache.py`](cache.py) | `cv_score_cache` adapter |
| [`telemetry.py`](telemetry.py) | One trace row per call |
| [`routes.py`](routes.py) | `GET /admin/cv-match/traces` + `POST /candidates/{id}/cv-match-override` |
| [`archetype_synthesizer.py`](archetype_synthesizer.py) | On-demand Sonnet rubric synthesis, hash-cached |
| [`pairwise.py`](pairwise.py) | Bradley-Terry tie-break + auto-sampled anchors |
| [`borderline.py`](borderline.py) | CISC self-consistency for borderline scores |
| [`calibrators/`](calibrators) | Platt + isotonic calibrators, judge bootstrap, recalibrator |
| [`evals/`](evals) | Golden-case harness + agreement metrics + autogen baseline diff |

## Iterating on the prompt

```python
# 1. Edit backend/app/cv_matching/prompts.py
# 2. Bump PROMPT_VERSION in backend/app/cv_matching/__init__.py
# 3. Run the eval harness against the golden cases:
cd backend
python -m app.cv_matching.evals.run_evals --no-cache --metrics-full --baseline-md
# 4. Review the autogen markdown report in
#    backend/app/cv_matching/evals/baseline_results/{prompt_version}_{ts}.md
# 5. If the diff looks good, deploy.
```

Bumping `PROMPT_VERSION` invalidates the cache cleanly — every cached
score becomes unreachable, the next score regenerates against the new
prompt. No manual flush needed.

## Costs (back-of-envelope)

Per scored CV against a JD that's already been seen:
- Cache hit on the score → $0
- Cache miss on the score → 1 Haiku call (~$0.0016) + 1 archetype-cache
  hit ($0)

Per scored CV against a JD never seen before:
- 1 Sonnet archetype-synthesis call (~$0.05) + 1 Haiku score call (~$0.0016)
- Synthesis cost amortises across every future CV scored against the
  same JD.

For a single-user platform iterating: **expect ~$0.05 the first time you
score against a new role, ~$0.001-0.002 for every subsequent CV against
that same role.**

## What was intentionally cut

To keep the system "simple AI you iterate on" (per Sam, 2026-04-28):

- **No EU AI Act / NYC LL144 compliance instrumentation** (counterfactual
  probes, impact-ratio dashboard, conformal prediction deferral, drift
  detection). Re-add when the platform has actual users in regulated
  markets.
- **No multi-version prompt dispatch.** One prompt. One path. Bump
  `PROMPT_VERSION` to iterate.
- **No batch API path.** Synchronous Haiku calls only. Re-add when you
  have offline rescoring jobs in volume.
- **No embedding-based pre-filter.** Single-user volume doesn't justify
  the Voyage/OpenAI dependency.
- **No static rubric library.** Replaced by on-demand Sonnet synthesis.
