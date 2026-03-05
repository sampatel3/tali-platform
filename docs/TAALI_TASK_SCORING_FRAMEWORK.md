# TAALI Task & Scoring Framework
**Version:** 2.0  
**Supersedes:** `TASK_DESIGN_FRAMEWORK.md` and `TASK_FRAMEWORK_COMPREHENSIVE.md`  
**Last updated:** 2026-03-05

---

## Purpose

This document is the single authoritative guide for designing TAALI assessment tasks. It is tightly coupled to the scoring engine (`scoring_core.py`, `analytics.py`, `rules.py`) so that every task design decision produces measurable hiring signal.

The central premise: **a task that cannot produce differentiated scores across the 8 scoring categories is not generating signal — it is generating noise.** Every design decision in this document is traceable to a specific line in the scoring engine.

---

## Part 1: Scoring Engine Reference

Before designing any task, understand exactly what the engine measures. All scores are 0–10 per category, combined into a final 0–100 score using fixed category weights.

### 1.1 Category Weights

```
task_completion    0.20  (highest — delivery matters)
independence       0.20  (highest — reading before prompting)
prompt_clarity     0.15
context_provision  0.15
utilization        0.10
communication      0.10
approach           0.05
cv_match           0.05  (excluded from weighted score when no CV is present)
```

### 1.2 What Each Category Actually Measures

**task_completion (20%)**  
`_score_task_completion()` in scoring_core.py  
- `tests_passed_ratio`: `tests_passed / tests_total × 10`  
- `time_compliance`: 10 if within limit, 7 if ≤125%, 4 if ≤150%, 1 otherwise  
- `time_efficiency`: 9 if ≤50% of limit used, 8 if ≤80%, 7 if ≤100%, 4 if ≤125%, 2 otherwise  
- Category score = average of the 3 metrics  
→ **Task design requirement:** Tests must actually pass when the correct fix is applied. 4–9 failing tests in the baseline repo.

**independence (20%)**  
`_score_independence()` in scoring_core.py  
- `first_prompt_delay`: Uses `min_reading_time_seconds` from `task_scoring_hints`
  - `top_threshold = max(60, min_reading_time_seconds)` → 10/10
  - `strong_threshold = max(60, round(top_threshold × 0.6))` → 8/10
  - ≥60s → 5/10, ≥30s → 3/10, <30s → 1/10
- `prompt_spacing`: ≥120s avg gap → 9/10, ≥60s → 7/10, ≥30s → 5/10, <30s → 2/10
- `prompt_efficiency`: prompts per test passed (≤1.5 → 10/10, ≤3 → 7/10, ≤5 → 5/10, >5 → 2/10)
- `token_efficiency`: tokens per test (≤500 → 10/10, ≤1000 → 8/10, ≤2000 → 6/10)
- `pre_prompt_effort`: rate of code changes before prompting  
→ **Task design requirement:** Always set `min_reading_time_seconds: 300` (or 360 for rich repos). The task must reward reading — candidates who skip docs produce worse first prompts.

**prompt_clarity (15%)**  
`_score_prompt_clarity()` in scoring_core.py  
- `prompt_length_quality`: sweet spot 20–150 words (penalises <10 words)
- `question_clarity`: % of prompts containing `?`
- `prompt_specificity`: % of non-vague prompts (see VAGUE_PATTERNS below)
- `vagueness_score`: inverse of vague prompt count  

**VAGUE_PATTERNS that penalise candidates** (defined in `rules.py`):
```
"fix it", "not working", "doesn't work", "help"
"make it work", "get the tests passing", "make this work"
"write the whole solution", "implement everything"
"just make/do/fix/build/write/implement"
"rewrite everything", "start over"
```
→ **Task design requirement:** Name specific functions, files, and failure modes. If a candidate can fix the issue with a vague prompt, the task hasn't been designed to reward specificity.

**context_provision (15%)**  
`_score_context_provision()` in scoring_core.py — uses `_extract_prompt_metadata()` to infer fields from prompt text  

