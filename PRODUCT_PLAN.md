# PRODUCT_PLAN.md — TALI Platform: Full Product Plan

> **Created**: 2026-02-11
> **Status**: ACTIVE
> **Previous plans**: `RALPH_TASK.md` (reopened as active hardening execution plan)

> **Execution note (2026-02-13)**: MVP feature scope remains in this file; cross-cutting hardening, CI, and baseline stability tasks are tracked in `RALPH_TASK.md`.

---

This document captures the full product plan. The sections below explicitly separate what is **in scope for MVP** vs **out of scope for MVP (V2+)**.


## Current implementation snapshot (reviewed 2026-02-13)

The product is live as a functional MVP with hardening in progress:

- Auth, candidate/task/assessment lifecycle, and candidate assessment execution flow are operational.
- CV + Job Spec upload/extraction and CV-job-fit scoring are implemented.
- Scoring breakdown and recruiter-facing candidate detail workflow (including report download, Workable posting, and candidate document downloads) are implemented.
- CI now runs backend/frontend checks by default, with production smoke tests isolated.

Known active engineering focus:
- Frontend decomposition away from the single `App.jsx` surface (CandidateDetail + Dashboard + Candidates extracted, remaining pages pending).
- Residual frontend test warnings (`act(...)`) cleanup.
- Assessment runtime context fidelity (task + repo context visible before coding).
- Further UX polish and export/reporting depth.

### Product refinements requested in latest review

1. **Candidate comparison modes:** support both radar overlay (A vs B) and side-by-side comparison summaries.
2. **Dimension explainability:** each scoring dimension needs plain-English definitions via chart tooltips + glossary fallback.
3. **Landing page positioning:** make “what is being tested” explicit as a core selling point.
4. **Brand agnosticism:** centralize brand name/domain/assets to make rebranding low-risk.
5. **Core assessment focus checks:**
   - Ensure full in-IDE task context visibility.
   - Ensure complete telemetry capture for all interactions.
   - Keep scoring deeply comprehensive across all categories and metrics.
   - Preserve basic CV↔Job Spec comparison as a default capability.
6. **Model cost strategy:** default non-production/test workloads to the cheapest Claude model, with environment-level override to stronger models.
7. **Service cost observability:** track per-assessment/per-tenant costs for Claude, E2B, email, storage, and background jobs.

---

## Product Vision

TALI is an AI-augmented technical assessment platform that evaluates candidates on their **prompt engineering ability** — how they collaborate with AI to solve problems. This reveals more about modern engineering capability than CVs or traditional coding tests.

**Sales Pitch:** "We capture 30+ signals about how candidates work with AI — from prompt clarity to debugging strategy to communication quality. See exactly how each candidate thinks, not just what they produce."

---

## Product Scope (with MVP boundaries)

### What's IN:
- Candidate comparison (overlay + side-by-side modes) for recruiter decision support
- Plain-English scoring dimension glossary with chart hover tooltips
- Business registration + authentication
- Task creation and management
- Candidate management with CV upload and job spec upload
- Assessment email invitation to candidates (default template)
- Candidate assessment environment (E2B sandbox + Claude chat)
- Comprehensive data capture (all interactions logged)
- Full scoring engine (30+ metrics across 8 categories)
- CV-to-job-spec matching (single Claude call)
- Business dashboard with complete scoring breakdown per candidate

### What's OUT (V2):
- Stripe billing (free pilot phase)
- Custom email templates (use defaults — revisit V2)
- Proctoring mode (exists but disabled)
- Team/multi-user management (exists, but not core)
- Real-time WebSocket monitoring
- White-labeling / custom branding
- SSO / SAML
- React Router migration / TypeScript migration

---

## Codebase Audit Summary

A full codebase audit was performed on 2026-02-11. This section was refreshed against the current repository state on 2026-02-13.

### What works today:
- Auth: register, login, email verification, password reset, JWT ✅
- Task CRUD + AI generation via Claude ✅
- Candidate CRUD (create, list, search, edit, delete) ✅
- Assessment lifecycle: create → email → candidate opens link → start → E2B sandbox → Claude chat → submit ✅
- 30+ metric scoring model across 8 categories with fraud detection ✅
- Frontend: landing page, dashboard, candidate detail (radar chart, component scores, per-prompt scores, timeline) ✅
- Analytics dashboard, billing/usage display, team management ✅
- Email sending via Resend (invite, results, verification, password reset) ✅

### Historical critical gaps (from 2026-02-11 baseline):

