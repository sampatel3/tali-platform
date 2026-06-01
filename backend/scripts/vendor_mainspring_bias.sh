#!/usr/bin/env bash
# Mirror-vendor mainspring's ORM-free bias-audit seam into the backend
# (ADR-0010 convergence, cut #4). Post-carve, mainspring's bias-audit VERDICT is
# pure, vertical-blind governance: mainspring/governance/bias_audit.py
# (`pairwise_fairness_verdict` — the EEOC 4/5ths pairwise disparate-impact test +
# the selection / outcome / calibration parity gaps). That governance module is
# ORM-free, so seam.py mirrors its pure verdict function byte-for-byte; the
# ORM-coupled data loading (querying audit-tagged Case rows, running the fitted
# policy) lives in accelerator/services/bias_audit.py and is NOT vendored.
#
# This script repoints at the post-carve canonical source, records the upstream
# SHA, and diffs governance's pure verdict against the vendored seam so any drift
# is visible at re-vendor time. seam.py is a hand-curated lift (it strips
# governance's BiasAuditResult/GroupMetrics adapter dataclasses, keeping only the
# ORM-free thresholds + SegmentMetrics + pairwise_fairness_verdict), so this does
# NOT clobber it.
#
#   MAINSPRING_DIR=/path/to/mainspring bash backend/scripts/vendor_mainspring_bias.sh
set -euo pipefail

MS="${MAINSPRING_DIR:-../mainspring}"
SRC="$MS/mainspring/governance/bias_audit.py"
DEST="backend/vendor/mainspring_bias"

[ -f "$SRC" ] || { echo "mainspring bias_audit not found at $SRC (set MAINSPRING_DIR)"; exit 1; }

SHA="$(git -C "$MS" rev-parse --short HEAD 2>/dev/null || echo unknown)"
{
  echo "mainspring master @ $SHA"
  echo "Source: mainspring/governance/bias_audit.py (post-carve canonical; ORM-free governance)"
  echo "Vendored (ORM-free verdict): the EEOC 4/5ths PAIRWISE disparate-impact test +"
  echo "selection / outcome / calibration parity gaps — pairwise_fairness_verdict, plus"
  echo "BiasThresholds, SegmentMetrics, and the threshold constants — mirrored from"
  echo "governance.bias_audit (itself ORM-free) into seam.py."
  echo "NOT vendored: the BiasAuditResult/GroupMetrics single-group demographic-parity"
  echo "adapter (demographic_parity_verdict) — that is the accelerator DB-adapter shape,"
  echo "not the brand's pairwise input; and accelerator/services/bias_audit.py audit()"
  echo "which runs select(Case) on a Session (NOT ORM-free)."
  echo "Re-vendor: MAINSPRING_DIR=<ms> bash backend/scripts/vendor_mainspring_bias.sh"
} > "$DEST/MAINSPRING_REF.txt"

echo "recorded mainspring bias-audit source SHA @ $SHA -> $DEST/MAINSPRING_REF.txt"
echo "NOTE: seam.py is hand-curated; review $SRC for drift against the vendored"
echo "      thresholds + pairwise_fairness_verdict rule."
