# Cursor Implementation Spec - Tali Assessment System

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
