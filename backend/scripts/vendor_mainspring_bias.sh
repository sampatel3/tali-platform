#!/usr/bin/env bash
# Mirror-vendor mainspring's ORM-free bias-audit seam into the backend
# (ADR-0010 convergence, cut #4). Unlike metering, mainspring's
# services/bias_audit.py is NOT ORM-free (its audit() runs select(Case) on a
# Session), so seam.py is a HAND-CURATED ORM-free lift, not a raw copy: it
# copies mainspring's dataclasses + constants verbatim and lifts the
# demographic-parity verdict out of audit(). This script therefore only refreshes
# the recorded source SHA + diffs the upstream file so a drift is visible at
# re-vendor time; it does NOT clobber the curated seam.
#
#   MAINSPRING_DIR=/path/to/mainspring bash backend/scripts/vendor_mainspring_bias.sh
set -euo pipefail

MS="${MAINSPRING_DIR:-../mainspring}"
SRC="$MS/mainspring/platform/services/bias_audit.py"
DEST="backend/vendor/mainspring_bias"

[ -f "$SRC" ] || { echo "mainspring bias_audit not found at $SRC (set MAINSPRING_DIR)"; exit 1; }

SHA="$(git -C "$MS" rev-parse --short HEAD 2>/dev/null || echo unknown)"
{
  echo "mainspring master @ $SHA"
  echo "Source: mainspring/platform/services/bias_audit.py"
  echo "Vendored (ORM-free only): GroupMetrics, BiasAuditResult, the constants"
  echo "(AUDIT_GROUP_FIELD, MAX_PARITY_GAP, MAX_ODDS_GAP, MIN_GROUP_N) — copied verbatim —"
  echo "plus a thin seam (evaluate_demographic_parity / GroupRate / BiasAuditor) lifting"
  echo "mainspring's audit() demographic-parity verdict out of its DB session so the"
  echo "shadow needs no mainspring Case/PolicyVersion/Session."
  echo "NOT vendored: audit() / _load_fitted() / _audit_group() / _expected_label() —"
  echo "they import sqlalchemy Session + Case + PolicyVersion and run select(Case) (NOT"
  echo "ORM-free); the equalized_odds branch (needs labelled cases loaded from the DB)."
  echo "Re-vendor: bash backend/scripts/vendor_mainspring_bias.sh"
} > "$DEST/MAINSPRING_REF.txt"

echo "recorded mainspring bias-audit source SHA @ $SHA -> $DEST/MAINSPRING_REF.txt"
echo "NOTE: seam.py is hand-curated (mainspring upstream is not ORM-free); review"
echo "      $SRC for drift against the vendored dataclasses + demographic-parity rule."
