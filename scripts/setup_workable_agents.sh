#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKTREE_ROOT_DEFAULT="${ROOT_DIR}/.worktrees/workable"
WORKTREE_ROOT="${WORKTREE_ROOT:-$WORKTREE_ROOT_DEFAULT}"
BASE_REF=""
FORCE=false

usage() {
  cat <<USAGE
Set up dedicated worktrees and branches for the Workable multi-agent execution plan.

Usage:
  $(basename "$0") [--base-ref <ref>] [--worktree-root <dir>] [--force]

Options:
  --base-ref <ref>       Base git ref for creating agent branches (default: current HEAD)
  --worktree-root <dir>  Directory where worktrees are created
  --force                Remove conflicting worktrees before recreating
  -h, --help             Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-ref)
      BASE_REF="$2"
      shift 2
      ;;
    --worktree-root)
      WORKTREE_ROOT="$2"
      shift 2
      ;;
    --force)
      FORCE=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$BASE_REF" ]]; then
  BASE_REF="$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD)"
fi

if ! git -C "$ROOT_DIR" rev-parse --verify "$BASE_REF" >/dev/null 2>&1; then
  echo "Base ref not found: $BASE_REF" >&2
  exit 1
fi

mkdir -p "$WORKTREE_ROOT"

declare -a AGENTS=(
  "a0|codex/workable-a0-integration-lead|agent_a0_orchestrator.md"
  "a1|codex/workable-a1-spec|agent_a1_spec.md"
  "a2|codex/workable-a2-backend|agent_a2_backend.md"
  "a3|codex/workable-a3-ia-jobs|agent_a3_frontend_ia_jobs.md"
  "a4|codex/workable-a4-candidate-workspace|agent_a4_candidate_workspace.md"
  "a5|codex/workable-a5-integration|agent_a5_workable_integration.md"
  "a6|codex/workable-a6-qa-rollout|agent_a6_qa_rollout.md"
)

echo "Root:         $ROOT_DIR"
echo "Base ref:     $BASE_REF"
echo "Worktree dir: $WORKTREE_ROOT"
echo ""

for row in "${AGENTS[@]}"; do
  IFS='|' read -r agent_id branch task_pack <<< "$row"
  wt_path="$WORKTREE_ROOT/$agent_id"

  if git -C "$ROOT_DIR" worktree list --porcelain | grep -F "worktree $wt_path" >/dev/null 2>&1; then
    if [[ "$FORCE" == "true" ]]; then
      echo "Removing existing worktree for $agent_id"
      git -C "$ROOT_DIR" worktree remove --force "$wt_path"
    else
      echo "Skipping $agent_id (worktree already exists): $wt_path"
      continue
    fi
  elif [[ -d "$wt_path" ]]; then
    if [[ "$FORCE" == "true" ]]; then
      echo "Removing stray directory for $agent_id: $wt_path"
      rm -rf "$wt_path"
    else
      echo "Skipping $agent_id (directory already exists): $wt_path"
      continue
    fi
  fi

  if git -C "$ROOT_DIR" show-ref --verify --quiet "refs/heads/$branch"; then
    echo "Adding worktree for existing branch $branch"
    git -C "$ROOT_DIR" worktree add "$wt_path" "$branch"
  else
    echo "Creating branch $branch from $BASE_REF"
    git -C "$ROOT_DIR" worktree add -b "$branch" "$wt_path" "$BASE_REF"
  fi

  task_src="$ROOT_DIR/tasks/workable_agents/$task_pack"
  if [[ -f "$task_src" ]]; then
    cp "$task_src" "$wt_path/AGENT_TASK.md"
  fi

done

echo ""
echo "Workable agent worktrees are ready."
echo ""
echo "Next steps (per worktree):"
echo "  1) Open AGENT_TASK.md"
echo "  2) Execute assigned lane only"
echo "  3) Commit lane changes to its branch"
echo "  4) Open PR to integration branch in merge order"
echo ""
echo "Reference docs:"
echo "  - $ROOT_DIR/RALPH_TASK.md"
echo "  - $ROOT_DIR/tasks/workable_agents/agent_matrix.yaml"
