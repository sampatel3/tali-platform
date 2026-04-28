# Calibrators (Phase 3)

Per-(role_family, dimension) calibrators map a raw cv_match score
into a calibrated `P(advance | recruiter)`. Used by the recruiter UI
as the primary ranking signal, with the raw score still surfaced for
explainability (RALPH 3.5).

## When a calibrator fires

`run_cv_match` (v4.2 path) attaches `calibrated_p_advance` to the
`CVMatchOutputV4` when:

1. The archetype router picks a rubric (i.e. the JD routes to a
   known role family).
2. A calibrator snapshot exists at
   `snapshots/{archetype_id}_role_fit_latest.json`.

When either condition fails, `calibrated_p_advance` stays `None` and
the caller falls back to the raw `role_fit_score`.

## API response shape (RALPH 3.5)

`candidate_applications.cv_match_details` (and the GET endpoint that
returns it) now includes:

```json
{
  "role_fit_score": 73.0,
  "cv_fit_score": 70.0,
  "requirements_match_score": 75.0,
  "recommendation": "yes",
  "calibrated_p_advance": 0.62,        // NEW (Phase 3.4)
  "dimension_scores": {                // NEW (Phase 2.10)
    "skills_coverage": 80.0,
    "skills_depth": 75.0,
    ...
  },
  "requires_human_review": false,      // NEW (Phase 4.6)
  "score_std": null                    // NEW (Phase 3.8) — populated only on borderline cases
}
```

Existing fields (role_fit_score, cv_fit_score, requirements_match_score,
recommendation) keep their old shape and meaning. New fields are
nullable so v3 consumers don't have to think about them.

## Frontend coordination

The RALPH spec says 3.5 is a coordination task. The backend is
ready to surface the field; the recruiter UI rollout is owned by
the frontend route owner. Suggested rollout:

1. Hide `calibrated_p_advance` in the UI initially (backend-only).
2. Add a developer-flag toggle to expose it as a sortable column.
3. After a week of recruiter feedback, default it to visible with the
   raw score still shown for explainability.

## Persistence layout

```
snapshots/
├── {role_family}_{dimension}_{ts}.json     — historical fits
└── {role_family}_{dimension}_latest.json   — runtime read-from
```

Each snapshot JSON contains either:

```json
{"kind": "platt", "a": 0.04, "b": -2.1,
 "feature_scale": 13.2, "feature_shift": 50.0}
```

or

```json
{"kind": "isotonic",
 "breakpoints": [{"x": 30, "y": 0.05}, {"x": 50, "y": 0.20}, ...]}
```

JSON (not pickle) keeps the artefact human-readable and avoids the
arbitrary-code-execution risk of unpickling something written by a
training pipeline.

## Recalibration cadence

Run `python -m app.cv_matching.calibrators.recalibrate` weekly.
Wire to Celery beat or your platform's cron. Per-fit reports are
logged at INFO; ECE > 0.05 logs at WARNING ("ECE alert ...") so
your alerting can grep for it.
