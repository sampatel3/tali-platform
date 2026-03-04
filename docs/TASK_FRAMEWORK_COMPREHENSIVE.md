# TAALI Task Framework — Comprehensive Design Guide

**Version:** 1.0
**Last updated:** 2026-03-03
**Basis:** Deep review of both canonical tasks (`ai_eng_genai_production_readiness`, `data_eng_aws_glue_pipeline_recovery`), scoring engine (`scoring_core.py`, `rules.py`), backlog analysis (`RALPH_TASK.md`), and the runtime spec contract.

---

## Purpose

This document is the definitive guide for designing new TAALI assessment tasks. It covers the design principles, scenario architecture, repository design, rubric engineering, scoring alignment, role traceability, test philosophy, and quality gates. Every new task must meet every requirement in this document before it is considered ready for human pilot testing.

The central premise: **TAALI tasks measure how a candidate collaborates with AI to solve real engineering problems. They do not measure memorised knowledge, speed-typing, or code quantity.** This distinction shapes every design decision.

---

## Section 1: Core Design Principles

These six principles are non-negotiable. Violating any of them produces a task that does not generate trustworthy signal.

### 1.1 Business pressure, not a puzzle

The task must simulate a real engineering situation that carries genuine business consequences. The framing should make clear why it matters — a finance close is blocked, a launch is threatened, a customer is at risk. Contrived puzzles ("implement a binary search tree") do not give the candidate a reason to prioritise, communicate, or exercise judgment. Business pressure is what forces the same decisions a real engineer must make.

*Both canonical tasks do this well: the Glue task uses a finance-close deadline; the GenAI task uses an executive preview in 7 days. The framing is specific, credible, and consequential.*

### 1.2 Prioritisation is the core signal

Thirty minutes is not enough time to fix everything. That is not a bug — it is the design. A task where every failure can be fixed in the timebox produces no prioritisation signal. A strong candidate will:
- Identify what blocks the immediate business outcome
- Fix the highest-priority items
- Explicitly name what they are leaving for later

A task must always have more failure modes than a candidate can fix in the timebox. The choice of what to fix is at least as important as the fix itself.

### 1.3 The repo is a context trap for bad prompters

The repo must be designed so that a candidate who reads the documentation before touching code has a decisive advantage over one who starts prompting immediately. This creates the reading signal, which feeds directly into the `independence` scoring category (20% weight). Specifically:
- The RISKS.md / RUNBOOK.md / diagnostics contain the full picture of what is wrong
- A candidate who reads these files first will prompt Claude with precise, grounded context
- A candidate who skips them will send vague prompts and receive less useful responses

The repo must reward reading. This means the failure modes visible in the docs must align exactly with the failing tests.

### 1.4 Deliberate failure shape

Every failing test must fail for a meaningful business reason, not due to a missing import, a syntax error, or an environment setup problem. The baseline repo is designed to be runnable — `workspace_bootstrap` installs everything — but logically broken in ways that reflect the incident.

The candidate must diagnose the failure, not just debug setup noise.

*In the Glue task: `quality_gate()` always returns `passed: True`, `deduplicate_records()` returns every record unchanged, `should_advance_bookmark()` ignores the quality result. Each of these is a direct representation of the audit findings.*

### 1.5 Single source of truth

The canonical task lives in `backend/tasks/` as a JSON file. Nothing else in the codebase defines task behavior. The repo structure, test files, scenario text, evaluation rubric, and role alignment are all part of the same JSON. The CRUD UI must never be the canonical source.

### 1.6 Role traceability, not generic difficulty

Every rubric dimension must map to a specific line in the job specification for the target role. A task that measures "general programming skill" is not a TAALI task. A task that measures "recovers a Glue pipeline under finance-close pressure with correct loading behavior, idempotency, and audit-ready documentation" — and can point to the exact JD requirement it tests — is.

---

## Section 2: Scenario Architecture

### 2.1 The stakeholder message pattern

Every scenario must include a message from a named, senior stakeholder (VP of Engineering, product director, etc.) with a specific, time-bound request. This serves three functions:

1. It anchors the business pressure to a real person with real consequences.
2. It frames the three-question structure: *What blocks us now? What can we ship first? What did you fix?*
3. It tests written communication — can the candidate address this specific person with a response they could actually use?

