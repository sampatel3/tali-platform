# Calibration, Aggregation, and Recommendation Logic

This document defines the deterministic logic that sits on top of the LLM output. The LLM only produces `skills_match_score`, `experience_relevance_score`, and per-requirement assessments. Everything else is computed in code from those inputs.

## Why deterministic aggregation

LLMs are inconsistent at multi-factor weighted arithmetic. Splitting the work — LLM does extraction and per-item judgment, code does the math — produces stable, auditable scores and reduces token output (lower cost).

---

## Score calibration ladder (LLM-facing)

The LLM is instructed to use this ladder for `skills_match_score` and `experience_relevance_score`:

| Range | Meaning |
|---|---|
| 90-100 | All must-haves met with strong verbatim evidence, most preferences met, at least one standout signal |
| 80-89 | All must-haves met with clear evidence, several preferences met, no standout |
| 70-79 | All must-haves met but evidence thin, OR one strong preference clearly missing |
| 60-69 | One must-have only partially met or weakly evidenced |
| 40-59 | One must-have missing OR multiple partially met |
| 20-39 | Multiple must-haves missing or unsupported |
| 0-19 | Fundamental misfit or thin CV |

---

## Aggregation formulas (code-side)

### `compute_requirements_match_score(assessments) -> float`

Weighted average across requirements, with priority and status weights.

**Priority weights:**
- `must_have`: 0.70
- `strong_preference`: 0.25
- `nice_to_have`: 0.05
- `constraint`: handled separately (see floors below)

**Status weights (multiplier on the priority weight):**
- `met`: 1.0
- `partially_met`: 0.5
- `unknown`: 0.3
- `missing`: 0.0

**Calculation:**
1. Sum the priority weights of all non-constraint requirements → `total_weight`
2. Sum `(priority_weight × status_weight)` for each non-constraint requirement → `earned_weight`
3. Base score = `(earned_weight / total_weight) × 100`
4. Apply floors:
   - If any `constraint` with `disqualifying_if_missing=True` has status in {missing, unknown}: floor at 30
   - If any `must_have` with `disqualifying_if_missing=True` has status in {missing, unknown}: floor at 40
   - Floors are caps from above on the score, not minimums (i.e., score becomes `min(score, 30)` etc.)

**Edge cases:**
- If no non-constraint requirements exist, return 50 (neutral)
- If `total_weight` is 0, return 50

### `compute_cv_fit(skills_match_score, experience_relevance_score) -> float`

Simple average of the two LLM-produced scores:

```
cv_fit = (skills_match_score + experience_relevance_score) / 2
```

### `compute_role_fit(cv_fit, requirements_match) -> float`

Per `TAALI_SCORING_RUBRIC.md`:

```
role_fit = 0.40 * cv_fit + 0.60 * requirements_match
```

### `derive_recommendation(role_fit, has_failed_constraint, has_missing_must_have) -> Recommendation`

Hard rules first, then score thresholds:

1. If `has_failed_constraint` is True: return `NO`
2. If `has_missing_must_have` is True: return at most `LEAN_NO` (i.e., never `YES` or `STRONG_YES`)
3. Otherwise:
   - `role_fit >= 85`: `STRONG_YES`
   - `role_fit >= 70`: `YES`
   - `role_fit >= 50`: `LEAN_NO`
   - `role_fit < 50`: `NO`

Where:
- `has_failed_constraint`: any `constraint` requirement with `disqualifying_if_missing=True` and status in {missing, unknown}
- `has_missing_must_have`: any `must_have` requirement with status in {missing, unknown}

---

## Worked example

Recruiter requirements:
- `req_1` must_have, disqualifying — "5+ years AWS Glue" — status: met
- `req_2` must_have — "Strong Python" — status: partially_met
- `req_3` strong_preference — "Fintech experience" — status: missing
- `req_4` constraint, disqualifying — "Based in UAE" — status: met
- `req_5` nice_to_have — "AWS certification" — status: missing

LLM scores: `skills_match_score=78`, `experience_relevance_score=72`

**requirements_match_score:**
- total_weight = 0.70 + 0.70 + 0.25 + 0.05 = 1.70
- earned_weight = 0.70×1.0 + 0.70×0.5 + 0.25×0.0 + 0.05×0.0 = 1.05
- base = (1.05 / 1.70) × 100 = 61.76
- No disqualifying floors triggered
- → **61.76**

**cv_fit** = (78 + 72) / 2 = **75.0**

**role_fit** = 0.40 × 75.0 + 0.60 × 61.76 = 30.0 + 37.06 = **67.06**

**recommendation:** no constraint failure, no missing must-have, role_fit between 50 and 70 → **LEAN_NO**

---

## Why this split works

- The LLM is good at: reading evidence, judging "is this skill met by this CV span?", summarising
- Code is good at: applying weights consistently, enforcing floors, deriving categorical decisions from numbers

If the LLM produced `requirements_match_score` and `recommendation` directly, two identical CVs would routinely produce different scores and different recommendations across runs. Splitting the work eliminates that variance entirely on the math side, while preserving the LLM's judgment on the parts only it can do.
