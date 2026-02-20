#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${1:-${RAILWAY_SERVICE:-resourceful-adaptation}}"
LINES="${LINES:-200}"
ENV_NAME="${RAILWAY_ENVIRONMENT:-production}"
LOG_KIND="${LOG_KIND:-runtime}" # runtime|build|deployment

if ! command -v railway >/dev/null 2>&1; then
  echo "error: railway CLI is not installed. Install with: npm i -g @railway/cli" >&2
  exit 1
fi

FLAGS=()
case "$LOG_KIND" in
  runtime)
    ;;
  build)
    FLAGS+=(--build)
    ;;
  deployment)
    FLAGS+=(--deployment)
    ;;
  *)
    echo "error: LOG_KIND must be one of: runtime, build, deployment" >&2
    exit 1
    ;;
esac

echo "Fetching ${LOG_KIND} logs for service '$SERVICE_NAME' (env: $ENV_NAME, lines: $LINES)..."
if ((${#FLAGS[@]})); then
  railway logs --service "$SERVICE_NAME" --environment "$ENV_NAME" --lines "$LINES" "${FLAGS[@]}"
else
  railway logs --service "$SERVICE_NAME" --environment "$ENV_NAME" --lines "$LINES"
fi
