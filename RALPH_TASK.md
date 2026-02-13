# RALPH_TASK.md — TALI Platform Hardening Execution Plan

> **Status:** ACTIVE (reopened)
> **Last refreshed:** 2026-02-13
> **Purpose:** source of truth for post-MVP hardening, QA, and release confidence work.

---

task: TALI Platform - Codebase + Product Hardening Sprint
test_command: "cd backend && pytest -q -m 'not production' && cd ../frontend && npm test -- --run"

---

## 1) Current status snapshot

### Completed in the current cycle
- [x] Removed candidate-side CV upload gate from assessment start.
- [x] Removed dashboard-side assessment creation entry point.
- [x] Restored assessment runtime context payload shape (`task_key`, `role`, `scenario`, `repo_structure`, `evaluation_rubric`, `extra_data`).
- [x] Isolated production smoke tests from default backend local-safe run.
- [x] Reconciled key docs (`README`, `PRODUCT_PLAN`, `RALPH_TASK`) toward a single active-plan narrative.

### Still open / in progress
- [ ] Verify all task creation/import paths always persist `scenario` and `repo_structure` end-to-end.
- [ ] Add targeted E2E for “History Backfill” (task context + repo files visible before first prompt).
- [ ] Resolve frontend unit test failures and remaining `act(...)` warning cleanup.
- [ ] Complete frontend decomposition so `App.jsx` is primarily routing/composition.

---

## 2) Parallel lane ownership (non-overlapping)

- **Lane A (Platform/Backend reliability):** schema correctness, request tracing, health readiness, task correlation.
- **Lane B (CI & test gates):** smoke isolation, CI matrix, coverage/lint quality gates.
- **Lane C (Frontend quality):** test stabilization, jsdom/test-env hardening, warning reduction.
- **Lane D (Product UX/workflow):** recruiter insights, Workable/report actions, document visibility/download UX.

Execution rule: each lane only touches scoped files to avoid overlap; merge sequentially with explicit handoff notes.

---

## 3) Priority workstreams and acceptance criteria

### P1 — Core assessment integrity (Owner: Agent A)
**Goal:** enforce the core product promise around task assessment reliability.

- [ ] Verify task context is visible in IDE before first prompt (`task`, `scenario`, `repo_structure`, rubric context).
- [ ] Verify fallback UX when repository context is missing.
- [ ] Verify full telemetry coverage for candidate interactions (prompt/response/code/test/timing/session metadata).
- [ ] Add focused E2E for history-backfill context visibility.

**Acceptance criteria**
- Candidate sees complete task context pre-coding.
- No major telemetry gaps in stored assessment artifacts.

### P2 — Scoring completeness + glossary (Owner: Agent B)
**Goal:** improve score comprehensiveness and interpretability.

- [ ] Verify frontend↔backend score category/metric parity.
- [ ] Centralize plain-English descriptions for every scoring dimension.
- [ ] Ensure charts/tooltips read from one glossary source.
- [ ] Add graceful UX fallback for partial/missing score components.

**Acceptance criteria**
- Each visible dimension has a clear description.
- Partial score payloads render without confusing empty states.

### P3 — Candidate comparison UX (Owner: Agent C)
**Goal:** allow clear Candidate A vs Candidate B comparison.

- [ ] Add comparison mode entry in candidate detail.
- [ ] Add radar overlay mode (A over B).
- [ ] Add side-by-side tables/cards with deltas.
- [ ] Keep selectors explicit (`Candidate A`, `Candidate B`).

**Acceptance criteria**
- Overlay and side-by-side modes are both functional.
- Recruiters can compare without leaving candidate-detail flow.

### P4 — Frontend decomposition completion (Owner: Agent C)
**Goal:** reduce monolith risk in `frontend/src/App.jsx`.

- [ ] Extract remaining Tasks flow from `App.jsx` if still embedded.
- [ ] Simplify route-level composition shell.
- [ ] Add minimal page-level tests for extracted modules.

**Acceptance criteria**
- `App.jsx` is primarily route wiring/composition.
- No behavior regressions for dashboard/candidates/tasks/detail flows.

### P5 — Landing page + brand-agnostic readiness (Owner: Agent D)
**Goal:** improve positioning and rebrand flexibility.

- [ ] Add explicit “What we test (30+ signals)” section.
- [ ] Clarify value proposition with concrete examples.
- [ ] Centralize brand name/domain/assets into config/constants.
- [ ] Ensure email/page-title/logo usage reads from centralized brand config.

### P6 — Model-tier strategy + cost observability (Owner: Agent E)
**Goal:** control cost while preserving quality path.

- [ ] Keep cheapest Claude tier as non-production default.
- [ ] Keep production model override configurable by environment.
- [ ] Track per-assessment and per-tenant costs (Claude/E2B/email/storage).
- [ ] Add dashboard thresholds (daily spend, cost per completed assessment).

### P7 — Integration + release gate (Owner: Agent F)
**Goal:** merge phase outputs safely with measurable confidence.

- [ ] Run full local-safe QA matrix and production build.
- [ ] Validate no doc drift across `README`, `PRODUCT_PLAN.md`, and `RALPH_TASK.md`.
- [ ] Publish release notes mapping each phase to shipped outcomes.

**Release gate**
- [ ] All phase acceptance criteria met.
- [ ] No blocking regressions in assessment runtime, scoring, or candidate comparison.

---

## 4) QA command contract

Use these commands as the baseline verification path:

```bash
# Backend local-safe path
cd backend && pytest -q -m "not production"

# Frontend unit tests
cd frontend && npm test -- --run

# Frontend production build
cd frontend && npm run build
```

Optional (live env dependent):

```bash
# Includes production smoke checks
cd backend && pytest -q
```

---

## 5) Definition of done for this reopened RALPH cycle

- [ ] Backend and frontend default test suites pass consistently.
- [x] Production-only tests are separated from local baseline.
- [x] README/task plans reflect the active-plan structure.
- [ ] Frontend architecture is no longer concentrated in one mega-file.
- [x] Recruiter-facing evaluation workflow is complete/exportable.
- [x] CI enforces baseline checks.

---

## 6) Backlog (non-blocking)

- [ ] Candidate comparison overlay + side-by-side cohort tooling.
- [ ] Scoring glossary + tooltip system.
- [ ] Centralized brand configuration surface for rebrand.
- [ ] Incremental TypeScript migration.
- [ ] Router migration away from hash routing.
- [ ] Enterprise access controls (SSO/SAML).
