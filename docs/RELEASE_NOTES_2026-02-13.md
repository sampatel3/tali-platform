# Release Notes — 2026-02-13 (RALPH Reopened Cycle Integration)

## Scope
This release note records Agent F integration work for phases P0 and P7 in `RALPH_TASK.md`.

## Phase-to-outcome mapping

- **P0 — Baseline lock & branch strategy (Agent F): shipped**
  - Confirmed backend local-safe baseline passes (`pytest -q -m "not production"`).
  - Confirmed frontend baseline passes (`npm test -- --run`).
  - Reconfirmed production-smoke isolation via pytest marker defaults and dedicated workflow.
  - Published branch naming plan for parallel lanes.

- **P1 — Core assessment integrity (Agent A): in progress / partially shipped**
  - Candidate runtime context restoration and start payload expansion were already marked complete in `RALPH_TASK.md`.
  - Remaining hardening items are still open.

- **P2 — Scoring completeness + dimension glossary (Agent B): in progress / partially shipped**
  - Recruiter interpretability improvements (strengths, risks, interview focus) were already marked complete.
  - Dimension glossary and fallback rendering tasks remain open.

- **P3 — Candidate comparison UX (Agent C): pending**
  - No merged comparison-mode release output yet.

- **P4 — Frontend decomposition completion (Agent C): in progress / partially shipped**
  - Candidate Detail, Dashboard, and Candidates extraction previously completed.
  - Tasks extraction and final route-shell simplification remain open.

- **P5 — Landing page message + brand-agnostic readiness (Agent D): pending**
  - No merged release output yet.

- **P6 — Model-tier strategy + cost observability (Agent E): pending**
  - No merged release output yet.

- **P7 — Integration, QA, and release gate (Agent F): in progress**
  - Full local-safe QA matrix + frontend production build are passing.
  - Release gate remains open until all phase acceptance criteria are complete and no blocking regressions remain in scoring/comparison/runtime tracks.

## Validation commands run

```bash
cd backend && pytest -q -m "not production"
cd frontend && npm test -- --run
cd frontend && npm run build
```