Most of the baseline gaps below are now resolved in this repository. Remaining active items are primarily async scoring (`SCORING` state/Celery flow), reminder automation, and frontend decomposition away from `App.jsx`.

| # | Gap | Impact | Phase |
|---|-----|--------|-------|
| 1 | CV is uploaded but never read/parsed/analyzed | CV is a gate, not a signal — wasted data | 1 |
| 2 | No job spec concept — only coding tasks exist | Can't match candidate fit to role requirements | 1 |
| 3 | No CV-to-job-spec matching | Core differentiator missing entirely | 2 |
| 4 | Scoring radar chart shows zeros in MVP mode | Claude scoring disabled → individual columns NULL → radar empty | 3 |
| 5 | Frontend breakdown fields don't match backend | camelCase `bugsFixed` vs snake_case `tests_passed_ratio` | 3 |
| 6 | No communication/grammar scoring | v2_stubs.py returns None for everything | 3 |
| 7 | Code quality = 4 regex checks | No linting, AST, or complexity analysis | 3 |
| 8 | Scoring engine has only 12 flat components | Need 30+ metrics in 8 organized categories | 3 |
| 9 | analytics.py and service.py are disconnected duplicates | Analytics signals aren't fed into composite score | 3 |
| 10 | No per-prompt scoring stored | Frontend chart expects array of per-prompt scores — gets nothing | 3 |
| 11 | CV stored on local filesystem | Won't survive Railway deployments (ephemeral disk) | 4 |
| 12 | All scoring runs synchronously | Candidate waits for everything — timeout risk | 4 |

---

## PHASE 1: CV, JOB SPEC & DOCUMENT INFRASTRUCTURE
> **Priority: CRITICAL** — Without this, the platform can't assess candidate-role fit

### 1.1 — Job spec fields on Candidate model

The job spec describes the **role** the candidate is applying for. It belongs on the Candidate (not the Task — a Task is the coding challenge, multiple candidates for different roles could take the same task).

- [x] Add fields to `Candidate` model:
  ```python
  job_spec_file_url = Column(String, nullable=True)
  job_spec_filename = Column(String, nullable=True)
  job_spec_text = Column(Text, nullable=True)        # Extracted text for matching
  job_spec_uploaded_at = Column(DateTime(timezone=True), nullable=True)
  ```
- [x] Alembic migration for new candidate fields
- [x] Add `cv_text` field to `Candidate` model (extracted text for matching)

### 1.2 — Document processing service

- [x] Create `backend/app/services/document_service.py`:
  ```python
  def extract_text_from_pdf(file_path: str) -> str:
      """Extract text from PDF using PyPDF2"""

  def extract_text_from_docx(file_path: str) -> str:
      """Extract text from DOCX using python-docx"""

  def process_upload(file: UploadFile, entity_id: int, doc_type: str) -> dict:
      """
      1. Validate file type (PDF/DOCX) and size (max 5MB)
      2. Save to storage (local for now, S3 in Phase 4)
      3. Extract text
      4. Return { file_url, filename, extracted_text }
      """
  ```
- [x] Add `PyPDF2` and `python-docx` to `requirements.txt`

### 1.3 — CV text extraction on upload

- [x] Update the existing CV upload endpoints (`POST /assessments/{id}/upload-cv` and `POST /assessments/token/{token}/upload-cv`):
  - After saving file, extract text via `document_service`
  - Store extracted text in `candidate.cv_text` (not just assessment — the CV belongs to the candidate)
  - Keep existing `cv_file_url`, `cv_filename`, `cv_uploaded_at` on Assessment for audit trail

### 1.4 — Job spec upload endpoints

- [x] `POST /api/v1/candidates/{id}/upload-job-spec`
  - Multipart file upload (PDF/DOCX/TXT, max 5MB)
  - Extract text via `document_service`
  - Store file URL, filename, extracted text on Candidate
  - Requires auth (business user)

### 1.5 — Frontend: Add candidate flow with document uploads

The "Add Candidate" flow should become a multi-step process:

- [x] Step 1: Basic info (name, email, position)
- [x] Step 2: Upload documents
  - CV upload dropzone (PDF/DOCX, max 5MB) — **required**
  - Job spec upload dropzone (PDF/DOCX/TXT, max 5MB) — **required**
  - Show upload progress
- [ ] Step 3: Assign task (dropdown of active tasks)
- [ ] Step 4: Review & send invite
  - Preview: candidate name, email, task, uploaded documents
  - "Send Assessment Invitation" button

