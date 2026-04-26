#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKTREE_ROOT_DEFAULT="${ROOT_DIR}/.worktrees/workable"
PROMPT_ROOT_DEFAULT="${ROOT_DIR}/tasks/workable_agents/prompts"
LAUNCH_STATE_DIR_DEFAULT="${ROOT_DIR}/.ralph/workable-launch"

WORKTREE_ROOT="${WORKTREE_ROOT:-$WORKTREE_ROOT_DEFAULT}"
PROMPT_ROOT="${PROMPT_ROOT:-$PROMPT_ROOT_DEFAULT}"
LAUNCH_STATE_DIR="${LAUNCH_STATE_DIR:-$LAUNCH_STATE_DIR_DEFAULT}"
AGENT_CLI="${AGENT_CLI:-cursor-agent}"
MODEL="${MODEL:-gpt-5}"
CODEX_REASONING_EFFORT="${CODEX_REASONING_EFFORT:-high}"
CODEX_SANDBOX="${CODEX_SANDBOX:-workspace-write}"
MODE="background"
AGENTS_CSV="a0,a1,a2,a3,a4,a5,a6"
DRY_RUN=false
YES=false
TMUX_SESSION="${TMUX_SESSION:-workable-agents}"

usage() {
  cat <<USAGE
Launch all Workable redesign agent lanes with predefined prompts.

Usage:
  $(basename "$0") [options]

Options:
  --mode <background|tmux|print>  Launch mode (default: background)
  --agents <csv>                  Agents to launch (default: a0,a1,a2,a3,a4,a5,a6)
  --model <name>                  Model passed to agent CLI (default: gpt-5)
  --agent-cli <cmd>               Agent CLI command (default: cursor-agent; supports codex)
  --codex-reasoning-effort <v>    Codex reasoning effort for codex CLI (default: high)
  --codex-sandbox <mode>          Codex sandbox mode for codex CLI (default: workspace-write)
  --worktree-root <dir>           Worktree root (default: .worktrees/workable)
  --prompt-root <dir>             Prompt root (default: tasks/workable_agents/prompts)
  --tmux-session <name>           Tmux session name (default: workable-agents)
  --dry-run                       Print commands only, do not execute
  -y, --yes                       Skip launch confirmation
  -h, --help                      Show help

Prerequisite:
  Run ./scripts/setup_workable_agents.sh first.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="$2"
      shift 2
      ;;
    --agents)
      AGENTS_CSV="$2"
      shift 2
      ;;
    --model)
      MODEL="$2"
      shift 2
      ;;
    --agent-cli)
      AGENT_CLI="$2"
      shift 2
      ;;
    --codex-reasoning-effort)
      CODEX_REASONING_EFFORT="$2"
      shift 2
      ;;
    --codex-sandbox)
      CODEX_SANDBOX="$2"
      shift 2
      ;;
    --worktree-root)
      WORKTREE_ROOT="$2"
      shift 2
      ;;
    --prompt-root)
      PROMPT_ROOT="$2"
      shift 2
      ;;
    --tmux-session)
      TMUX_SESSION="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    -y|--yes)
      YES=true
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

if [[ "$MODE" != "background" && "$MODE" != "tmux" && "$MODE" != "print" ]]; then
  echo "Invalid --mode: $MODE" >&2
  exit 1
fi

if [[ "$MODE" == "tmux" ]] && ! command -v tmux >/dev/null 2>&1; then
  echo "tmux not found; use --mode background or install tmux." >&2
  exit 1
fi

if [[ "$MODE" != "print" ]] && ! command -v "$AGENT_CLI" >/dev/null 2>&1; then
  echo "Agent CLI not found: $AGENT_CLI" >&2
  exit 1
fi

mkdir -p "$LAUNCH_STATE_DIR"

agent_prompt_file() {
  local agent_id="$1"
  echo "$PROMPT_ROOT/agent_${agent_id}_prompt.md"
}

agent_worktree_dir() {
  local agent_id="$1"
  echo "$WORKTREE_ROOT/$agent_id"
}