**Required elements in the stakeholder message:**
- Named role (not "my manager")
- The immediate business consequence if this is not fixed
- The specific failure modes they are aware of (should match the incident docs)
- An explicit statement of what they do NOT want (e.g., "I do not need a hero rewrite")
- A clear ask with a time-bound output expectation

### 2.2 The mission statement

The scenario must end with a short `**Your mission:**` block that tells the candidate what they are being assessed on. This should name: the type of judgment being evaluated, the balance to strike (safety/delivery, correctness/speed, etc.), and the output form expected.

This is not just good candidate experience — it is honest. TAALI measures AI collaboration quality, and the candidate should know it. A candidate who knows they are being assessed on *how they work with Claude, not just what they produce* will behave differently and more naturally.

### 2.3 Embedded vs. hidden context

**All success-critical context must be visible in the scenario, the repo docs, or the task files.** A candidate must never fail a rubric dimension because they lacked information that was not given to them.

However, context that rewards reading can be distributed across multiple files. A strong candidate reads everything; a weak one prompts immediately. The distributed context structure is intentional:
- The stakeholder message gives the business framing
- The RISKS.md / RUNBOOK.md / diagnostics give the technical picture
- The failing tests confirm the specific failure modes
- The launch checklist / architecture doc gives the success criteria

### 2.4 Timebox design

The 30-minute timebox is calibrated to produce a genuine triage scenario. Design for these approximate phases:
- Minutes 0–8: Reading and diagnosis
- Minutes 8–20: Highest-priority fixes
- Minutes 20–28: Test re-run and assessment of what still blocks
- Minutes 28–30: Written summary

A task is well-calibrated if a strong candidate can fix 2–3 of the most critical failures but not all of them. A task that can be fully solved in 20 minutes produces no differentiation.

**Declare `"min_reading_time_seconds": 300` in `scoring_hints` for all complex tasks.** This is used by the scoring engine to calibrate the `first_prompt_delay` signal. For tasks where the repo is particularly rich (5+ files, detailed docs), increase to 360.

---

## Section 3: Repository Design

### 3.1 Structure principles

The repo must:
- Be executable in a clean workspace with no ambient packages, credentials, or hidden setup
- Have a single `requirements.txt` containing only `pytest>=8.0.0` (no extra dependencies unless the task genuinely requires them, and even then minimise)
- Include a `.gitignore` for `.venv/`, `.pytest_cache/`, `__pycache__/`
- Bootstrap successfully via `workspace_bootstrap` commands before the assessment starts

The test runner command must use the virtualenv explicitly: `./.venv/bin/python -m pytest -q --tb=no`.

### 3.2 File distribution

A task repo should have:

| Type | Count | Purpose |
|------|-------|---------|
| README.md | 1 | Why this repo exists, local workflow |
| Incident/Risk docs | 1–2 | Diagnostics, findings, runbook, or RISKS.md |
| Architecture doc | 1 | System shape, constraints, or checklist |
| Source modules | 3–5 | The broken production code |
| Test modules | 2–4 | The failing expectations |
| requirements.txt | 1 | Minimal dependencies |

**Do not include:**
- Commented-out solution code
- Hints embedded in code comments
- Files that have no role in the task story (padding adds noise, not signal)

### 3.3 The broken baseline

The source code must be syntactically correct and fully importable. Tests must collect successfully. Failures must occur at test execution time, triggered by logical bugs, not at import time.

**Categories of intentional failures:**
1. **Stub functions** — Functions that exist and are called but return trivially wrong values (e.g., `return []`, `return list(records)`)
2. **Incorrect logic** — Business logic that ignores a condition it should check (e.g., `should_advance_bookmark` ignores `quality_passed`)
3. **Missing type mappings** — TYPE_MAPPING that doesn't include common types, causing fallback behavior
4. **Disabled config flags** — Config defaults that disable safety features (e.g., `pii_redaction_enabled: False`)

**Each failing test should correspond directly to one incident symptom visible in the diagnostics/audit docs.** If a test fails but the failure is not traceable to the incident story, remove it.

### 3.4 The PII/safety trap (for GenAI tasks)

For tasks involving LLM prompt construction, the default config must have safety features disabled (e.g., `pii_redaction_enabled: False`, `require_human_review: False`). The failing test must verify that PII leaks into prompts when the feature is off. A strong candidate enables it; a weak one ignores it.