- [x] Update the Candidates page to show document status (CV uploaded? Job spec uploaded?)
- [x] Update the Candidate Detail page to show uploaded documents with download links

### 1.6 — Update schemas

- [x] Update `CandidateCreate` schema to accept document info
- [x] Update `CandidateResponse` schema to include `cv_text` (truncated), `job_spec_text` (truncated), document URLs
- [x] Create `DocumentUploadResponse` schema: `{ file_url, filename, text_preview }`

---

## PHASE 2: CV-TO-JOB-SPEC MATCHING WITH CLAUDE
> **Priority: CRITICAL** — Core MVP differentiator (single Claude call per assessment)

### 2.1 — Claude fit-matching service

- [x] Create `backend/app/services/fit_matching_service.py`:

  ```python
  CV_MATCH_PROMPT = """
  Analyze the match between this candidate's CV and the job specification.

  CV:
  {cv_text}

  Job Specification:
  {job_spec_text}

  Provide a JSON response with:
  {{
      "overall_match_score": <0-10>,
      "skills_match_score": <0-10>,
      "experience_relevance_score": <0-10>,
      "matching_skills": ["skill1", "skill2", ...],
      "missing_skills": ["skill1", "skill2", ...],
      "experience_highlights": ["relevant experience 1", ...],
      "concerns": ["concern 1", ...],
      "summary": "2-3 sentence summary of fit"
  }}

  Be objective and base scores only on evidence in the documents.
  """

  async def calculate_cv_job_match(cv_text: str, job_spec_text: str) -> dict:
      """Single Claude call to analyze CV-job fit.
      Uses claude-3-haiku for cost efficiency."""
  ```

- [x] Handle missing data gracefully: if no CV or no job spec, return `{ "error": "Missing CV or job spec" }` and skip fit scoring
- [x] Truncate inputs: CV to ~4000 chars, job spec to ~2000 chars (Haiku context limit)

### 2.2 — Integrate fit scoring into assessment pipeline

- [x] Add fields to `Assessment` model:
  ```python
  cv_job_match_score = Column(Float, nullable=True)
  cv_job_match_details = Column(JSON, nullable=True)  # Full Claude response
  ```
- [x] Alembic migration
- [x] Call `calculate_cv_job_match()` at **submission time** (in `submit_assessment`), alongside other scoring
- [x] Include `cv_job_match_score` in the composite score calculation (weight: 5%)
- [x] Store the full match details for frontend display

### 2.3 — Frontend: CV-Job Match display

- [x] Add "CV & Job Fit" tab to Candidate Detail page with:
  ```
  ┌─────────────────────────────────────────────────────────────┐
  │  CV-JOB FIT ANALYSIS                                        │
  │                                                             │
  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
  │  │Overall Match │  │Skills Match  │  │ Experience   │     │
  │  │   7.0/10     │  │   8.0/10     │  │   6.0/10     │     │
  │  └──────────────┘  └──────────────┘  └──────────────┘     │
  │                                                             │
  │  MATCHING SKILLS                                            │
  │  ✓ Python  ✓ SQL  ✓ AWS  ✓ Data Pipelines  ✓ ETL          │
  │                                                             │
  │  MISSING SKILLS                                             │
  │  ✗ Spark  ✗ Kafka  ✗ Kubernetes                            │
  │                                                             │
  │  RELEVANT EXPERIENCE                                        │
  │  • 3 years at DataCorp building ETL pipelines              │
  │  • Led migration from on-prem to AWS                       │
  │                                                             │
  │  CONCERNS                                                   │
  │  • No experience with streaming data                       │
  │  • Limited exposure to containerization                    │
  │                                                             │
  │  SUMMARY                                                    │
  │  "Strong foundation in core data engineering skills..."    │
  │                                                             │
  │  DOCUMENTS                                                  │
  │  📄 CV: john_doe_cv.pdf                [View] [Download]   │
  │  📄 Job Spec: senior_engineer.pdf      [View] [Download]   │
  └─────────────────────────────────────────────────────────────┘
  ```
- [x] Include fit score in the candidate detail header summary card

---

## PHASE 3: SCORING ENGINE — REBUILD TO 30+ METRICS
> **Priority: HIGH** — Scoring is the product; it needs to be comprehensive and display correctly

The existing scoring engine has 12 flat components. This phase restructures it into **8 categories with 30+ metrics**, fixes the backend→frontend data flow, and adds communication/approach scoring.

### 3.1 — Restructure scoring into 8 categories

Refactor `backend/app/components/scoring/service.py` to compute scores in organized categories:

