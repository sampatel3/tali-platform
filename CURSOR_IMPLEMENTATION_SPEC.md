# Cursor Implementation Spec - Taali Assessment System

## Implementation Notes (Product Decisions Applied)

1. **Candidate rubric visibility**
   - Candidate start payload includes only rubric category keys + weights via `rubric_categories`.
   - Rubric criteria (`excellent/good/poor`) and evaluator-only guidance are not included in candidate payloads.

2. **Timeout auto-complete**
   - When assessment time reaches zero during candidate interaction, the backend auto-submits:
     - blocks new actions
     - captures chat + git evidence (best effort)
     - marks `completed_due_to_timeout=true`
     - sets status `completed_due_to_timeout`

3. **Evaluation visibility**
   - Evaluation is non-blind; recruiter flows continue to include candidate identity.

4. **AI-assisted evaluation v2 scaffold**
   - Feature-flagged endpoint `POST /api/v1/assessments/{id}/ai-eval-suggestions`.
   - Stub implementation returns suggestions only; human reviewer remains final decision-maker.

5. **Repository workflow**
   - `AssessmentRepositoryService` creates/uses a per-task template repo and assessment branch `assessment/{assessment_id}`.
   - Includes local mock GitHub harness (`GITHUB_MOCK_MODE=true`) for deterministic tests.

## Per-Role Task Mapping (active starred Workable roles)

These map every starred role on `sampatel@deeplight.ae`'s Workable integration to either an existing task, a new task spec authored in `backend/tasks/`, or an explicit "no assessment" decision. Source-of-truth list is `roles.starred_for_auto_sync = true` in production.

| Workable role | Task / decision | Notes |
|---|---|---|
| Senior Scrum Master (workable:C9D0B1B53A) | `scrum_master_sprint_recovery_scenario` | Non-coding scenario task. HANDBACK.md is the deliverable; pytest checks topic coverage; rubric judges quality. |
| Senior Cloud Solutions Architect (workable:A16265634E) | Reuse `platform_eng_aws_eks_misconfig_triage` with rubric overrides | Architecture role overlaps with platform engineering at the IaC level; bias the rubric weights toward `technical_design` and `communication_clarity`, drop `implementation_quality` to 0.10. |
| AI Delivery Lead (workable:3C21FD5F3F) | Reuse `ai_eng_genai_production_readiness` with rubric overrides | Delivery framing, not engineering depth. Override `score_weights` per assessment to: communication 0.30, prompt_clarity 0.20, decomposition 0.20, code_correctness 0.20, independence 0.10. Document the override on the assessment record so recruiter UI can show "Delivery rubric applied". |
| Senior AWS Platform Engineer (workable:B9E7FE0FD6) | `platform_eng_aws_eks_misconfig_triage` | New task — VPC + EKS + IAM + CNI add-on misconfig triage; pytest parses Terraform via `python-hcl2`. |
| Senior Azure Platform Engineer (workable:CE0F3B4176) | `platform_eng_azure_aks_misconfig_triage` | New task — sibling to the AWS one with AKS + Bicep + RBAC + NSG; pytest parses precompiled Bicep JSON. |
| Senior Project Manager — Cyber & Tech Operations (workable:7B9EC03663) | Reuse `scrum_master_sprint_recovery_scenario` with scenario substitution | Same shape; replace SPRINT_BOARD/PO_CHAT inputs with an incident-recovery brief and a security-vendor coordination thread. Defer to phase 2. |
| Portfolio Lead and Business Manager (workable:A913E6DC5F) | No assessment for now | Use CV-match + role-fit only at top-of-funnel; defer scenario authoring until volume justifies. |
| Data Assurance Analyst (workable:9EE19097F4) | No assessment for now | 422 applications — CV-match + role-fit gate is sufficient as a first-pass screen. Reserve assessment seats for the top 50 by `role_fit_score`. |

### How rubric overrides land

The platform already supports per-task `score_weights`. To apply a delivery-leaning override on an existing task without forking the spec, set the override in `task.score_weights` for the role's task assignment record (not on the global task). The submission runtime reads `task.score_weights` at scoring time (see [submission_runtime.py:561](backend/app/components/assessments/submission_runtime.py:561)) and the TAALI role-fit blend layered on top is unchanged.

If a role needs a different `evaluation_rubric` (the qualitative criteria), that's a fork of the task — author it as a new JSON in `backend/tasks/` rather than overriding at runtime.