This creates a direct signal: candidates who protect sensitive inputs before optimising performance are demonstrating production readiness.

### 3.5 Documentation as signal

The repo documentation (README, RUNBOOK, diagnostics) is not just background material — it is part of the assessment. A candidate who reads it carefully before prompting will:
- Know exactly which files contain the bugs
- Know the business priority order
- Know what the evaluator considers success

Prompts that reference specific file names, line numbers, or error patterns from the diagnostics are rewarded by the scoring engine. The documentation quality directly affects the quality of AI prompts the task generates.

**Write documentation as you would write an internal engineering handover.** It should be specific, terse, and factually correct. Never write vague documentation in a task repo.

---

## Section 4: Rubric Engineering

### 4.1 Rubric structure

Every task must have exactly **5 rubric dimensions**. Each dimension must have:
- A `weight` (all 5 weights must sum to 1.0)
- `criteria.excellent`, `criteria.good`, `criteria.poor` descriptions
- A corresponding entry in `role_alignment.jd_to_signal_map`

### 4.2 Weight allocation principles

| Weight | Meaning |
|--------|---------|
| 0.20–0.24 | Core technical judgment for this role — the thing this task exists to measure |
| 0.18–0.22 | Important technical dimension — differentiates strong from mediocre |
| 0.14–0.20 | Execution quality — separates good from excellent |
| 0.14–0.18 | Communication and stakeholder framing — increasingly important for senior roles |

Weights should reflect what the hiring manager actually cares about for this role. A Data Engineer fixing a pipeline under audit pressure cares most about correctness and trust, then prioritization, then communication. An AI Engineer cares most about safety/risk assessment, then production readiness, then delivery judgment.

**Never set all 5 dimensions to equal weights.** Equal weights signal that the task designer has not thought about what the role actually requires.

### 4.3 Writing excellent/good/poor criteria

Each criterion level must describe observable behavior, not abstract quality. Avoid subjective language ("solid", "acceptable"). Use specific behaviors:

**Excellent:** Describes the best-case decision sequence. Should name the specific actions or artifacts the candidate produces. Example: *"Reads the diagnostics first, identifies schema drift, duplicate retries, and bookmark trust as the immediate incident drivers, and keeps performance tuning secondary."*

**Good:** Describes correct overall direction with a named gap. Example: *"Finds the main incident themes with minor prioritization gaps."*

**Poor:** Describes a specific failure mode that is likely in weak candidates. Example: *"Jumps into code without a coherent explanation of the failure pattern."*

The excellent criterion must be achievable in the timebox. If the excellent bar requires something that cannot realistically be done in 30 minutes, lower the bar or extend the timebox.

### 4.4 The five canonical rubric slots

These are the five slot types from which task designers should draw. Not every task needs all five — pick the five most relevant to the role:

| Slot | What it measures | Typical weight |
|------|-----------------|----------------|
| **Situational assessment / Risk identification** | Does the candidate read the context before acting? Do they correctly identify what is broken and why? | 0.20–0.24 |
| **Prioritisation judgment** | Does the candidate choose the right thing to fix first given the business constraint? | 0.18–0.22 |
| **Technical design** | Are the fixes credible and correct? Do they reflect production-grade thinking? | 0.18–0.22 |
| **Implementation quality** | Does the code actually fix the failing tests? Is it clear and maintainable? | 0.18–0.22 |
| **Communication clarity** | Can the candidate explain what they did, what remains broken, and what to do next, in terms a VP or Finance lead can act on? | 0.14–0.18 |

For GenAI/LLM tasks, substitute or augment with:

| Slot | What it measures |
|------|-----------------|
| **Safety guardrails** | Does the candidate protect sensitive inputs, gate high-stakes actions, and treat model uncertainty as a product risk? |
| **Grounding and retrieval judgment** | Does the candidate keep recommendations grounded in evidence? Do they avoid trusting unverified model output? |
| **Pragmatic delivery judgment** | Can they scope a narrow, defensible release? Do they explain what is out of scope for this timebox? |

---

## Section 5: Scoring Engine Alignment

TAALI's scoring engine produces scores across 8 dimensions. Task design must actively enable each dimension's signal. A task that scores all candidates identically on a given dimension is failing to provide signal for that dimension.

### 5.1 The 8 scoring dimensions and how tasks enable them

