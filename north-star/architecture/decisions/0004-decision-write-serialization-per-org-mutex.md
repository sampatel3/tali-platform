# 4. Decision-write serialization (per-org mutex)

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** Sam

## Context

Workable decision writes (hiring decisions pushed to the ATS) are not safely
concurrent: two writes for the same org can race, producing duplicate or conflicting
decisions in Workable, which is externally visible and hard to undo. Background jobs
and user actions can both trigger writes.

## Decision

We will serialize Workable decision writes **per org** behind a mutex. For a given
org, at most one decision write proceeds at a time; others wait. The serialization is
keyed by org so different orgs are unaffected. This lives with the decision engine in
the substrate.

Current (legacy) home: `backend/app/components/integrations/workable/service.py` and
`.../workable/sync_service.py`. Target home: `mainspring`.

## Consequences

- No duplicate/conflicting decision writes for an org; the ATS stays consistent.
- Decision writes for a single org are throughput-limited by design — acceptable
  given they are low-frequency and correctness-critical.
- Any new code path that writes decisions MUST go through the serialized engine, not
  call Workable directly.
- Modelled as `invariants[].decision-writes-serialized` and on the `decision-engine`
  component in `model.yaml`.