Signals detected automatically from prompt content:
| Signal | How the engine detects it |
|--------|--------------------------|
| `code_snippet_included` | ``` in prompt OR 4-space indented block |
| `error_message_included` | "traceback", "error:", "exception", "failed", "assert", "stack trace", "SyntaxError", "TypeError", "ValueError" |
| `line_number_referenced` | "line \d+" or ":\d+:\d+" |
| `file_reference` | src/, tests?/, backend/, .py/.js/.json/.yml/.yaml/.md filenames |
| `references_previous` | "I tried", "I've tried", "I attempted", "expected X but got Y", "already X without success" |
| `retry_after_failure` | "retry", "tried again", "after it failed", "failed previously" |

→ **Task design requirement:** Diagnostics must contain specific, quotable artifacts — transaction IDs, error codes, function names with .py extension, line-specific error messages. These are what candidates need to paste into prompts to score well.

**utilization (10%)**  
`_score_utilization()` in scoring_core.py  
- `post_prompt_changes`: % of prompts followed by code changes (code_diff_lines_added/removed)
- `wasted_prompts`: inverse of prompts with zero code change
- `iteration_quality`: rate of `references_previous` + `retry_after_failure`  
→ **Task design requirement:** Multiple interdependent failure modes force iterative prompting with natural references to prior attempts.

**communication (10%)**  
`_score_communication()` in scoring_core.py  
- `grammar_score`: checks for lowercase 'i', double spaces, no end punctuation
- `readability_score`: sentence length sweet spot 10–20 words → 9/10
- `tone_score`: penalises unprofessional patterns and filler words (um, uh, like, basically, actually, just, really, very)  
→ **Task design requirement:** Scenario must require a written stakeholder summary. Professional writing quality is the signal.

**approach (5%)**  
`_score_approach()` in scoring_core.py  

DEBUGGING_PATTERNS (presence + reasoning depth scores):
```
print, log, debug, console.log
error, exception, traceback, stack
step by step, one at a time, isolat(e)
hypothesis, theory, suspect, might be
```
DESIGN_PATTERNS:
```
architect, structure, design, pattern
tradeoff, trade-off, pros and cons, alternative
scalab(le), maintain, extend, modular
edge case, corner case, what if
performance, efficiency, complexity
```
`_score_reasoning_depth()` gives 3/3 for hypothesis + test plan, 2/3 for concrete file grounding, 1/3 for partial specificity.  
→ **Task design requirement:** Scenario must ask prioritisation/design questions ("What blocks launch now?" / "What should you fix first?") that naturally elicit hypothesis and tradeoff language.

### 1.3 Fraud Detection Flags (caps score at 50)

Defined in `_detect_fraud()` in scoring_core.py and INJECTION_PATTERNS/VAGUE_PATTERNS in `rules.py`:

| Flag | Trigger |
|------|---------|
| `first_prompt_within_30_seconds` | First prompt < 30s after assessment start |
| `suspiciously_fast` | Entire assessment < 5 minutes AND tests pass |
| `solution_dump_detected` | Single prompt >500 words AND >3 function definitions |
| `injection_attempt` | "ignore previous instructions", "write the complete solution for me", "give me the full working answer", "no restrictions" |
| `external_paste_detected` | Paste event with >400 characters |
| `paste_ratio_above_70_percent` | >70% of prompts contain paste events |
| `zero_code_changes_after_3plus_prompts` | 3+ prompts with no resulting code diff |

→ **Task design requirement:** Tasks must not be solvable via a single solution-dump prompt. The `min_reading_time_seconds` flag also works as an anti-fraud signal — a candidate who prompts at second 5 is generating a fraud flag regardless of content.

---

## Part 2: Signal Map — Task Design Decisions to Engine Signals

This is the core of the framework. Every task design decision maps to a scoring signal.

| Task Design Decision | Scoring Signal | Category | Engine Location |
|---|---|---|---|
| Set `min_reading_time_seconds: 300` | `first_prompt_delay` | independence 20% | `_score_independence()` top_threshold |
| Include `.py` filenames in diagnostics | `file_reference` | context_provision 15% | `_extract_prompt_metadata()` |
| Include error text ("TypeError", "assert") in diagnostics | `error_message_included` | context_provision 15% | `_extract_prompt_metadata()` |
| Name specific functions in failing tests | `code_snippet_included` | context_provision 15% | Candidate pastes function name + context |
| Use specific IDs (TXN-9001, CUST-12) in incident logs | `error_message_included` + `reference_rate` | context_provision 15% | Candidate quotes specific artifacts |
| 4–9 intentionally failing tests | `tests_passed_ratio` | task_completion 20% | `_score_task_completion()` |
| Multiple interdependent failure modes | `iteration_quality`, `references_previous` | utilization 10% | ATTEMPT_PATTERNS |
| Scenario asks "what blocks us?" | `debugging_score` | approach 5% | DEBUGGING_PATTERNS |
| Scenario asks "what is the right design?" | `design_score` | approach 5% | DESIGN_PATTERNS |
| Require a written VP/Finance summary | `grammar_score`, `readability_score`, `tone_score` | communication 10% | `_score_communication()` |
| Layered complexity (easy → hard fixes) | `prompt_efficiency` | independence 20% | prompts per test passed |
| Disabled safety defaults (e.g. `pii_redaction_enabled: False`) | Candidate must enable for tests to pass | task_completion 20% | test assertions |

### Anti-patterns that collapse scoring signal

| Anti-pattern | Why it collapses signal |
|---|---|
| Thin diagnostic docs with no specific artifact names | `context_provision` is uniformly low — no differentiation |
| `min_reading_time_seconds` not set | Engine uses default 60s threshold — reading signal is weak |
| Tests that pass when a hardcoded value is returned | `tests_passed_ratio` is gamed — no task_completion signal |
| Single obvious fix | No prioritisation signal — every candidate looks the same |
| Scenario that doesn't ask for a written output | `communication` scores candidates on prompts only — too thin |
| Repo that can be fully solved in <20 minutes | No time pressure signal — independence scores cluster at top |

---

## Part 3: Task Design Protocol

### 3.1 Business Pressure Framing

Every task must simulate a real engineering situation with a named stakeholder, specific consequences, and a time-bound output ask.

**Required elements in the stakeholder message:**
- Named senior role (VP of Engineering, product director — not "my manager")
- Specific business consequence if unresolved (finance close blocked, executive preview in 7 days)
- Named failure modes the stakeholder is aware of (these should match the incident docs exactly)
- Explicit scope limiter ("I do not need a hero rewrite")
- Three-question output structure: what blocks us now / what can we ship first / what did you fix

### 3.2 Timebox Calibration

30 minutes, calibrated to these phases:
- 0–8 min: Reading docs, mapping failures to incident symptoms
- 8–20 min: Fixing 2–3 highest-priority failures
- 20–28 min: Re-running tests, assessing residual risk
- 28–30 min: Writing the stakeholder summary

**A well-calibrated task:** strong candidate fixes 3–5 of 7–9 failing tests in the timebox. If all tests are fixable in 20 minutes, there is no differentiation.

### 3.3 Calibration Prompt

A 2-minute warmup before the main task, completable with a single well-formed prompt. Must require the candidate to provide context (table schema, constraints, success criteria) to establish a pre-task baseline for their prompting behaviour.

---

## Part 4: Repository Design

### 4.1 Structure Requirements

- Bootstrap cleanly via `workspace_bootstrap` with no ambient credentials or packages
- `requirements.txt`: `pytest>=8.0.0` only (unless the role genuinely requires additional packages)
- `.gitignore`: `.venv/`, `.pytest_cache/`, `__pycache__/`
- Test runner: `./.venv/bin/python -m pytest -q --tb=short` (use `--tb=short` so pytest output contains quotable error lines)

**Critical:** Use `--tb=short` not `--tb=no` in the test runner. Short tracebacks produce the error text (TypeError, AssertionError with specific values) that candidates can paste into prompts, directly raising `error_message_included` scores.

### 4.2 File Distribution

| File type | Count | Purpose |
|---|---|---|
| README.md | 1 | Why this repo exists; local workflow |
| Incident/risk docs | 1–2 | Diagnostics with specific IDs, error codes, function names |
| Architecture doc | 1 | System shape, constraints, data contract expectations |
| Source modules | 3–6 | The broken production code — named functions in named .py files |
| Test modules | 3–5 | Failing expectations, named after incident symptoms |
| requirements.txt | 1 | Minimal |

**Documentation quality requirement:** Every diagnostic file must contain at least:
- One specific transaction/entity ID (e.g. `TXN-9001`, `cust-42`)  
- One specific error text that would appear in a pytest traceback  
- One specific function name in a `.py` file  
These are what candidates need to quote in prompts to score well on `context_provision`.

### 4.3 Intentional Failure Categories

| Category | Example | Fixes to |
|---|---|---|
| Stub function | `return []` or `return list(records)` | Real logic |
| Incorrect logic | Condition that ignores a parameter | Condition that checks it |
| Missing config | `pii_redaction_enabled: False` | `True` (enabled by default) |
| Incomplete mapping | TYPE_MAPPING missing 'double', 'integer' | Add entries |
| Missing error handling | No try/except around LLM call | Graceful fallback |

Each failing test must map to a named symptom in the incident docs. If a test fails but the failure is not in the diagnostics, remove it.

### 4.4 Test Count Guidelines

| Timebox | Failing tests | Strong candidate fixes |
|---|---|---|
| 30 minutes | 7–9 | 3–5 |
| 45 minutes | 9–12 | 5–7 |

Distribute across difficulty:
- **Easy (early wins, <5 min each):** Missing config flags, type mapping gaps, single-line logic fixes
- **Medium (5–10 min each):** Stub functions requiring real business logic
- **Hard (10–15 min each):** Interdependent failures, system-level understanding required

---

## Part 5: Rubric Engineering

### 5.1 Structure

Exactly 5 dimensions, weights summing to 1.0. Each dimension has:
- `weight`
- `criteria.excellent`, `criteria.good`, `criteria.poor` (observable behaviour, not abstract quality)
- A corresponding entry in `role_alignment.jd_to_signal_map`

### 5.2 Weight Allocation

| Weight | Role |
|---|---|
| 0.20–0.24 | Core technical judgment — the primary signal this task exists to produce |
| 0.18–0.22 | Important technical dimension |
| 0.16–0.20 | Execution quality |
| 0.14–0.18 | Communication / stakeholder framing |

Never use equal weights. Weight allocation should reflect what the hiring manager actually cares about for this specific role.

### 5.3 Writing Criteria

**Excellent:** Names the specific decision sequence or artifact. "Reads diagnostics/audit_findings.md before editing code, identifies schema drift and duplicate retries as the blockers, and explains why performance tuning is secondary."

**Good:** Correct direction with a named gap. "Finds the main incident themes with minor prioritisation gaps."

**Poor:** Names a specific failure mode likely in weak candidates. "Jumps into code without reading the incident docs."

---

## Part 6: JD-to-Task Generation Protocol

This section defines how to use a job specification to generate a bespoke task. The workflow is Claude-assisted, human-approved.

### Step 1: Role Analysis (input: job spec)

Extract and classify JD requirements into three tiers:
- **Tier 1 — Must test:** Core technical skills the role cannot function without
- **Tier 2 — Should test:** Differentiating skills that separate strong from adequate
- **Tier 3 — Cannot test in 30 min:** Infrastructure, deployment, platform provisioning — list in `must_not_cover`

### Step 2: Incident Scenario Selection

Choose a business scenario that:
- Creates genuine pressure for Tier 1 skills
- Has a named business consequence (finance close, customer preview, compliance audit)
- Can be represented in a local Python repo without cloud credentials

### Step 3: Rubric Mapping

Map each Tier 1 and Tier 2 requirement to a rubric dimension and a repo artifact:

```
JD requirement → task_artifact (specific file + function) → rubric_dimension → weight
```

No JD requirement should appear in `must_cover` if you cannot fill in `task_artifact`.

### Step 4: Repo Design

For each rubric dimension, design at least one intentional failure that:
- Lives in a named function in a named `.py` file
- Is described by name in the incident docs
- Has a corresponding failing test

### Step 5: Scoring Signal Audit

Before finalising the task, verify:

| Scoring category | Task provides signal because... |
|---|---|
| task_completion | Tests fail for meaningful reasons and pass when correctly fixed |
| independence | `min_reading_time_seconds: 300` set; docs reward reading |
| prompt_clarity | Named functions/files give candidates specific things to ask about |
| context_provision | Diagnostics contain quotable error text, IDs, and .py filenames |
| utilization | Multiple interdependent failures require iterative prompting |
| communication | Scenario explicitly requires a written stakeholder summary |
| approach | Scenario asks prioritisation/design questions |

### Step 6: Claude Prompt Template for Task Generation

When generating a new task, provide Claude with:

```
Role: [exact job title from JD]
JD requirements (Tier 1): [list]
JD requirements (Tier 2): [list]
Must not cover: [list]
Business scenario: [brief description]
Scoring signal audit (complete before handing to Claude):
  - What is the named stakeholder and deadline?
  - What are the 3 incident symptoms in the diagnostics?
  - What are the 7-9 failing test names?
  - What quotable error artifacts appear in the incident docs?
