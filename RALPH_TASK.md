# RALPH_TASK.md — TAALI Platform Hardening Execution Plan

> **Status:** ACTIVE (reopened)
> **Last refreshed:** 2026-02-13
> **Purpose:** source of truth for post-MVP hardening, QA, and release confidence work.

---

task: TAALI Platform - Codebase + Product Hardening Sprint
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
- [x] Verify all task creation/import paths always persist `scenario` and `repo_structure` end-to-end.
- [x] Add targeted E2E for “History Backfill” (task context + repo files visible before first prompt).
- [x] Resolve frontend unit test failures and remaining `act(...)` warning cleanup.
- [x] Complete frontend decomposition so `App.jsx` is primarily routing/composition.

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

- [x] Verify task context is visible in IDE before first prompt (`task`, `scenario`, `repo_structure`, rubric context).
- [x] Verify fallback UX when repository context is missing.
- [x] Verify full telemetry coverage for candidate interactions (prompt/response/code/test/timing/session metadata).
- [x] Add focused E2E for history-backfill context visibility.

**Acceptance criteria**
- Candidate sees complete task context pre-coding.
- No major telemetry gaps in stored assessment artifacts.

### P2 — Scoring completeness + glossary (Owner: Agent B)
**Goal:** improve score comprehensiveness and interpretability.

- [x] Verify frontend↔backend score category/metric parity.
- [x] Centralize plain-English descriptions for every scoring dimension.
- [x] Ensure charts/tooltips read from one glossary source.
- [x] Add graceful UX fallback for partial/missing score components.

**Acceptance criteria**
- Each visible dimension has a clear description.
- Partial score payloads render without confusing empty states.

### P3 — Candidate comparison UX (Owner: Agent C)
**Goal:** allow clear Candidate A vs Candidate B comparison.

- [x] Add comparison mode entry in candidate detail.
- [x] Add radar overlay mode (A over B).
- [x] Add side-by-side tables/cards with deltas.
- [x] Keep selectors explicit (`Candidate A`, `Candidate B`).

**Acceptance criteria**
- Overlay and side-by-side modes are both functional.
- Recruiters can compare without leaving candidate-detail flow.

### P4 — Frontend decomposition completion (Owner: Agent C)
**Goal:** reduce monolith risk in `frontend/src/App.jsx`.

- [x] Extract remaining Tasks flow from `App.jsx` if still embedded.
- [x] Simplify route-level composition shell.
- [x] Add minimal page-level tests for extracted modules.

**Acceptance criteria**
- `App.jsx` is primarily route wiring/composition.
- No behavior regressions for dashboard/candidates/tasks/detail flows.

### P5 — Landing page + brand-agnostic readiness (Owner: Agent D)
**Goal:** improve positioning and rebrand flexibility.

- [x] Add explicit “What we test (30+ signals)” section.
- [x] Clarify value proposition with concrete examples.
- [x] Centralize brand name/domain/assets into config/constants.
- [x] Ensure email/page-title/logo usage reads from centralized brand config.

### P6 — Model-tier strategy + cost observability (Owner: Agent E)
**Goal:** control cost while preserving quality path.

- [x] Keep cheapest Claude tier as non-production default.
- [x] Keep production model override configurable by environment.
- [x] Track per-assessment and per-tenant costs (Claude/E2B/email/storage).
- [x] Add dashboard thresholds (daily spend, cost per completed assessment).

### P7 — Integration + release gate (Owner: Agent F)
**Goal:** merge phase outputs safely with measurable confidence.

- [x] Run full local-safe QA matrix and production build.
- [x] Validate no doc drift across `README`, `PRODUCT_PLAN.md`, and `RALPH_TASK.md`.
- [x] Publish release notes mapping each phase to shipped outcomes.

**Release gate**
- [x] All phase acceptance criteria met.
- [x] No blocking regressions in assessment runtime, scoring, or candidate comparison.

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

- [x] Backend and frontend default test suites pass consistently.
- [x] Production-only tests are separated from local baseline.
- [x] README/task plans reflect the active-plan structure.
- [x] Frontend architecture is no longer concentrated in one mega-file.
- [x] Recruiter-facing evaluation workflow is complete/exportable.
- [x] CI enforces baseline checks.

---

## 6) Backlog (non-blocking)

- [x] Candidate comparison overlay + side-by-side cohort tooling.
- [x] Scoring glossary + tooltip system.
- [x] Centralized brand configuration surface for rebrand.
- [x] Incremental TypeScript migration.
- [x] Router migration away from hash routing.
- [x] Enterprise access controls (SSO/SAML).

Execution note (2026-02-13): Added incremental TS support (`tsconfig.json`, `typecheck` script, converted shared scoring libs to `.ts`), moved primary app routing to path-based URLs with legacy hash normalization fallback, and shipped enterprise org access controls (allowed domains, SSO enforcement, SAML metadata settings) across backend APIs, middleware, and Settings UI.

