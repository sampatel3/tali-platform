#!/usr/bin/env bash
# Mirror-vendor mainspring's ORM-free promotion-gate seam into the backend
# (ADR-0010 convergence, cut #3). Unlike the metering seam, mainspring does NOT
# ship a single ORM-free file here: platform/services/promotion_gate.py run_gate
# is Session/ORM-coupled. So this script does NOT copy a mainspring file — it
# records the source SHA the hand-extracted seam.py was derived from, and exists
# so the provenance check + re-derive step stays a one-liner. When mainspring
# splits a pure gate-decision module out of run_gate, point SRC at it and copy.
# Mirrors backend/scripts/vendor_mainspring_metering.sh.
#
#   MAINSPRING_DIR=/path/to/mainspring bash backend/scripts/vendor_mainspring_gate.sh
set -euo pipefail

MS="${MAINSPRING_DIR:-../mainspring}"
SRC="$MS/mainspring/platform/services/promotion_gate.py"
DEST="backend/vendor/mainspring_gate"

[ -f "$SRC" ] || { echo "mainspring promotion_gate not found at $SRC (set MAINSPRING_DIR)"; exit 1; }

mkdir -p "$DEST"

SHA="$(git -C "$MS" rev-parse --short HEAD 2>/dev/null || echo unknown)"
{
  echo "mainspring master @ $SHA"
  echo "Source: platform/services/promotion_gate.py (run_gate) + platform/models/policy_version.py (PolicyStatus)."
  echo "Vendored: the ORM-free gate-DECISION rule only (seam.py) — mainspring's run_gate"
  echo "is Session/ORM-coupled (queries rows, mutates PolicyVersion.status, writes an"
  echo "AuditEvent), so it can't be vendored whole. seam.py extracts the pure"
  echo "composition (passed = shadow & holdout & bias; status ACTIVE/GATED/FAILED_GATE)"
  echo "verbatim, plus the PolicyStatus string values, with no Session/ORM imports."
  echo "Re-vendor: bash backend/scripts/vendor_mainspring_gate.sh"
} > "$DEST/MAINSPRING_REF.txt"

echo "recorded mainspring gate seam provenance @ $SHA -> $DEST"
echo "NOTE: seam.py is hand-extracted (run_gate is ORM-coupled); re-derive by hand if run_gate's composition changed."