Framework reference: TAALI_TASK_SCORING_FRAMEWORK.md v2.0
```

---

## Part 7: Quality Gates

A task is not ready for seeding until all of the following are true.

### Automated checks

- [ ] Schema validation passes (`python3 scripts/validate_task_specs.py`)
- [ ] `workspace_bootstrap` succeeds in a clean directory
- [ ] Tests collect without import errors
- [ ] Baseline repo has 7–9 failing tests
- [ ] No failures caused by missing imports or syntax errors
- [ ] `--tb=short` in test_runner command (produces quotable error text)

### Signal audit checks

- [ ] `min_reading_time_seconds: 300` is set in `scoring_hints`
- [ ] Every failing test maps to a named symptom in incident docs
- [ ] Diagnostic docs contain at least one: specific ID, error text string, .py function name
- [ ] Scenario explicitly asks for a written stakeholder summary
- [ ] Scenario includes at least one prioritisation question ("what blocks us?") and one design question
- [ ] Every rubric dimension has a `task_artifact` entry in `jd_to_signal_map`
- [ ] Weights do not use equal allocation across all 5 dimensions
- [ ] `must_not_cover` lists at least 2 genuine scope exclusions

### Human pilot checks (`human_testing_checklist`)

- [ ] `candidate_clarity: true` — pilot candidate understood the task without clarification
- [ ] `repo_boot_ok: true` — workspace bootstrapped in a clean environment
- [ ] `tests_collect_ok: true` — tests collected without errors
- [ ] `baseline_failures_meaningful: true` — failing tests correspond to incident symptoms
- [ ] `rubric_matches_role: true` — confirmed against live job spec
- [ ] `timebox_realistic: true` — pilot candidate fixed 3–5 of 7–9 tests in 30 minutes

---

## Appendix A: Scoring Engine Quick Reference

| Engine location | What it computes | Task design lever |
|---|---|---|
| `scoring_core.py: _score_independence()` | first_prompt_delay via top_threshold | `scoring_hints.min_reading_time_seconds` |
| `scoring_core.py: _extract_prompt_metadata()` | Infers code/error/file signals from prompt text | Quality of diagnostic docs |
| `scoring_core.py: _is_vague_prompt()` | Penalises "fix it", "make it work" etc | Task complexity forces specificity |
| `scoring_core.py: _detect_fraud()` | Flags fast completions, dumps, injection | 30-min timebox + complex failures |
| `scoring_core.py: _score_approach()` | Detects debugging + design vocabulary | Scenario asks prioritisation questions |
| `analytics.py: compute_time_to_first_prompt()` | Flags "rushed" if <30s, "deliberate" if >300s | `min_reading_time_seconds` |
| `rules.py: VAGUE_PATTERNS` | Patterns that trigger specificity penalties | Named artifacts in repo docs |
| `rules.py: INJECTION_PATTERNS` | Patterns that trigger fraud flags | Task complexity prevents solution dumps |

## Appendix B: Canonical Task Reference

| Property | `ai_eng_genai_production_readiness` v2 | `data_eng_aws_glue_pipeline_recovery` v2 |
|---|---|---|
| Role | AI Engineer (Banking/GenAI) | AWS Glue Data Engineer |
| Business pressure | Executive preview + compliance audit in 7 days | Finance close tomorrow |
| Top rubric weight | system_risk_assessment 0.24 | situational_assessment 0.22 |
| Deliberate failures | PII in prompts; no RAG grounding; no audit log; no degraded mode | Quality gate stub; dedup no-op; bookmark ignores quality; type mapping gaps; loading method hardcoded; contract validation stub |
| Baseline failing tests | 7 | 9 |
| min_reading_time_seconds | 300 | 300 |
| Test runner | `pytest -q --tb=short` | `pytest -q --tb=short` |
