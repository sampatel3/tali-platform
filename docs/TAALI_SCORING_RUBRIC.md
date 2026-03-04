# TAALI Scoring Rubric

## Purpose

TAALI is the master overall candidate ranking score used to compare candidates for the same role.

`Role fit` is a visible sub-score because recruiters need it for filtering, quick interpretation, and to understand how much of the ranking is driven by fit to the role versus assessment performance.

The rubric is hierarchical:

1. Compute `Role fit`.
2. If no completed assessment exists, `TAALI = Role fit`.
3. If a completed assessment exists, recompute `TAALI` using both `Assessment` and `Role fit`.
4. Apply downstream integrity caps or modifiers after the weighted score is computed.

## Rubric Version

- Current version: `taali_v3_hierarchical_role_fit`

## Visible Recruiter Scores

Recruiter-facing primary scores are limited to:

- `TAALI score`
- `Role fit`
- `Assessment`

Supporting detail can still show:

- `CV fit`
- `Requirements fit`
- assessment dimensions
- evidence, rationale, integrity, and history

## Weighting

### Role Fit

`Role fit = 40% CV fit + 60% Requirements fit`

Rationale:

- `CV fit` captures broad profile alignment
- `Requirements fit` captures recruiter-specific must-have alignment
- weighting requirements higher keeps the fit score role-specific and recruiter-useful

### TAALI Before Assessment

If there is no completed assessment:

`TAALI = Role fit`

### TAALI After Assessment

If there is a completed assessment:

`TAALI = 60% Assessment + 40% Role fit`

Rationale:

- assessment evidence should carry more weight once it exists
- role fit remains a meaningful signal for ranking and filtering

## Fallback Rules

- If only one `Role fit` component exists, `Role fit` falls back to the available component.
- If `Assessment` exists but `Role fit` is missing, `TAALI` falls back to `Assessment`.
- If `Assessment` is missing, `TAALI` falls back to `Role fit`.
- If neither is available, `TAALI` is unset.

## Integrity and Modifier Rules

Integrity caps, fraud modifiers, or severe-language caps are applied after the weighted score is computed.

These modifiers do not create additional recruiter headline scores.

## Compatibility

Legacy fields such as:

- `cv_job_match_score`
- `skills_match`
- `experience_relevance`

remain available for compatibility and internal analysis, but they are not primary recruiter-facing headline scores.