**CATEGORY 1: Task Completion (Weight: 20%)**

| Metric | ID | Method |
|--------|-----|--------|
| Tests passed ratio | `tests_passed_ratio` | Heuristic |
| Time compliance | `time_compliance` | Heuristic |
| Time efficiency | `time_efficiency` | Heuristic |

**CATEGORY 2: Prompt Clarity (Weight: 15%)**

| Metric | ID | Method |
|--------|-----|--------|
| Prompt length quality | `prompt_length_quality` | Heuristic (sweet spot 20-150 words) |
| Question presence | `question_clarity` | Heuristic (% containing questions) |
| Specificity | `prompt_specificity` | Heuristic (has context vs vague) |
| Vagueness avoidance | `vagueness_score` | Heuristic (regex vague patterns) |

**CATEGORY 3: Context Provision (Weight: 15%)**

| Metric | ID | Method |
|--------|-----|--------|
| Code snippet inclusion | `code_context_rate` | Heuristic |
| Error message inclusion | `error_context_rate` | Heuristic |
| Line/file references | `reference_rate` | Heuristic |
| Prior attempt mention | `attempt_mention_rate` | Heuristic (regex: "I tried", "expected X but got Y") |

**CATEGORY 4: Independence & Efficiency (Weight: 20%)**

| Metric | ID | Method |
|--------|-----|--------|
| Time to first prompt | `first_prompt_delay` | Heuristic (>2min good, <30s bad) |
| Spacing between prompts | `prompt_spacing` | Heuristic (avg gap >60s = good) |
| Prompts per test passed | `prompt_efficiency` | Heuristic (fewer = better) |
| Token efficiency | `token_efficiency` | Heuristic (tokens per test) |
| Code changes before prompts | `pre_prompt_effort` | Heuristic (self-attempt rate) |

**CATEGORY 5: Response Utilization (Weight: 10%)**

| Metric | ID | Method |
|--------|-----|--------|
| Code change after prompt | `post_prompt_changes` | Heuristic |
| Zero-change prompts | `wasted_prompts` | Heuristic (prompt but no action) |
| Iterative refinement | `iteration_quality` | Heuristic (builds on previous) |

**CATEGORY 6: Communication Quality (Weight: 10%)**

| Metric | ID | Method |
|--------|-----|--------|
| Grammar quality | `grammar_score` | Heuristic (lowercase "i", random caps, double spaces) |
| Readability | `readability_score` | Heuristic (sentence length sweet spot 10-20 words) |
| Professional tone | `tone_score` | Heuristic (unprofessional patterns, filler words) |

```python
UNPROFESSIONAL_PATTERNS = [
    r"\b(wtf|omg|lol|lmao|bruh)\b",
    r"!!!+", r"\?\?\?+",
    r"^(ugh|argh|damn|shit|fuck)",
]
FILLER_WORDS = ["um", "uh", "like", "basically", "actually", "just", "really", "very"]
```

**CATEGORY 7: Debugging & Design (Weight: 5%)**

| Metric | ID | Method |
|--------|-----|--------|
| Debugging strategy | `debugging_score` | Heuristic (debug/error/isolate/hypothesis patterns) |
| Design thinking | `design_score` | Heuristic (architecture/tradeoff/scalability/edge case patterns) |

```python
DEBUGGING_PATTERNS = [
    r"(print|log|console\.log|debug)",
    r"(error|exception|traceback|stack)",
    r"(step by step|one at a time|isolate)",
    r"(hypothesis|theory|suspect|might be)",
]
DESIGN_PATTERNS = [
    r"(architecture|structure|design|pattern)",
    r"(tradeoff|trade-off|pros and cons|alternative)",
    r"(scalab|maintain|extend|modular)",
    r"(edge case|corner case|what if)",
    r"(performance|efficiency|complexity)",
]
```

**CATEGORY 8: CV-Job Match (Weight: 5%)**

| Metric | ID | Method |
|--------|-----|--------|
| Overall fit | `cv_job_match_score` | Claude (single call) |
| Skills alignment | `skills_match` | Claude (from same call) |
| Experience relevance | `experience_relevance` | Claude (from same call) |

**Category weights:**
```python
CATEGORY_WEIGHTS = {
    "task_completion": 0.20,
    "prompt_clarity": 0.15,
    "context_provision": 0.15,
    "independence": 0.20,
    "utilization": 0.10,
    "communication": 0.10,
    "approach": 0.05,
    "cv_match": 0.05,
}
```

