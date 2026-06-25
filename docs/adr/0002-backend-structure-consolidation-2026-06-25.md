# ADR 0002 — Backend structure consolidation & god-file burn-down

- **Status:** Accepted (plan), execution staged
- **Date:** 2026-06-25
- **Related:** PR #711 (de-bloat pass + CI file-size gate), the 2026-06-25 full-repo review

## Context

A full-repo review surfaced two structural problems in `backend/app` (122k LOC):

1. **Three competing organisation paradigms.** Business logic lives in **both**
   `services/` (24.5k LOC) and `components/` (13.9k LOC) with no rule for which
   goes where, while `domains/` (25k LOC) holds the route layer *plus* some logic
   (`pipeline_service.py`, `role_support.py`). Concrete proof of the drift: Workable
   logic is split across `services/workable_*.py` (4 files) **and**
   `components/integrations/workable/` (4 files). New code lands in whichever tier
   the author happens to pick.

2. **God-files.** `domains/assessments_runtime/applications_routes.py` is **5,426
   LOC / 44 routes / 57 helpers / 56 imports** on a single prefix-less router —
   the live core application API (pipeline, scoring, CV fetch, Workable outcome
   sync, the unified "process candidates" cascade). `components/integrations/workable/sync_service.py`
   (2,405) is the next-largest.

There was **no backend CI size gate** (only `compileall` + the alembic head check),
so both problems could grow unchecked. PR #711 shipped `scripts/check_file_sizes.py`
(the existing 500-LOC route/service guard, now CI-enforced and the single source of
truth for `tests/test_ci_architecture_gates.py`). That **stops new bloat** but does
not fix the existing god-files or the tier drift.

## Decision

**Target shape (no big-bang):**

- **Routes → `domains/<area>/*_routes.py`.** HTTP surface only; thin handlers that
  delegate to logic.
- **Logic → `components/<domain>/`.** One home for business logic + integration
  adapters, grouped by domain.
- **`services/` is frozen.** No new files in `services/`; migrate existing modules
  into `components/` opportunistically when they're next touched. Do not mass-move.

**`applications_routes.py` is split incrementally, not in one PR**, because it is the
live core API with only partial test coverage, and a single 5.4k-LOC relocation
cannot be verified well enough for an unattended deploy. Splitting also does **not**
reduce total LOC — the value is navigability — so it does not justify big-bang risk.

## Consequences

- Clearer boundaries over time; new contributors get one obvious home per concern.
- **Interim coexistence**: `services/` and `components/` both exist during the
  migration. The freeze + gate keep the drift from widening.
- Each god-file split is several small PRs rather than one — more review overhead,
  but each step is independently verifiable and revertible.
- The 500-LOC gate's allowlist (`scripts/check_file_sizes.py`) is the burn-down
  tracker: removing an entry = that file is now healthy.

## Rollout / deprecation plan

**Already shipped (2026-06-25, PR #711):** CI file-size gate; `candidate_feedback_engine`
PDF primitives extracted to `components/reporting/`; shared assessment helpers to
`components/scoring/assessment_metrics.py`.

**`applications_routes.py` burn-down — one PR per step, each gated on an OpenAPI
fingerprint (dump `app.openapi()` paths/methods/params before & after — must be
byte-identical) + app boot + `test_api_*` suites:**

1. **Helpers first (lowest risk).** Move the ~57 private helper functions to
   `applications/_helpers.py`. No routes move → the OpenAPI fingerprint is unchanged
   by construction; verify it anyway. *(Target: by 2026-07-15.)*
2. **Peel route groups**, one cohesive group per PR, into sibling route modules
   included on the same router: interviews/Fireflies → documents/PDF → Workable
   outcome sync → CV fetch / "process candidates" cascade. *(Target: through 2026-08.)*
3. Remove `applications_routes.py` from the gate allowlist once every piece is
   < 500 LOC (or consciously re-allowlist any that stay cohesively larger).

**`sync_service.py` (2,405):** split along its internal phases (fetch / map / persist)
when next substantially touched; not urgent. *(No fixed date — opportunistic.)*

**`services/` → `components/` migration:** opportunistic, file-by-file, when a module
is next edited for feature work. Reassess whether to accelerate at the next
architecture review. *(Checkpoint: 2026-09.)*

**Explicitly out of scope here:** the drifted score helpers (`_score_100` /
`_score_10` / `_extract_category_scores`) that differ between `analytics_routes.py`
and `candidate_feedback_engine.py` — reconciling them changes live report/analytics
numbers and is a scoring-policy decision, tracked separately.
