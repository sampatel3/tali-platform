#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

EXPECTED_SHA=""
if [[ "${1:-}" == "--expected-sha" ]]; then
  EXPECTED_SHA="${2:-}"
  if [[ -z "$EXPECTED_SHA" || $# -ne 2 ]]; then
    echo "error: --expected-sha requires exactly one commit SHA." >&2
    exit 2
  fi
elif [[ $# -ne 0 ]]; then
  echo "error: usage: $0 [--expected-sha <sha>]" >&2
  exit 2
fi

if ! command -v git >/dev/null 2>&1; then
  echo "error: required release command is missing: git" >&2
  exit 1
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: production releases must run from a Git worktree." >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "error: production releases require a clean worktree." >&2
  exit 1
fi

HEAD_SHA="$(git rev-parse HEAD)"
if [[ -n "$EXPECTED_SHA" ]]; then
  # Coordinated rollouts pin every child process to a SHA that passed the exact
  # origin/main check at kickoff. Require the private, process-scoped
  # attestation created by the coordinator so setting an environment SHA alone
  # cannot turn a stale checkout into coordinated mode.
  EXPECTED_SHA="$(git rev-parse "${EXPECTED_SHA}^{commit}" 2>/dev/null)" || {
    echo "error: coordinated release SHA is not a commit in this worktree." >&2
    exit 1
  }
  if [[ "$HEAD_SHA" != "$EXPECTED_SHA" ]]; then
    echo "error: source commit changed during the coordinated rollout." >&2
    echo "       expected=$EXPECTED_SHA" >&2
    echo "         actual=$HEAD_SHA" >&2
    exit 1
  fi

  ATTESTATION_FILE="${TALI_COORDINATED_RELEASE_ATTESTATION:-}"
  ATTESTATION_TOKEN="${TALI_COORDINATED_RELEASE_TOKEN:-}"
  if [[ -z "$ATTESTATION_FILE" || -z "$ATTESTATION_TOKEN" \
    || ! -f "$ATTESTATION_FILE" || -L "$ATTESTATION_FILE" \
    || ! -O "$ATTESTATION_FILE" ]]; then
    echo "error: coordinated release attestation is missing or invalid." >&2
    exit 1
  fi
  ATTESTED_TOKEN="$(sed -n '1p' "$ATTESTATION_FILE")"
  ATTESTED_SHA="$(sed -n '2p' "$ATTESTATION_FILE")"
  ATTESTED_ROOT="$(sed -n '3p' "$ATTESTATION_FILE")"
  if [[ "$ATTESTED_TOKEN" != "$ATTESTATION_TOKEN" \
    || "$ATTESTED_SHA" != "$EXPECTED_SHA" \
    || "$ATTESTED_ROOT" != "$(pwd -P)" ]]; then
    echo "error: coordinated release attestation does not match this release." >&2
    exit 1
  fi

  git fetch --quiet origin main
  MAIN_SHA="$(git rev-parse origin/main)"
  if ! git merge-base --is-ancestor "$EXPECTED_SHA" "$MAIN_SHA"; then
    echo "error: coordinated release SHA is not in origin/main history." >&2
    echo "expected=$EXPECTED_SHA" >&2
    echo "origin/main=$MAIN_SHA" >&2
    exit 1
  fi
else
  git fetch --quiet origin main
  MAIN_SHA="$(git rev-parse origin/main)"
  if [[ "$HEAD_SHA" != "$MAIN_SHA" ]]; then
    echo "error: refusing to deploy a branch or stale commit." >&2
    echo "       HEAD=$HEAD_SHA" >&2
    echo "origin/main=$MAIN_SHA" >&2
    echo "Merge through main, fetch it, and deploy that exact commit." >&2
    exit 1
  fi
fi

echo "Release source verified: origin/main@$HEAD_SHA"