- [x] Implement all 8 category scoring functions
- [x] Implement `calculate_final_score()` that calls all categories, computes weighted composite (0-100), applies fraud penalty
- [x] Keep fraud detection (6 flags, critical→cap at 30, high→cap at 50)
- [x] Store result in `score_breakdown` JSON with structure:
  ```json
  {
    "final_score": 72.3,
    "category_scores": { "task_completion": 8.5, "prompt_clarity": 6.8, ... },
    "detailed_scores": {
      "task_completion": { "tests_passed_ratio": 9.0, "time_compliance": 10.0, "time_efficiency": 6.5 },
      "prompt_clarity": { "prompt_length_quality": 7.5, ... },
      ...
    },
    "flags": [...],
    "metadata": { "total_prompts": 12, "total_tokens": 3847, "duration_minutes": 38, ... }
  }
  ```

### 3.2 — Per-prompt scoring

- [x] For each prompt interaction, compute individual scores:
  ```json
  { "clarity": 7.2, "specificity": 8.0, "efficiency": 6.5, "has_context": true, "is_vague": false }
  ```
- [x] Store as `prompt_analytics.per_prompt_scores` array in the assessment
- [x] This powers the "Prompt Quality Progression" line chart in the frontend

### 3.3 — Unify analytics.py and service.py

- [x] Refactor: `service.py` should call `analytics.py` for heuristic signals instead of reimplementing them
- [x] Remove duplicated logic between the two modules
- [x] Ensure all 10 analytics signals (time_to_first_prompt, prompt_speed, prompt_frequency, prompt_length_stats, copy_paste_detection, code_delta, self_correction_rate, token_efficiency, browser_focus_ratio, tab_switch_count) are stored in `prompt_analytics` JSON

### 3.4 — Fix backend→frontend data flow

- [x] Map the 8 category scores to the individual assessment columns that the frontend reads:
  ```python
  # In submit_assessment, after scoring:
  assessment.prompt_quality_score = category_scores["prompt_clarity"]
  assessment.prompt_efficiency_score = category_scores["independence"]
  assessment.independence_score = detailed["independence"]["first_prompt_delay"]
  assessment.context_utilization_score = category_scores["context_provision"]
  assessment.design_thinking_score = detailed["approach"]["design_score"]
  assessment.debugging_strategy_score = detailed["approach"]["debugging_score"]
  assessment.written_communication_score = category_scores["communication"]
  # ... etc for all radar chart dimensions
  ```
- [x] Fix the `breakdown` serialization to provide what the frontend summary card expects:
  ```python
  "breakdown": {
      "testsPassed": f"{tests_passed}/{tests_total}",
      "codeQuality": round(category_scores["task_completion"], 1),
      "timeEfficiency": round(detailed["task_completion"]["time_efficiency"], 1),
      "aiUsage": round(category_scores["independence"], 1),
      "communication": round(category_scores["communication"], 1),
  }
  ```
- [x] Ensure `prompt_analytics.component_scores` and `prompt_analytics.weights_used` are populated for the component bar chart

### 3.5 — Score explanations

- [x] Generate human-readable explanation for each metric:
  ```python
  "explanations": {
      "first_prompt_delay": "Candidate waited 4m 23s before first prompt, showing good self-reliance.",
      "prompt_specificity": "7 out of 12 prompts included specific code context or error messages.",
      "grammar_score": "Minor issues: 3 instances of lowercase 'i', otherwise clean writing.",
  }
  ```
- [x] Store explanations in `score_breakdown` for frontend display

---

## PHASE 4: PRODUCTION HARDENING
> **Priority: MEDIUM** — Required before real business usage

### 4.1 — S3 file storage

- [x] Implement S3 upload service using boto3 (already in requirements + config):
  ```python
  def upload_to_s3(file_path: str, key: str) -> str:
      """Upload file to S3 and return URL"""
  def download_from_s3(key: str) -> bytes:
      """Download file from S3"""
  ```
- [x] Move CV uploads from local filesystem to S3
- [x] Move job spec uploads to S3
- [x] Return S3 URLs instead of local paths
- [x] This is critical for Railway (ephemeral filesystem)

### 4.2 — Async scoring pipeline

- [ ] Move scoring from synchronous submit handler to Celery background task
- [ ] Add `SCORING` to AssessmentStatus enum:
  ```
  PENDING → IN_PROGRESS → SUBMITTED → SCORING → COMPLETED
  ```
