#!/usr/bin/env bash
# Mirror-vendor mainspring's ORM-free metering seam into the backend
# (ADR-0010 metering convergence, cut #1b). Copies only metering/{seam,pricing}.py
# — the two files the shadow comparator needs — and records the source SHA.
# Mirrors frontend/scripts/vendor_mainspring_tokens.sh.
#
#   MAINSPRING_DIR=/path/to/mainspring bash backend/scripts/vendor_mainspring_metering.sh
set -euo pipefail

MS="${MAINSPRING_DIR:-../mainspring}"
SRC="$MS/mainspring/platform/metering"
DEST="backend/vendor/mainspring_metering"

[ -d "$SRC" ] || { echo "mainspring metering not found at $SRC (set MAINSPRING_DIR)"; exit 1; }

mkdir -p "$DEST"
cp "$SRC/seam.py" "$SRC/pricing.py" "$DEST/"

SHA="$(git -C "$MS" rev-parse --short HEAD 2>/dev/null || echo unknown)"
{
  echo "mainspring @ $SHA"
  echo "Files: metering/seam.py, metering/pricing.py (ORM-free seam only)."
  echo "Re-vendor: bash backend/scripts/vendor_mainspring_metering.sh"
} > "$DEST/MAINSPRING_REF.txt"

echo "vendored mainspring metering seam @ $SHA -> $DEST"