---

## 7) Taali Assessment System gaps (from CURSOR_IMPLEMENTATION_SPEC)

**Source:** `docs/TAALI_ASSESSMENT_SYSTEM_IMPLEMENTATION_STATUS.md`. Execute in order below.

### G1 — Repo management + Monaco IDE (CRITICAL: do first)

**Goal:** Full working repo management and candidate-facing repo structure in the IDE.

- [x] **G1.1** Monaco IDE shows repo structure: file tree from `repo_structure` (or assessment start payload) visible in assessment UI; candidate can expand/collapse and open files.
- [x] **G1.2** Candidate interacts with repo structure in Monaco: selecting a file loads its content into the editor (or a second pane); edits apply to the “current file” context sent with prompts / persisted in sandbox where applicable.
- [x] **G1.3** Production GitHub (optional but recommended): implement real GitHub API in `AssessmentRepositoryService` (create repo, create branch, push) using GITHUB_TOKEN/GITHUB_ORG when not in mock mode; or document and keep mock-only with clear “production: set GITHUB_MOCK_MODE=false and implement” note.
- [x] **G1.4** If git push fails on timeout/submit: persist patch/diff in DB so work is not lost (already partially there via `git_evidence`; ensure diff is always stored even when push fails).

### G2 — Task seed + production task reset

**Goal:** Seed uses loader; production tasks cleared and re-seeded from `tasks/` once G1 is done.

- [x] **G2.1** Seed script uses task loader: `scripts/seed_tasks_db.py` calls `load_task_specs(tasks_dir)` (or validates each JSON with `validate_task_spec`) so rubric weights are validated at seed time.
- [x] **G2.2** Add script to remove all tasks from DB (for use with Railway): e.g. `scripts/clear_tasks.py` — nullify `assessment.task_id`, then delete all tasks; runnable via `railway run python scripts/clear_tasks.py` or with `DATABASE_URL` set.
- [x] **G2.3** After G1 is complete and G2.1/G2.2 are in place: remove all tasks in production using Railway CLI/script, then re-seed from `tasks/` (run seed script once) so only tasks from `tasks/*.json` exist.
  - Executed on 2026-02-13 via Railway CLI: cleared 6 production tasks, then seeded 1 task from `tasks/`.

**Run clear then seed:** From repo root, with DB reachable (see note below):
```bash
railway run bash -c 'cd backend && .venv/bin/python ../scripts/clear_tasks.py'
railway run bash -c 'cd backend && .venv/bin/python ../scripts/seed_tasks_db.py'
```
**Note:** `railway run` runs locally; Railway’s `DATABASE_URL` uses `postgres.railway.internal`, which only resolves inside Railway. To run G2.3 from your machine, add **`DATABASE_PUBLIC_URL`** in the Railway project (from the Postgres service → Connect → “Public network” URL) so the scripts use it. Otherwise run the same commands from **Railway Shell** in the dashboard (service → Shell).

### G3 — Recruiter: git evidence + manual evaluator UI

**Goal:** Evaluator sees chat + git artifacts; can set manual rubric scores and evidence.

- [x] **G3.1** Display git evidence in recruiter UI: in candidate/assessment detail, show `git_evidence.diff_main`, `git_evidence.commits`, `git_evidence.head_sha` (e.g. “Code / Git” or “Evidence” tab). Data is already in API response.
- [x] **G3.2** Manual evaluator UI: section that shows assessment `evaluation_rubric` categories; for each category, allow selecting excellent/good/poor (dropdown or buttons).
- [x] **G3.3** Evidence notes: require at least one evidence snippet/note per category (or per assessment); store on assessment or EvaluationResult.
- [x] **G3.4** Show chat log alongside git diff/commits so evaluator can pick evidence (timeline/prompts already exist; place next to or above git evidence in same view).

### G4 — EvaluationResult model (optional)

**Goal:** First-class evaluation artifact if product wants it.

- [x] **G4.1** Add EvaluationResult model (or equivalent): categoryScores[categoryKey] = {score, weight, evidence[]}, overallScore, strengths[], improvements[], link to assessment, completed_due_to_timeout.
- [x] **G4.2** Wire manual evaluator UI to persist to this model (or to assessment JSON field) and load for display.

### Implementation order (execute in this sequence)

1. **G1.1, G1.2** — Monaco repo structure + candidate interaction with repo files.
2. **G1.3** (optional), **G1.4** — Production GitHub or doc; ensure diff persisted on push failure.
3. **G2.1, G2.2** — Seed uses loader; clear_tasks script.
4. **G2.3** — Remove all tasks in production (Railway), then re-seed from `tasks/` once.
5. **G3.1** — Recruiter UI: display git_evidence.
6. **G3.2, G3.3, G3.4** — Manual rubric UI + evidence notes + chat alongside git.
7. **G4** — EvaluationResult model if desired.

---

## 8) UI/UX Modernization Plan