- [ ] Candidate submits → immediate response "Assessment submitted" → status = SCORING → background job runs → status = COMPLETED
- [ ] Notify recruiter when scoring is complete (email)
- [ ] Frontend: show "Scoring in progress..." state when status = SCORING

### 4.3 — Error handling for scoring failures

- [x] If Claude API fails (for CV matching), fall back to no fit score and note it
- [x] If text extraction fails, skip fit matching and note it
- [x] Never block assessment completion due to scoring failures
- [x] Store which scoring components succeeded/failed in `score_breakdown.errors[]`

### 4.4 — Assessment expiry and reminders

- [ ] Celery periodic task to check for pending assessments approaching expiry
- [ ] Send reminder email 24h before expiry
- [ ] Auto-expire assessments past their deadline (status → EXPIRED)

---

## PHASE 5: COMPREHENSIVE SCORING DASHBOARD
> **Priority: HIGH** — The business must see a full, meaningful breakdown

### 5.1 — Redesigned candidate detail header

- [x] Overall score prominently displayed: **X / 100** with progress bar
- [x] Recommendation badge based on score:
  - **≥ 80**: "STRONG HIRE" (green)
  - **≥ 65**: "HIRE" (blue)
  - **≥ 50**: "CONSIDER" (amber)
  - **< 50**: "NOT RECOMMENDED" (red)
- [x] Key strengths: top 3 scoring categories (shown in category bars)
- [x] Red flags: any fraud flags + any category < 4/10 (color-coded red)
- [x] Role fit score from CV-job matching

### 5.2 — Assessment Results tab redesign

```
┌──────────────────────────────────────────────────────────────┐
│  CATEGORY BREAKDOWN                                          │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  [RADAR CHART showing 8 categories]                    │  │
│  │      Task Completion ● 8.5/10                          │  │
│  │  Independence ●                  ● Prompt Clarity      │  │
│  │     7.2/10                          6.8/10             │  │
│  │  Utilization ●                    ● Context            │  │
│  │     6.5/10                          7.5/10             │  │
│  │  Communication ●                  ● Approach           │  │
│  │     8.0/10                          5.5/10             │  │
│  │      CV Match ● 7.0/10                                 │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  DETAILED METRICS (expandable sections)                      │
│                                                              │
│  ▼ Task Completion (8.5/10) ─────────────────────────────   │
│    • Tests Passed: 9/10 (9.0) — 90% of tests passing        │
│    • Time Compliance: 10/10 — Completed within limit         │
│    • Time Efficiency: 6.5/10 — 38 min for 45 min task       │
│                                                              │
│  ▼ Prompt Clarity (6.8/10) ──────────────────────────────   │
│    • Prompt Length: 7.5/10 — Avg 45 words (good range)       │
│    • Clear Questions: 6.0/10 — 60% contained questions       │
│    • Specificity: 7.0/10 — Usually specific                  │
│    • Avoids Vagueness: 7.0/10 — 1 vague prompt detected     │
│                                                              │
│  ▼ Context Provision (7.5/10) ───────────────────────────   │
│    • Includes Code: 8.0/10 — 80% included code context       │
│    • Includes Errors: 6.0/10 — Shared errors when relevant   │
│    • Specific References: 7.0/10 — Referenced lines/files    │
│    • Prior Attempts: 9.0/10 — Showed what they tried first   │
│                                                              │
│  ▼ Independence & Efficiency (7.2/10) ───────────────────   │
│    • Thinks Before Asking: 8.0/10 — Waited 4m 23s           │
│    • Attempts Between Prompts: 6.5/10 — ~72s average gap    │
│    • Prompt Efficiency: 7.0/10 — 1.3 prompts per test       │
│    • Token Efficiency: 7.5/10 — 320 tokens per test         │
│    • Self-Attempt Rate: 7.0/10 — Changed code before 70%    │
│                                                              │
│  ▼ Response Utilization (6.5/10) ────────────────────────   │
│    • Uses AI Responses: 7.0/10 — Applied 70% of suggestions │
│    • Actionable Prompts: 7.0/10 — 3 zero-change prompts     │
│    • Iterative Refinement: 5.5/10 — Some building on prior   │
│                                                              │
│  ▼ Communication (8.0/10) ───────────────────────────────   │
│    • Grammar: 8.5/10 — Minor issues (2x lowercase 'i')      │
│    • Readability: 8.0/10 — Avg 14 words per sentence         │
│    • Professional Tone: 7.5/10 — Mostly professional         │
│                                                              │
│  ▼ Debugging & Design (5.5/10) ──────────────────────────   │
│    • Debugging Strategy: 6.0/10 — Some systematic approach   │
│    • Design Thinking: 5.0/10 — Limited design discussion     │
│                                                              │
│  ▼ CV-Job Fit (7.0/10) ─────────────────────────────────   │
│    • Skills Match: 8.0/10 — 5/8 required skills present     │
│    • Experience: 6.0/10 — 3 years relevant experience        │
│    [View Full Fit Analysis →]                                │
│                                                              │
│  ASSESSMENT METADATA                                         │
│  • Duration: 38 minutes (of 45 min limit)                    │
│  • Total Prompts: 12                                         │
│  • Tokens Used: 3,847                                        │
│  • Tests Passed: 9/10                                        │
│  • Started: Feb 11, 2026 9:00 AM                             │
│  • Submitted: Feb 11, 2026 9:38 AM                           │
└──────────────────────────────────────────────────────────────┘
```