**task_completion (20% weight)**
Measures: test pass rate, time compliance, time efficiency.
*Task design requirement:* Tests must actually pass when the correct fix is made. The `test_runner` section must be correctly configured. Failing tests must be fixable in the timebox. This is the highest-weighted dimension — if tests never pass for anyone, 20% of the score is noise.

**prompt_clarity (15% weight)**
Measures: specificity of prompts, avoidance of vague language, structured question asking.
*Task design requirement:* The repo must give candidates specific things to ask about — function names, error messages, file paths, line numbers. A task with no specific named artifacts produces no specificity signal.

**context_provision (15% weight)**
Measures: inclusion of code snippets, error messages, file references, line numbers in prompts.
*Task design requirement:* Failing tests must produce specific, citable error output. The diagnostic files should contain specific error patterns (timestamps, error codes, transaction IDs) that a candidate can quote in prompts. The scoring engine detects `code_snippet_included`, `error_message_included`, `line_number_referenced` — the task must create natural opportunities for all three.

**independence (20% weight)**
Measures: time before first prompt, avoidance of solution dumps, self-correction after failed attempts.
*Task design requirement:* `min_reading_time_seconds: 300` must be set. The repo documentation must be rich enough that 5 minutes of reading genuinely changes the quality of the first prompt. The task must not be trivially solvable without reading. If the first prompt is inevitably "fix the tests", the independence signal is weak.

**utilization (10% weight)**
Measures: application of Claude's responses, follow-up specificity after receiving a response.
*Task design requirement:* The task should have layered complexity — fixes should unlock new problems to address. A candidate who applies Claude's first response effectively should be able to identify the next thing to fix. This is naturally produced by tasks with 3–4 interdependent failure modes.

**communication (10% weight)**
Measures: written clarity, professional tone, structural quality of written outputs.
*Task design requirement:* The scenario must require a written output (the stakeholder summary). This should be explicitly part of the mission statement. The candidate should know they are expected to produce a written handover or status report.

**approach (5% weight)**
Measures: debugging patterns (hypothesis formation, isolation, print/log statements) and design thinking (tradeoffs, edge cases, alternatives).
*Task design requirement:* The task must have genuine debugging challenges that reward systematic thinking over random guessing. The diagnostic files should model the kind of systematic thinking being rewarded (hypothesis → check → confirm).

**cv_match (5% weight)**
Measures: relevance of the candidate's background to the role.
*Task design requirement:* The `role_alignment.source_role_name` must match the actual job title. The `must_cover` list must accurately describe what the task tests. When no CV is uploaded, this dimension is excluded from the weighted score — the task must still produce a strong signal on the other 7 dimensions without relying on cv_match.

### 5.2 The reading signal — the most important indirect signal

The single clearest differentiator between strong and weak AI collaborators in the TAALI system is the time between assessment start and first prompt (`time_to_first_prompt_seconds`). A candidate who spends 5+ minutes reading the repo before their first prompt will:
- Include specific file names and function names in their first prompt
- Reference the exact failure mode described in the diagnostics
- Frame their ask in terms of the business problem, not just the code

A candidate who prompts within 30 seconds will produce vague, generic prompts that trigger `vagueness_score` penalties.

**Design tasks so that reading pays off.** The richness of the documentation — and the alignment between the docs and the failing tests — is the primary mechanism that differentiates reading candidates from non-reading candidates.

### 5.3 Scoring dimension traps to avoid

**Independence trap:** If the task can be solved by asking Claude "fix all the failing tests and explain what you changed," the independence signal collapses. The task must require judgment about *what* to fix, not just execution of fixing. Multi-failure-mode repos with a documented priority order create this naturally.

**Context provision trap:** If the repo has no specific, citable artifacts (no error messages, no transaction IDs, no specific function names), candidates cannot include them in prompts. Context provision scores will be uniformly low. Include specific, quotable artifacts in the diagnostics.

**Communication trap:** If the mission statement does not explicitly ask for a written summary, strong candidates who code well but communicate poorly will appear stronger than they are. Always require a written output in the final 2 minutes.

---

## Section 6: Role Alignment

### 6.1 Role alignment structure

The `role_alignment` block is mandatory and must contain:

| Field | Requirement |
|-------|-------------|
| `source_user_email` | The TAALI account email of the role owner |
| `source_role_name` | Exact job title from the live job posting |
| `source_role_identifier` | The Workable job ID (format: `workable:XXXXXXXX`) |
| `captured_at` | ISO 8601 timestamp when the alignment was captured |
| `must_cover` | 3–5 sanitized requirement statements extracted from the JD |
| `must_not_cover` | 2–4 things the task explicitly does NOT test (scope boundary) |
| `jd_to_signal_map` | Array mapping each JD requirement to a task artifact and rubric dimension |

### 6.2 The jd_to_signal_map is the heart of role alignment

Each entry must have:
- `job_requirement`: the exact requirement from the JD (paraphrased, not the raw JD text)
- `task_artifact`: the specific file, function, or document in the task repo that tests this requirement
- `rubric_dimension`: the exact key from `evaluation_rubric`

If you cannot fill in `task_artifact` for a JD requirement, the task does not test that requirement. Either add an artifact that tests it or remove the requirement from `must_cover`.

### 6.3 must_not_cover is as important as must_cover

A task that tries to test infrastructure provisioning, model training, and frontend code all at once tests nothing well. The `must_not_cover` list signals to the hiring manager what the task is not a signal for, so they do not over-interpret the score.

Keep tasks focused. A backend data pipeline task should not test TypeScript skills. An AI engineer task should not test Kubernetes configuration.

---

## Section 7: Candidate Experience Design

### 7.1 expected_candidate_journey

This section encodes the ideal candidate behavior as a 4-phase timeline. It is used by evaluators to calibrate manual rubric scoring and by the platform to generate interview guide recommendations. Write it as an observational description, not instructions.

| Phase | Duration | What the ideal candidate does |
|-------|----------|------------------------------|
| `first_8_minutes` | 0–8 min | Reads all docs, maps the incident, forms a hypothesis |
| `minutes_8_to_20` | 8–20 min | Executes the 2–3 highest-priority fixes with Claude |
| `minutes_20_to_28` | 20–28 min | Runs tests, assesses residual risk, scopes what remains |
| `final_2_minutes` | 28–30 min | Writes the stakeholder summary |

### 7.2 interviewer_signals

`strong_positive` (5–6 items): Specific, observable actions that distinguish an excellent candidate. These should be things a human evaluator watching the timeline tab would recognise. Each item should be behavioural, not evaluative ("reads RISKS.md before editing code", not "demonstrates good judgment").

`red_flags` (5–6 items): Specific failure modes that indicate a candidate is not ready for this role. These should be actionable — something the evaluator could point to a specific moment in the assessment timeline and say "here, this happened."

### 7.3 calibration_prompt

The calibration prompt is a 2-minute warmup before the main task that establishes a baseline for this candidate's AI collaboration style with zero task pressure. For a data engineering task, it should involve a simple data validation or query scenario. For an AI engineering task, it should involve a small prompt engineering request.

The calibration prompt must:
- Be completable in 2 minutes with a single well-formed prompt
- Require the candidate to provide context (table schema, assumptions, success criteria)
- Have a clear, verifiable success condition
- Not share content with the main task repo

The purpose is to answer: how does this candidate prompt Claude when there is no time pressure and the problem is well-defined? This calibration score becomes a baseline for interpreting the main task scores.

---

## Section 8: Test Design Philosophy

### 8.1 Tests are the ground truth of the task story

Every failing test must be traceable to a specific symptom in the incident story. The test file names and test function names should be readable enough that a candidate can map them to the diagnostics without needing to run them.

**Test naming convention:**
- `test_does_not_advance_bookmark_when_quality_gate_fails` — maps to the audit finding about bookmark advancement
- `test_redacts_ticket_pii_before_prompt` — maps to the security finding about PII in prompts
- `test_deduplicates_retries_by_latest_record` — maps to the duplicate-inflation finding

### 8.2 Test correctness contract

Before seeding a task, every test must be manually verified to:
1. Collect without import errors
2. Fail against the baseline repo for the right reason
3. Pass when the correct fix is applied
4. Not pass when a superficial or incorrect fix is applied

A test that can be made to pass by returning a hardcoded value is a bad test. Tests must validate behavior, not output shape.

### 8.3 Number of failing tests

For a 30-minute task, the baseline repo should have **4–8 failing tests** across 2–4 test files. Fewer than 4 tests produces insufficient completion signal. More than 8 tests is overwhelming for a timed task and may push candidates toward solution dumps.

