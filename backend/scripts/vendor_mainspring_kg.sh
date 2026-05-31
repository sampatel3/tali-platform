#!/usr/bin/env bash
# Mirror-vendor mainspring's ORM-free knowledge-graph interface into the backend
# (ADR-0010 KG convergence, cut #5). Copies only knowledge_graph/base.py — the
# KnowledgeGraphBackend Protocol + pure dataclass shapes the shadow comparator
# needs — and records the source SHA. INTERFACE ONLY: the production
# GraphitiBackend stub (knowledge_graph/graphiti.py) is deliberately NOT vendored.
# Mirrors backend/scripts/vendor_mainspring_metering.sh.
#
#   MAINSPRING_DIR=/path/to/mainspring bash backend/scripts/vendor_mainspring_kg.sh
set -euo pipefail

MS="${MAINSPRING_DIR:-../mainspring}"
SRC="$MS/mainspring/platform/knowledge_graph"
DEST="backend/vendor/mainspring_kg"

[ -d "$SRC" ] || { echo "mainspring knowledge_graph not found at $SRC (set MAINSPRING_DIR)"; exit 1; }

mkdir -p "$DEST"
cp "$SRC/base.py" "$DEST/"

SHA="$(git -C "$MS" rev-parse --short HEAD 2>/dev/null || echo unknown)"
{
  echo "mainspring master @ $SHA"
  echo "Files: knowledge_graph/base.py (ORM-free Protocol + dataclasses only)."
  echo "Vendored INTERFACE ONLY (ADR-0010 cut #5, decision: converge the interface, not the store)."
  echo "The production GraphitiBackend (knowledge_graph/graphiti.py) is a known"
  echo "NotImplementedError stub and is deliberately NOT vendored or called — the"
  echo "shadow logs status=mainspring_stub instead. Tali keeps Graphiti as its store."
  echo "Re-vendor: bash backend/scripts/vendor_mainspring_kg.sh"
} > "$DEST/MAINSPRING_REF.txt"

echo "vendored mainspring kg interface @ $SHA -> $DEST"