### 5.3 — Prompt Log tab enhancement

```
┌──────────────────────────────────────────────────────────────┐
│  PROMPT TIMELINE                                             │
│  |----●--●-------●●--●--------●---●●●----|                   │
│  0    5   10    15   20    25    30    38 min                │
│                                                              │
│  PROMPT QUALITY PROGRESSION                                  │
│  [Line chart: clarity/specificity/efficiency over time]      │
│                                                              │
│  PROMPT LOG (12 prompts)                                     │
│                                                              │
│  #1 │ 0:42 │ 23 words │ +0/-0 lines │ Clarity: 3.5         │
│  ├─────────────────────────────────────────────────────────  │
│  │ "How do I connect to a PostgreSQL database in Python?"    │
│  │ ⚠️ No context provided                                   │
│  └──[View Response]                                          │
│                                                              │
│  #2 │ 3:15 │ 67 words │ +12/-0 lines │ Clarity: 8.5        │
│  ├─────────────────────────────────────────────────────────  │
│  │ "I'm trying to connect but getting this error:            │
│  │  'psycopg2.OperationalError: FATAL: password auth         │
│  │  failed'. Here's my code: [snippet]. I've checked         │
│  │  the password is correct in my .env file."                │
│  │ ✓ Good: Includes error, code, and prior attempt           │
│  └──[View Response]                                          │
│                                                              │
│  PROMPT STATISTICS                                           │
│  • Average word count: 48                                    │
│  • Questions asked: 75%                                      │
│  • Included code context: 58%                                │
│  • Included error messages: 33%                              │
│  • Paste detected: 8% (1 prompt)                             │
└──────────────────────────────────────────────────────────────┘
```

### 5.4 — Frontend components needed

- [x] `CategoryBreakdown` — expandable section with individual metrics, scores, and explanations (in Results tab)
- [x] `FitAnalysisCard` — displays CV-job match with skills lists and concerns (CV & Fit tab)
- [x] `PromptLogItem` — single prompt with metadata, quality indicators, and per-prompt score (AI Usage tab)
- [x] `PromptTimeline` — visual timeline of prompt activity across the session (Timeline tab)
- [x] Update existing radar chart to use 8 categories instead of 12 individual fields
- [x] Update summary card to show 0-100 score with new recommendation badges

---

### 5.5 — Candidate comparison + score glossary UX

- [ ] Add candidate-vs-candidate comparison entry point in Candidate Detail
- [ ] Add radar overlay toggle (`single`, `overlay`, `side-by-side`)
- [ ] Add metric comparison table with deltas and confidence notes
- [ ] Add chart dimension tooltip content sourced from a central score-dimension glossary file
- [ ] Ensure keyboard-accessible and mobile-safe fallback (non-hover glossary drawer)

### 5.6 — Cost and model controls

- [ ] Add model selection config per environment (`test/staging/prod`) with cheapest default in non-prod
- [ ] Add cost ledger events for Claude/E2B/email/storage and aggregate into per-assessment + per-tenant summaries
- [ ] Add cost dashboard cards and alert thresholds (daily spend, cost per completed assessment, anomalies)

## IMPLEMENTATION ORDER

```
Week 1:
  Phase 1 (CV, Job Spec, Document Infrastructure)
  Phase 3.4 (Fix backend→frontend data flow so existing UI works)

Week 2:
  Phase 3.1-3.3 (Rebuild scoring engine to 30+ metrics in 8 categories)
  Phase 3.5 (Score explanations)

Week 3:
  Phase 2 (CV-to-Job-Spec matching with Claude)
  Phase 5 (Comprehensive scoring dashboard)

Week 4:
  Phase 4 (S3 storage, async scoring, error handling, reminders)
  Testing & polish
```

