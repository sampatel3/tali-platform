# Bias-audit holdout (protected-attribute examples)

**REQUIRES COMPLIANCE / LEGAL SIGN-OFF. Git-tracked; every change carries a PR co-sign.**

These files are the protected-attribute holdout the promotion gate's EEOC
bias audit scores a candidate decision-policy model against before
auto-applying a learned change. They are read by
`app.decision_policy.audit_examples.load_audit_examples`, which threads them
through `nightly_retune.run_for_all_orgs → run_for_org → evaluate_auto_apply
→ bias_audit.audit` (TAA-28).

## Why a curated file (not warehouse data)

Protected attributes (gender, race, age_band, nationality,
disability_status, religion — see `bias_audit_thresholds.yaml`) are
deliberately **kept out of the production warehouse** (see
`config/blocked_edge_attributes.yaml` and the graph-writeback sensitivity
filter). The bias audit therefore cannot derive its holdout from app data;
it must be a **curated, compliance-signed** set supplied here, out of band.

## How it is consumed

Auto-apply is **operator-opt-in and OFF by default**
(`Organization.workspace_settings.decision_policy_auto_apply`). When it is
off, these files are irrelevant — every proposal is written inactive for
human review.

When auto-apply IS on, the gate runs the bias audit on the resolved holdout:
- A holdout present and clean (no parity violations) lets the gate pass.
- A holdout that shows disparate impact / parity gaps blocks activation.
- **No holdout present → the gate fails closed (cold start)**: the proposal
  is written inactive, exactly as if auto-apply were off. This is the safe
  default for any org that hasn't filed a signed-off holdout.

## File resolution

For each org, the loader looks for, in order:
1. `config/bias_audit_examples/<org-slug>.json` — org-specific holdout.
2. `config/bias_audit_examples/default.json` — org-agnostic fallback.
3. (neither) → `[]`, gate fails closed.

## Format

A JSON list of objects, each shaped like `decision_policy.bias_audit.AuditExample`:

```json
[
  {
    "features": {"role_fit": 0.81, "skills_depth": 0.70},
    "label": 1,
    "segments": {"gender": "F", "race": "white", "age_band": "30-39"}
  },
  {
    "features": {"role_fit": 0.42, "skills_depth": 0.55},
    "label": 0,
    "segments": {"gender": "M", "race": "black", "age_band": "40-49"}
  }
]
```

- `features` — the model input features (same keys the fitted policy uses);
  numeric values.
- `label` — the realised binary outcome (0/1).
- `segments` — protected-attribute values for this example; the audit needs
  at least 2 distinct values per attribute to measure parity.

Malformed rows are skipped with a warning so one bad entry never crashes the
nightly job. See `bias_audit_thresholds.yaml` for the parity thresholds and
the audited attribute list.