Distribute tests across multiple files to create a layered discovery experience:
- Some tests should be fixable early (building confidence)
- Some should only be fixable after understanding the harder failure
- At least one test should require the candidate to read the incident docs to understand what "correct" means

### 8.4 Schema and type tests

For data engineering tasks, include at least one test that validates type mapping or schema evolution. These tests are quick wins that reward reading the schema documentation and demonstrate familiarity with data contract principles.

---

## Section 9: JSON Spec Contract

Every canonical task JSON must include the following top-level keys. Missing keys will fail schema validation.

| Key | Type | Required |
|-----|------|----------|
| `task_id` | string | Yes |
| `name` | string | Yes |
| `role` | string (`ai_engineer`, `data_engineer`, `backend_engineer`, etc.) | Yes |
| `duration_minutes` | integer | Yes |
| `calibration_prompt` | string | Yes |
| `scenario` | string (markdown) | Yes |
| `repo_structure` | object | Yes |
| `evaluation_rubric` | object (5 dimensions) | Yes |
| `expected_candidate_journey` | object | Yes |
| `interviewer_signals` | object | Yes |
| `scoring_hints` | object | Yes |
| `test_runner` | object | Yes |
| `workspace_bootstrap` | object | Yes |
| `role_alignment` | object | Yes |
| `human_testing_checklist` | object | Yes |

The `human_testing_checklist` must be updated to `true` only after a human pilot run has confirmed each field.

---

## Section 10: Quality Gates

A task is not ready for production seeding until all of the following are confirmed:

### Automated checks (`python3 scripts/validate_task_specs.py`)
- [ ] Schema validation passes
- [ ] `workspace_bootstrap` succeeds in a clean directory
- [ ] Tests collect without import errors
- [ ] Baseline repo has at least 4 failing tests
- [ ] No test failures are caused by missing dependencies or import-time crashes

### Manual review checks
- [ ] Every failing test maps to a named symptom in the incident docs
- [ ] Every rubric dimension maps to a specific JD requirement in `jd_to_signal_map`
- [ ] The scenario is readable in under 90 seconds and the mission is clear
- [ ] The stakeholder message names a specific person, consequence, and output expectation
- [ ] The timebox is realistic: a strong candidate can fix 2–3 failures in 30 minutes, not all of them
- [ ] `min_reading_time_seconds: 300` is set (or higher for richer repos)
- [ ] The `calibration_prompt` is specific to the role domain, completable in 2 minutes

### Human pilot checks (`human_testing_checklist`)
- [ ] `candidate_clarity: true` — A pilot candidate understood the task without clarification
- [ ] `repo_boot_ok: true` — The workspace bootstrapped successfully in a clean environment
- [ ] `tests_collect_ok: true` — All tests collected without errors
- [ ] `baseline_failures_meaningful: true` — Failing tests clearly correspond to the incident story
- [ ] `rubric_matches_role: true` — The rubric dimensions were confirmed against the live job spec
- [ ] `timebox_realistic: true` — The pilot candidate could fix 2–3 issues in 30 minutes but not all

---

## Section 11: Anti-patterns

These are the failure modes most likely to produce a task that does not generate trustworthy signal. Check every new task against this list.

### Task design anti-patterns

**Single correct answer.** If there is one obviously right fix and the task is just about whether the candidate finds it, there is no judgment signal. Tasks must have multiple valid approaches and require prioritisation.

**Speed-first design.** If the task rewards the fastest candidate rather than the most thoughtful one, the AI collaboration signal collapses. Prioritisation and reading time are the primary signals — not raw execution speed.

**Ambient context dependency.** If the task requires knowledge that is not in the repo (AWS console access, external documentation, specific industry knowledge beyond the incident), it is not fair and will not produce consistent signal.

**Solution dump vulnerability.** If "write me the complete working solution for all the failing tests" would produce a full score, the independence signal is broken. The task must require judgment about *what* to fix, not just execution.

**Underdocumented repo.** If the RUNBOOK / RISKS.md / diagnostics are thin, candidates cannot prompt with specific context, and context_provision scores will be uniformly low. Every task needs rich, specific incident documentation.

### Rubric anti-patterns