**Total estimated effort**: ~4 weeks of focused development

---

## GUARDRAILS

### Scoring is heuristic-first
- **Trigger**: Importing ML/HuggingFace libraries for scoring
- **Rule**: STOP. Use regex and pattern matching. The only Claude call is for CV-job matching (one call per assessment at submission). All other scoring is pure heuristic. HuggingFace/ML is V2.

### Log everything in assessments
- **Trigger**: Any Claude interaction code
- **Rule**: MUST capture ALL fields: message, response, code_before, code_after, timestamps, tokens, word_count, paste_detected, browser_focused. This is the core data asset.

### Check existing code first
- **Trigger**: Starting any new feature
- **Rule**: Review existing implementation before creating new files. The codebase has many features already built (auth, task CRUD, candidate CRUD, assessment flow, scoring, email). Don't rebuild what exists.

### Keep frontend in App.jsx for now
- **Trigger**: Creating new component files
- **Rule**: The entire frontend is in `App.jsx` (3773 lines). For MVP, keep adding to it. Decomposition is V2. Only split out truly reusable components (scoring display components are OK).

### Single Claude call per assessment
- **Trigger**: Multiple Claude scoring calls
- **Rule**: CV-job match = ONE Claude call at submission. No other Claude calls for scoring. Keep costs low and scoring fast.

---

## CORE PRODUCT VALIDATION CHECKLIST

- [ ] **Assessment workspace completeness:** candidate sees full task/scenario/repo context before first prompt.
- [ ] **Telemetry completeness:** every interaction event is captured with enough granularity for replay and scoring audits.
- [ ] **Scoring completeness:** each category + dimension has clear rubric logic, explanations, and frontend visibility.
- [ ] **CV↔Job Spec baseline fit:** maintain low-cost LLM fit scoring; optionally add embedding similarity as fallback/validation.
- [ ] **Model tiering controls:** cheapest model default for test/staging, explicit override path for higher-quality production scoring.
- [ ] **Cost monitoring:** per-assessment and per-tenant cost attribution across LLM, sandbox, storage, and comms providers.
- [ ] **Additional high-leverage checks:** prompt-injection resilience, fairness/drift review, and deterministic score replay tooling.

---

## SUCCESS CRITERIA

MVP is complete when:

1. [x] Business can register and login
2. [x] Business can create tasks with starter code and tests
3. [x] Business can add candidates with CV and job spec uploads
4. [x] CV and job spec text is extracted and stored
5. [x] System sends assessment invitation email
6. [x] Candidate can access unique assessment link
7. [x] Candidate uploads CV (if not already) before starting
8. [x] Candidate can code in Monaco editor with E2B sandbox
9. [x] Candidate can chat with Claude (all interactions logged with full metadata)
10. [x] Candidate can run tests and submit
11. [x] System calculates 30+ scoring metrics across 8 categories on submission
12. [x] System runs CV-job match analysis (one Claude call) on submission
13. [x] Business dashboard shows full score breakdown with radar chart
14. [x] Each scoring element is displayed with score + explanation
15. [x] Business can view CV-Job fit analysis (matching skills, missing skills, concerns)
16. [x] Business can view prompt log with per-prompt quality indicators
17. [x] Fraud flags are detected and displayed
18. [x] Files are stored durably (S3, not local filesystem)
19. [ ] At least one working task exists for pilot testing

---

## ARCHITECTURE REFERENCE

```
Backend:  FastAPI + SQLAlchemy + PostgreSQL
          Components: app/components/{assessments,auth,candidates,scoring,integrations,notifications,...}
          Services:   app/services/{claude,e2b,email,scoring,document,fit_matching}_service.py
          Platform:   app/platform/{config,database,security,middleware,logging}.py

Frontend: React 18 + Tailwind CSS + Monaco Editor + Recharts
          Single-file: src/App.jsx (now reduced; still primary composition surface)
          API client: src/lib/api.js
          Assessment: src/components/assessment/{AssessmentPage,ClaudeChat,CodeEditor}.jsx

Feature flags: MVP_DISABLE_STRIPE, MVP_DISABLE_WORKABLE, MVP_DISABLE_CELERY,
               MVP_DISABLE_CLAUDE_SCORING, MVP_DISABLE_CALIBRATION, MVP_DISABLE_PROCTORING
```

---

*This plan tracks product scope; active hardening execution lives in `RALPH_TASK.md`.*
