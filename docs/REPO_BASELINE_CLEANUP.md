# Repo Baseline Cleanup

Last reviewed: 2026-04-26

This note records the cleanup pass used to get the repository into a safer baseline for iteration. It separates changes that are safe to automate from branches/worktrees that still need an owner decision.

## Safe Cleanup Applied

- Removed local generated caches:
  - `__pycache__/`
  - `.pytest_cache/`
  - `*.pyc`
  - `.DS_Store`
  - `frontend/dist/`
  - `frontend/test-results/`
- Removed duplicate ignored Python environments:
  - `.venv/`
  - `backend/venv/`
- Kept `backend/.venv/` as the active backend test environment.
- Removed stale untracked Ralph launch logs:
  - `.ralph/workable-launch/`
- Removed clean extra worktrees:
  - `/private/tmp/tali-main-merge`
  - `/Users/sampatel/tali-platform-v2-migration`
  - `/Users/sampatel/tali-platform/.worktrees/push-main`
- Added root ignore rules for Python bytecode, pytest cache, and build output.
- Pruned stale Git worktree metadata with `git worktree prune`.
- Deleted local branches that Git reported as already merged into the current branch and that were not checked out:
  - `codex/demo-design-framework-pass`
  - `codex/settings-refactor-and-preferences-fix`
  - `codex/theme-toggle-unification`
  - `codex/workable-recovery-ralph`
  - `codex/ia-candidate-scoring-redesign`
  - `feat/workable-integration-hardening`
  - `codex/prescreen-prod-fix`
- Deleted exact-duplicate local branch pointers while preserving an equivalent branch at the same commit:
  - `codex/v2-full-migration-integration` duplicated `codex/v2-landing-zone-restore`
  - `release/main-merge-taali` duplicated `codex/header-account-fix`
- Rebuilt frontend dependencies with `npm ci` after cleanup to restore package internals and verify the install from `package-lock.json`.

## Not Removed Automatically

These were intentionally preserved because they are dirty, checked out in a worktree, point at remote history, or may be useful for recovery:

- Current working branch: `codex/scoring-rubric-and-backend-contract`
- Active/dirty local worktrees under `.worktrees/workable/*`
- Dirty worktrees under `/private/tmp/*`
- External worktrees:
  - `/Users/sampatel/tali-platform-header-account-fix`
  - `/Users/sampatel/tali-platform-taali-redesign`
- Dependency/runtime folders:
  - `frontend/node_modules/`
  - `backend/.venv/`
  - `backend/uploads/`

## Remaining Local Bloat To Consider

The biggest local-only folders at review time were dependency/runtime folders. They are ignored by Git and safe to recreate, but removing them slows follow-up testing until dependencies are installed again.

| Path | Approx size | Recommendation |
| --- | ---: | --- |
| `frontend/node_modules/` | ~282 MB | Keep while actively testing frontend. Remove only before archiving the workspace. |
| `backend/.venv/` | ~169 MB | Keep as the canonical backend test environment. |
| `.worktrees/` | ~30 MB | Do not remove until dirty worktree changes have been merged, archived, or explicitly abandoned. |
| `backend/uploads/` | ~8 MB | Keep unless demo/test uploaded files are deliberately archived or discarded. |

## Branch Hygiene Recommendation

For the next clean baseline branch:

1. Decide whether `design/taali-redesign-2025` or the current deployed branch is the canonical source branch.
2. Merge or archive dirty worktree changes that are still valuable.
3. Remove worktrees with `git worktree remove <path>` only after `git -C <path> status --short` is empty or the changes are deliberately abandoned.
4. Delete local branches with `git branch -d <branch>` first. Use force deletion only when the branch is explicitly archived elsewhere or no longer needed.
5. Prune remote-tracking references with `git remote prune origin` after stale remote branches are deleted on GitHub.

## Verification From Cleanup Pass

- `npm run typecheck`
- `npm run lint:ui`
- `npm test -- --run src/test/CandidateDetail.test.jsx src/components/assessment/AssessmentPage.test.jsx src/features/jobs/JobPipelinePage.test.jsx src/features/tasks/TasksPage.test.jsx src/test/DemoFlow.test.jsx src/test/HeaderMobile.test.jsx src/test/SecureCandidateShareLinks.test.jsx`
- `npm run build`
- `backend/.venv/bin/pytest backend/tests/components/assessments/test_assessment_terminal.py backend/tests/components/assessments/test_terminal_chat_bridge.py backend/tests/test_api_assessments.py backend/tests/test_api_roles.py backend/tests/test_unit_claude_model_fallback.py backend/tests/test_api_workable_sync.py backend/tests/test_unit_workable_actions.py backend/tests/test_ci_architecture_gates.py backend/tests/platform/test_startup_validation.py`

## Claude Integration Baseline

Claude API ownership and generated-surface mapping now lives in `docs/claude/README.md`. Keep that document current as the product adds more generated recruiter, candidate, and interviewer touchpoints.
