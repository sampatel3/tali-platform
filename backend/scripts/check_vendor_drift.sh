#!/usr/bin/env bash
# Drift gate for the vendored mainspring seams (backend/vendor/mainspring_*).
#
# The seams are mirror-vendored from mainspring (ADR-0010). This catches the case
# where the committed copy has silently fallen out of sync with upstream — either
# because mainspring moved on, or because someone hand-edited a vendored file.
#
# Two classes of carve-out (see each backend/scripts/vendor_mainspring_*.sh):
#   * REGENERATING (autoresearch, metering, kg, policy) — the vendor script
#     rewrites the seam from mainspring source, so re-running it and diffing IS
#     the drift signal. This gate fails when any of these differ.
#   * HAND-MAINTAINED (bias, gate) — the vendor script only stamps the source
#     SHA; seam.py is curated by hand (mainspring's source is ORM-coupled). These
#     can't be auto-diffed, so we WARN when the pinned SHA lags mainspring HEAD;
#     their behavioural drift is covered by the parity tests
#     (tests/decision_policy/test_bias_seam_parity.py,
#     tests/agent_v2/test_phase5_promotion_gate.py).
#
# Usage (token-free, needs a local mainspring checkout):
#   MAINSPRING_DIR=../mainspring bash backend/scripts/check_vendor_drift.sh
#
# Non-destructive: restores backend/vendor/ to HEAD before exiting.
set -uo pipefail

MS="${MAINSPRING_DIR:-../mainspring}"
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

REGEN="autoresearch metering kg policy"
HANDMADE="bias gate"

[ -d "$MS/mainspring" ] || { echo "ERROR: mainspring checkout not found at '$MS' (set MAINSPRING_DIR)"; exit 2; }
if ! git diff --quiet -- backend/vendor; then
  echo "ERROR: backend/vendor has uncommitted changes — commit or stash before checking."; exit 2
fi

MS_HEAD="$(git -C "$MS" rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "Vendor drift gate — checking against mainspring @ $MS_HEAD ($MS)"
echo

fail=0

# --- regenerating seams: re-vendor, then diff -------------------------------
for c in $REGEN; do
  if ! MAINSPRING_DIR="$MS" bash "backend/scripts/vendor_mainspring_$c.sh" >/dev/null 2>&1; then
    echo "  [error] vendor_mainspring_$c.sh failed against $MS (a source path may have moved upstream)"
    fail=1
  fi
done

# git status --porcelain (not git diff --name-only) so a re-vendor that emits a
# NEW, untracked file is caught too: git diff ignores untracked files, and the
# `git clean` below would then delete that fresh output — letting CI pass with
# an incomplete committed vendor tree. The `sed` strips the 3-char status prefix.
changed="$(git status --porcelain -- backend/vendor ':(exclude)backend/vendor/**/MAINSPRING_REF.txt' 2>/dev/null | sed 's/^...//')"
if [ -n "$changed" ]; then
  echo "DRIFT — committed seams differ from a fresh re-vendor of mainspring @ $MS_HEAD:"
  echo "$changed" | sed 's/^/    /'
  echo "    fix: MAINSPRING_DIR=$MS bash backend/scripts/vendor_mainspring_<seam>.sh  (then commit)"
  fail=1
else
  echo "OK — regenerating seams (autoresearch, metering, kg, policy) match mainspring @ $MS_HEAD"
fi

# --- hand-maintained seams: SHA-lag warning ---------------------------------
echo
for c in $HANDMADE; do
  ref="$(grep -oE '@ [0-9a-f]{7,40}' "backend/vendor/mainspring_$c/MAINSPRING_REF.txt" 2>/dev/null | head -1 | awk '{print $2}')"
  if [ -z "$ref" ]; then
    echo "  [warn] mainspring_$c: no pinned SHA recorded in MAINSPRING_REF.txt"
  elif [ "$ref" != "$MS_HEAD" ]; then
    echo "  [warn] mainspring_$c (hand-maintained) pinned @ $ref, mainspring HEAD @ $MS_HEAD"
    echo "         -> review the seam against upstream; behavioural check = its parity test"
  fi
done

# --- restore working tree (non-destructive) ---------------------------------
git checkout -- backend/vendor >/dev/null 2>&1 || true
git clean -fdq -- backend/vendor >/dev/null 2>&1 || true

echo
if [ "$fail" -eq 0 ]; then echo "drift-gate: PASS"; else echo "drift-gate: FAIL"; fi
exit "$fail"
