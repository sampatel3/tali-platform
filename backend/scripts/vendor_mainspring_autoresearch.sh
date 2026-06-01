#!/usr/bin/env bash
# Mirror-vendor mainspring's pure autoresearch search loop into the backend
# (ADR-0010 convergence). Unlike the bias/gate seams, mainspring ships this as a
# dependency-free core module (mainspring/core/autoresearch.py: stdlib only, no
# ORM/LLM/platform), so this script COPIES it verbatim — seam.py must stay
# byte-identical to the source. The brand wires fit/score/audit callables and a
# Proposer around it (see app/decision_policy/autoresearch.py).
# Mirrors backend/scripts/vendor_mainspring_metering.sh (the other verbatim copy).
#
#   MAINSPRING_DIR=/path/to/mainspring bash backend/scripts/vendor_mainspring_autoresearch.sh
set -euo pipefail

MS="${MAINSPRING_DIR:-../mainspring}"
SRC="$MS/mainspring/core/autoresearch.py"
DEST="backend/vendor/mainspring_autoresearch"

[ -f "$SRC" ] || { echo "mainspring autoresearch not found at $SRC (set MAINSPRING_DIR)"; exit 1; }

mkdir -p "$DEST"
cp "$SRC" "$DEST/seam.py"

SHA="$(git -C "$MS" rev-parse --short HEAD 2>/dev/null || echo unknown)"
BRANCH="$(git -C "$MS" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
{
  echo "mainspring $BRANCH @ $SHA"
  echo "Source: mainspring/core/autoresearch.py"
  echo "Vendored: the WHOLE module, verbatim. Unlike the bias/gate seams, this is a"
  echo "pure core-layer module (stdlib only, no ORM/LLM/platform), so it is copied"
  echo "1:1 rather than hand-extracted — seam.py must stay byte-identical to source."
  echo "The brand (tali) injects fit_fn/score_fn/audit_fn + a Proposer; the loop"
  echo "mechanics (propose -> fit -> score -> audit -> keep/discard) live here."
  echo "Re-vendor: bash backend/scripts/vendor_mainspring_autoresearch.sh"
} > "$DEST/MAINSPRING_REF.txt"

echo "vendored mainspring autoresearch seam @ $SHA -> $DEST"