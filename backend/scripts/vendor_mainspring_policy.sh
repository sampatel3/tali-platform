#!/usr/bin/env bash
# Mirror-vendor mainspring's ORM-free deterministic PolicyEngine into the
# backend (ADR-0010 convergence, cut #2: decision-policy). Copies only the
# pure core/{policy,signals,budget,pipeline}.py — the files the policy shadow
# comparator needs — and records the source SHA. Mirrors
# vendor_mainspring_metering.sh.
#
#   MAINSPRING_DIR=/path/to/mainspring bash backend/scripts/vendor_mainspring_policy.sh
set -euo pipefail

MS="${MAINSPRING_DIR:-../mainspring}"
SRC="$MS/mainspring/core"
DEST="backend/vendor/mainspring_policy"

[ -d "$SRC" ] || { echo "mainspring core not found at $SRC (set MAINSPRING_DIR)"; exit 1; }

mkdir -p "$DEST"
cp "$SRC/policy.py" "$SRC/signals.py" "$SRC/budget.py" "$SRC/pipeline.py" "$DEST/"

SHA="$(git -C "$MS" rev-parse --short HEAD 2>/dev/null || echo unknown)"
{
  echo "mainspring @ $SHA"
  echo "Files: core/policy.py, core/signals.py, core/budget.py, core/pipeline.py"
  echo "       (ORM-free deterministic PolicyEngine + its pure deps)."
  echo "Re-vendor: bash backend/scripts/vendor_mainspring_policy.sh"
} > "$DEST/MAINSPRING_REF.txt"

echo "vendored mainspring policy engine @ $SHA -> $DEST"