build_command() {
  local agent_id="$1"
  local wt prompt
  local cli_basename
  wt="$(agent_worktree_dir "$agent_id")"
  prompt="$(agent_prompt_file "$agent_id")"
  cli_basename="$(basename "$AGENT_CLI")"

  if [[ ! -d "$wt" ]]; then
    echo "ERROR: missing worktree for $agent_id at $wt" >&2
    return 1
  fi

  if [[ ! -f "$prompt" ]]; then
    echo "ERROR: missing prompt for $agent_id at $prompt" >&2
    return 1
  fi

  local cmd
  if [[ "$cli_basename" == "codex" ]]; then
    cmd="cd $(printf '%q' "$wt") && $(printf '%q' "$AGENT_CLI") exec -m $(printf '%q' "$MODEL") --sandbox $(printf '%q' "$CODEX_SANDBOX") -c model_reasoning_effort=$(printf '%q' "\"$CODEX_REASONING_EFFORT\"") -C $(printf '%q' "$wt") --skip-git-repo-check \"\$(cat $(printf '%q' "$prompt"))\""
  else
    cmd="cd $(printf '%q' "$wt") && $(printf '%q' "$AGENT_CLI") -p --force --model $(printf '%q' "$MODEL") \"\$(cat $(printf '%q' "$prompt"))\""
  fi
  echo "$cmd"
}

IFS=',' read -r -a SELECTED_AGENTS <<< "$AGENTS_CSV"

for a in "${SELECTED_AGENTS[@]}"; do
  if [[ ! "$a" =~ ^a[0-6]$ ]]; then
    echo "Invalid agent id: $a (expected a0..a6)" >&2
    exit 1
  fi
  build_command "$a" >/dev/null
done

echo "Launch plan"
echo "  Mode:      $MODE"
echo "  Agent CLI: $AGENT_CLI"
echo "  Model:     $MODEL"
echo "  Agents:    ${SELECTED_AGENTS[*]}"
echo ""

for a in "${SELECTED_AGENTS[@]}"; do
  cmd="$(build_command "$a")"
  echo "[$a] $cmd"
done

echo ""
if [[ "$DRY_RUN" == "true" || "$MODE" == "print" ]]; then
  echo "Dry-run complete. No agents launched."
  exit 0
fi

if [[ "$YES" != "true" ]]; then
  read -r -p "Launch these agents now? [y/N] " REPLY
  if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
  fi
fi

timestamp="$(date +%Y%m%d-%H%M%S)"

if [[ "$MODE" == "background" ]]; then
  pid_file="$LAUNCH_STATE_DIR/pids-$timestamp.txt"
  echo "Launching in background..."
  for a in "${SELECTED_AGENTS[@]}"; do
    cmd="$(build_command "$a")"
    log_file="$LAUNCH_STATE_DIR/${a}-$timestamp.log"
    (
      bash -lc "$cmd"
    ) >"$log_file" 2>&1 &
    pid=$!
    echo "$a $pid $log_file" | tee -a "$pid_file"
  done

  echo ""
  echo "Launched ${#SELECTED_AGENTS[@]} agents in background."
  echo "PID file: $pid_file"
  echo "Logs:     $LAUNCH_STATE_DIR"
  exit 0
fi

if [[ "$MODE" == "tmux" ]]; then
  if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    echo "tmux session already exists: $TMUX_SESSION" >&2
    exit 1
  fi

  first_agent="${SELECTED_AGENTS[0]}"
  first_cmd="$(build_command "$first_agent")"
  tmux new-session -d -s "$TMUX_SESSION" -n "$first_agent" "bash -lc '$first_cmd'"

  for ((i = 1; i < ${#SELECTED_AGENTS[@]}; i++)); do
    a="${SELECTED_AGENTS[$i]}"
    cmd="$(build_command "$a")"
    tmux new-window -t "$TMUX_SESSION" -n "$a" "bash -lc '$cmd'"
  done

  echo "tmux session '$TMUX_SESSION' started with ${#SELECTED_AGENTS[@]} windows."
  echo "Attach: tmux attach -t $TMUX_SESSION"
  exit 0
fi
