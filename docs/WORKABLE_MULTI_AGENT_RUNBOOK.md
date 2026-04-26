# Workable Redesign Multi-Agent Runbook

## Purpose
Operational runbook for executing the Workable-aligned recruiter workflow redesign with concurrent agent lanes.

## Canonical Artifacts
- Execution board: `/Users/sampatel/tali-platform/RALPH_TASK.md`
- Agent matrix: `/Users/sampatel/tali-platform/tasks/workable_agents/agent_matrix.yaml`
- Branch/worktree setup: `/Users/sampatel/tali-platform/scripts/setup_workable_agents.sh`

## Lanes
- A0: Orchestrator (`codex/workable-a0-integration-lead`)
- A1: Spec Freeze (`codex/workable-a1-spec`)
- A2: Backend Domain (`codex/workable-a2-backend`)
- A3: Frontend IA + Jobs Hub (`codex/workable-a3-ia-jobs`)
- A4: Candidate Workspace (`codex/workable-a4-candidate-workspace`)
- A5: Workable Integration (`codex/workable-a5-integration`)
- A6: QA + Rollout (`codex/workable-a6-qa-rollout`)

## Setup
```bash
cd /Users/sampatel/tali-platform
./scripts/setup_workable_agents.sh --base-ref codex/scoring-rubric-and-backend-contract
```

## Launching All 7 Agents
Predefined lane prompts are stored in `/Users/sampatel/tali-platform/tasks/workable_agents/prompts`.

Dry run (recommended first):
```bash
cd /Users/sampatel/tali-platform
./scripts/launch_workable_agents.sh --mode print
```

Launch all lanes in background:
```bash
cd /Users/sampatel/tali-platform
./scripts/launch_workable_agents.sh --mode background -y
```

Launch all lanes in tmux windows:
```bash
cd /Users/sampatel/tali-platform
./scripts/launch_workable_agents.sh --mode tmux -y
tmux attach -t workable-agents
```

## Branch/Worktree Policy
- One branch/worktree per lane.
- No cross-lane file ownership changes without A0 approval.
- Contract-changing PRs must include contract documentation updates.
- Workable behavior stays bounded to read sync plus invite/reject/reopen write-back; no broader ATS orchestration.

## Merge Gates
1. A1 spec freeze merged.
2. A2 contract merged after A5 review.
3. A3 and A4 merge in parallel stream.
4. A5 integration contract merge.
5. A6 QA/rollout package merge.
6. A0 final integration signoff.

## Daily Cadence
- AM (15 min): blockers and dependency changes only.
- PM (15 min): interface diffs and next-day merge intent.
- Any contract change requires same-day owner update to this runbook.

## Required Human Reviews
- A1 scope lock + acceptance criteria.
- A0 contract freeze checks.
- A0 final risk register + release signoff.
- A1 final workflow conformance check.

## Minimum Exit Criteria
- Jobs-first workflow usable behind feature flag.
- Canonical stage movement APIs and UI flows test-covered.
- Workable adapter contract documented and verified for read sync plus invite/reject/reopen write-back.
- Rollout + rollback + KPI monitoring package approved.
