#!/usr/bin/env bash
# Mirror-vendor mainspring's ORM-free metering seam into the backend
# (ADR-0010 metering convergence, cut #1b). Copies only metering/{seam,pricing}.py
# — the two files the shadow comparator needs — and records the source SHA.
# Mirrors frontend/scripts/vendor_mainspring_tokens.sh.
#
#   MAINSPRING_DIR=/path/to/mainspring bash backend/scripts/vendor_mainspring_metering.sh
set -euo pipefail

MS="${MAINSPRING_DIR:-../mainspring}"
# Post-carve (3-layer): the ORM-free seam lives in the accelerator and the
# (vertical-blind, ORM-free) pricer lives in governance. platform/metering is
# now empty, so source each file from its canonical post-carve home.
SEAM_SRC="$MS/mainspring/accelerator/metering/seam.py"
PRICING_SRC="$MS/mainspring/governance/metering/pricing.py"
DEST="backend/vendor/mainspring_metering"

[ -f "$SEAM_SRC" ] || { echo "mainspring metering seam not found at $SEAM_SRC (set MAINSPRING_DIR)"; exit 1; }
[ -f "$PRICING_SRC" ] || { echo "mainspring metering pricing not found at $PRICING_SRC (set MAINSPRING_DIR)"; exit 1; }

mkdir -p "$DEST"
cp "$SEAM_SRC" "$PRICING_SRC" "$DEST/"

SHA="$(git -C "$MS" rev-parse --short HEAD 2>/dev/null || echo unknown)"
{
  echo "mainspring @ $SHA"
  echo "Files: accelerator/metering/seam.py, governance/metering/pricing.py (ORM-free seam only)."
  echo "Re-vendor: bash backend/scripts/vendor_mainspring_metering.sh"
} > "$DEST/MAINSPRING_REF.txt"

echo "vendored mainspring metering seam @ $SHA -> $DEST"