**Vague criteria language.** "Shows good judgment" is not a criterion. "Prioritises PII redaction before adding cache optimisations" is. Make every criterion observable.

**Rewarding code volume.** A rubric that scores `excellent` based on how much code was written will favour solution-dump behavior. Weight judgment over output.

**Missing communication dimension.** Without a communication_clarity or stakeholder_communication dimension, candidates who produce great technical work but cannot explain it will appear equivalent to those who do both.

**Equally weighted dimensions.** All-equal weights signal that no prioritisation was done. Allocate weights in proportion to what the hiring manager actually needs.

### Scoring alignment anti-patterns

**No min_reading_time_seconds.** Without this hint, the scoring engine uses a default threshold that is too short for complex repos. Always set it.

**Tests that can be cheated.** Tests that pass when a hardcoded value is returned create misleading completion scores. Every test must validate behavior.

**No citable artifacts in diagnostics.** If the incident logs contain no specific transaction IDs, error codes, function names, or file paths, candidates have nothing to quote in their prompts. Context provision scores will be flat.

---

## Section 12: New Task Creation Checklist

Use this checklist for every new task from inception to production seeding.

### Phase 1: Role alignment
- [ ] Export the live job spec for the target role
- [ ] Identify 3–5 core requirements the task will test
- [ ] Identify 2–4 things the task will explicitly NOT test
- [ ] Select the 5 rubric dimensions that best map to the JD requirements
- [ ] Draft the `jd_to_signal_map` array

### Phase 2: Scenario design
- [ ] Write the stakeholder message (named person, consequence, output ask)
- [ ] Write the mission statement (names the judgment being evaluated)
- [ ] Confirm the timebox: 2–3 fixable issues, not all
- [ ] Write the `expected_candidate_journey` phases

### Phase 3: Repository design
- [ ] Design the broken baseline: stub functions + incorrect logic
- [ ] Write the incident documentation: README, diagnostics, runbook/risks
- [ ] Verify each failing test maps to an incident symptom
- [ ] Confirm the repo bootstraps cleanly
- [ ] Confirm 4–8 tests fail for meaningful reasons
- [ ] Set `min_reading_time_seconds: 300` in `scoring_hints`

### Phase 4: Rubric and signals
- [ ] Write `evaluation_rubric` with 5 dimensions, weights summing to 1.0
- [ ] Write excellent/good/poor criteria for each dimension (observable behavior)
- [ ] Write `interviewer_signals.strong_positive` (5–6 items)
- [ ] Write `interviewer_signals.red_flags` (5–6 items)

### Phase 5: Technical completion
- [ ] Write the `calibration_prompt` (role-specific, 2-minute, context-requiring)
- [ ] Complete `workspace_bootstrap` and `test_runner` sections
- [ ] Run schema validation
- [ ] Run a local dry-run and document results in `docs/task_dry_runs/`

### Phase 6: Human pilot
- [ ] Conduct at least one internal pilot run
- [ ] Update `human_testing_checklist` based on pilot results
- [ ] Confirm `rubric_matches_role: true` against the live job spec
- [ ] Seed to production database

---

## Appendix: Reference — Both Canonical Tasks

| Property | `ai_eng_genai_production_readiness` | `data_eng_aws_glue_pipeline_recovery` |
|----------|-------------------------------------|---------------------------------------|
| Role | AI Engineer | Data Engineer |
| Duration | 30 min | 30 min |
| Business pressure | Executive preview in 7 days | Finance close tomorrow |
| Core signal | Safety judgment + delivery pragmatics | Correctness + audit trust + prioritisation |
| Top rubric weight | system_risk_assessment 0.24 | situational_assessment 0.22 |
| Deliberate failures | PII leaks into prompts; no degraded mode; cache not used; review disabled | Quality gate always passes; dedupe is a no-op; bookmark ignores quality; type mapping incomplete |
| Reading trap | RISKS.md + launch_checklist.md | diagnostics/job_run.log + audit_findings.md |
| min_reading_time_seconds | 300 | 300 |
| Test count (baseline failing) | 4 | 5 |
| Role alignment source | Workable:120884740D (GenAI Engineer) | Workable:CE038C5C15 (AWS Glue Data Engineer) |

---

*This document supersedes the brief `TASK_DESIGN_FRAMEWORK.md`. The brief version remains as a quick-reference summary. This document is the authoritative guide for task authors.*