> **Status:** COMPLETE
> **Started:** 2026-02-17
> **Goal:** Systematically adopt design system across all pages. Switch body font to Inter, eliminate inline styles, remove `!important` dark-mode hacks, and use `<Button>`, `<Input>`, `<Select>`, `<Badge>`, `<Spinner>`, `<Panel>`, `<TableShell>`, `<Sheet>` everywhere.

### Key Principles
1. **Never add border-radius** — 0px brutalist aesthetic preserved
2. **`font-mono` is opt-in** — body font is Inter; add `font-mono` only to: table headers, code/data values, emails, technical labels, badge text
3. **No `style={{}}` for colors** — every color through CSS variable or Tailwind class mapped to CSS variable
4. **Dark mode is free** — CSS variables handle everything, no `!important` hacks
5. **Components first** — always prefer primitives over raw HTML

### Phase 1: Design Foundation (4 files) ✅
- [x] `index.html` — Add Inter font from Google Fonts alongside JetBrains Mono
- [x] `index.css` — Change `--taali-font` to Inter; add semantic colors (`--taali-success`, `--taali-warning`, `--taali-danger`, `--taali-info` + soft/border); add difficulty levels (`--taali-level-*`); dark mode overrides; remove 25+ `!important` hacks; fix input bg to `var(--taali-surface)`
- [x] `tailwind.config.js` — Map semantic colors to CSS variables
- [x] `TaaliPrimitives.jsx` — Add `Spinner`, `TabBar`, `danger`/`info` Badge variants; import `Loader2`

### Phase 2: Auth Pages (5 files) ✅
- [x] `LoginPage.jsx` — raw `<input>` → `<Input>`, raw `<button style>` → `<Button>`, `<Loader2 style>` → `<Spinner>`, hardcoded colors → CSS vars
- [x] `RegisterPage.jsx` — same pattern
- [x] `ForgotPasswordPage.jsx` — same pattern
- [x] `ResetPasswordPage.jsx` — same pattern
- [x] `VerifyEmailPage.jsx` — same pattern

### Phase 3: Navigation & Shared Atoms (3 files) ✅
- [x] `DashboardNav.jsx` — mobile hamburger menu, replace inline styles, CSS var classes
- [x] `DashboardAtoms.jsx` — replace `border-black`, `bg-white`, `text-gray-*`; refactor `StatusBadge` → `<Badge>`
- [x] `Branding.jsx` — replace `style={{ backgroundColor }}` with Tailwind class

### Phase 4: Dashboard Page (1 file) ✅
- [x] `DashboardPage.jsx` — remove mobile block, `<Select>`, `<Button>`, `<Spinner>`, `<TableShell>`, CSS vars, remove `font-mono` from subtitle

### Phase 5: Settings Page (1 file) ✅
- [x] `SettingsPage.jsx` — `<TabBar>`, `<Spinner>`, `<Input>`, `<Select>`, `<Button>`, `<Sheet>` for Workable drawer, `<Panel>`, CSS vars

### Phase 6: Tasks Pages (3 files) ✅
- [x] `TasksListView.jsx` — difficulty CSS vars, `<Spinner>`, `<Button>`, `<Badge>`, `<Panel>`
- [x] `TasksPage.jsx` — (structure unchanged; uses TasksListView)
- [x] `CreateTaskModal.jsx` — `<Panel>`, `<Button>`, CSS vars

### Phase 7: Candidates Pages (7 files) ✅
- [x] `CandidateResultsTab.jsx` — `scoreColor` → `var(--taali-success/warning/danger)`
- [x] `CandidateDetailPage.jsx` — `getRecommendation` color map → CSS vars
- [x] `CandidateDetailSecondaryTabs.jsx` — remove `font-mono` from narrative, CSS vars
- [x] `CandidatesPage.jsx`, `CandidatesTable.jsx`, `CandidateSheet.jsx`, `AssessmentInviteSheet.jsx` — design system

### Phase 8: Analytics & Landing (2 files) ✅
- [x] `AnalyticsPage.jsx` — loaders, border/bg classes → design system
- [x] `LandingPage.jsx` — inline purple → CSS vars, Inter for body, mono for code snippets

### Phase 9: Assessment Runtime (4-6 files) ✅
- [x] `AssessmentTopBar.jsx` — inline purple → CSS vars
- [x] `CandidateWelcomePage.jsx` — same
- [x] `AssessmentBrandGlyph.jsx` — same
- [x] `AssessmentWorkspace.jsx` — same
- [x] `ClaudeChat.jsx` — keep monospace for code/chat, CSS vars for colors
- [x] `CodeEditor.jsx` — keep monospace for code, CSS vars for colors

### Verification (after each phase)
1. `npm run dev` — visually verify changed pages
2. Toggle dark mode in Settings → Preferences
3. Test mobile viewport (Chrome DevTools)
4. `npm run build` — no build errors
5. `npm test` — existing tests pass
