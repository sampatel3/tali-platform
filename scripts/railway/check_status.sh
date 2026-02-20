#!/usr/bin/env bash
set -euo pipefail

ENV_NAME="${RAILWAY_ENVIRONMENT:-production}"

if ! command -v railway >/dev/null 2>&1; then
  echo "error: railway CLI is not installed. Install with: npm i -g @railway/cli" >&2
  exit 1
fi

railway environment "$ENV_NAME" >/dev/null
STATUS_FILE="$(mktemp)"
trap 'rm -f "$STATUS_FILE"' EXIT
railway status --json > "$STATUS_FILE"

python3 - "$STATUS_FILE" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1]))
project = payload.get("name") or "(unknown)"
print(f"Project: {project}")

edges = payload.get("environments", {}).get("edges", [])
if not edges:
    print("No environments found.")
    raise SystemExit(0)

env_node = edges[0].get("node", {})
print(f"Environment: {env_node.get('name') or '(unknown)'}")
print("")
print("Services:")

service_instances = env_node.get("serviceInstances", {}).get("edges", [])
if not service_instances:
    print("- none")
    raise SystemExit(0)

for edge in service_instances:
    node = edge.get("node", {})
    name = node.get("serviceName") or "(unknown)"
    latest = node.get("latestDeployment") or {}
    status = latest.get("status") or "UNKNOWN"
    created = latest.get("createdAt") or "-"
    print(f"- {name}: {status} (latest: {created})")
PY
