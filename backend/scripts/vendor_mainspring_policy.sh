#!/usr/bin/env bash
# Mirror-vendor mainspring's ORM-free deterministic PolicyEngine into the
# backend (ADR-0010 convergence, decision-policy). Post-carve, mainspring's
# verdict kernel is split across two layers:
#   - mainspring/spec/{policy_types,signal_types,pipeline}.py  (pure types)
#   - mainspring/governance/{policy,signals,budget}.py         (the logic)
# and mainspring/core/{policy,signals,budget,pipeline}.py are now thin
# back-compat SHIMS that re-export across those layers. We can't copy the
# shims verbatim — their `from mainspring.governance...` imports won't resolve
# in a flat vendored directory — so we ASSEMBLE four flat, self-contained
# modules (the shape the policy engine needs) from the canonical spec +
# governance sources and rewrite their cross-layer imports to local ones.
#
# Vendored layout (flat, import-local, ORM-free):
#   vendor/mainspring_policy/policy.py    spec/policy_types (Verdict, Rule,
#       EscalationConfig, SKIP/NO_ACTION/ESCALATE) + governance/policy
#       (PolicyEngine, DecisionPointSpec, WeightedRule, org_wide_send_bar)
#   vendor/mainspring_policy/signals.py   spec/signal_types (Signal,
#       SignalBundle, SignalProducer) + governance/signals (gather_signals)
#   vendor/mainspring_policy/budget.py    governance/budget (BudgetGovernor)
#   vendor/mainspring_policy/pipeline.py  spec/pipeline (Entity, …)
#
#   MAINSPRING_DIR=/path/to/mainspring bash backend/scripts/vendor_mainspring_policy.sh
set -euo pipefail

MS="${MAINSPRING_DIR:-../mainspring}"
SPEC="$MS/mainspring/spec"
GOV="$MS/mainspring/governance"
DEST="backend/vendor/mainspring_policy"

for f in "$SPEC/policy_types.py" "$SPEC/signal_types.py" "$SPEC/pipeline.py" \
         "$GOV/policy.py" "$GOV/signals.py" "$GOV/budget.py"; do
  [ -f "$f" ] || { echo "mainspring source not found: $f (set MAINSPRING_DIR)"; exit 1; }
done

mkdir -p "$DEST"

# --- policy.py : spec/policy_types (pure types) + governance/policy (logic) ---
# Strip governance/policy's spec imports (the types are inlined above it) and
# repoint its SignalBundle import at the local vendored signals module.
{
  echo '"""VENDORED from mainspring (ADR-0010, decision-policy). DO NOT EDIT BY HAND.'
  echo ''
  echo 'Assembled flat from mainspring/spec/policy_types.py (pure types) +'
  echo 'mainspring/governance/policy.py (engine logic). Re-vendor via'
  echo 'backend/scripts/vendor_mainspring_policy.sh."""'
  echo 'from __future__ import annotations'
  echo ''
  echo 'from dataclasses import dataclass, field'
  echo 'from statistics import median'
  echo 'from typing import Any, Callable, Protocol, runtime_checkable'
  echo ''
  echo 'from .signals import SignalBundle'
  echo ''
  # spec/policy_types.py body: drop its module docstring + __future__ +
  # imports (everything up to and including the SignalBundle TYPE_CHECKING
  # block); keep the SKIP/NO_ACTION/ESCALATE + dataclasses.
  awk '
    /^SKIP = / {emit=1}
    emit {print}
  ' "$SPEC/policy_types.py"
  echo ''
  echo ''
  # governance/policy.py body: drop its docstring/__future__/imports/__all__;
  # keep from the first "# ---" section marker onward (the weighted-point block,
  # PolicyEngine, helpers, org_wide_send_bar).
  awk '
    /^# ----/ {emit=1}
    emit {print}
  ' "$GOV/policy.py"
} > "$DEST/policy.py"

# --- signals.py : spec/signal_types (pure types) + governance/signals (logic) -
{
  echo '"""VENDORED from mainspring (ADR-0010, decision-policy). DO NOT EDIT BY HAND.'
  echo ''
  echo 'Assembled flat from mainspring/spec/signal_types.py (pure types) +'
  echo 'mainspring/governance/signals.py (gather_signals). Re-vendor via'
  echo 'backend/scripts/vendor_mainspring_policy.sh."""'
  echo 'from __future__ import annotations'
  echo ''
  echo 'from dataclasses import dataclass, field'
  echo 'from typing import Any, Protocol, runtime_checkable'
  echo ''
  echo 'from .budget import BudgetGovernor'
  echo 'from .pipeline import Entity'
  echo ''
  # spec/signal_types.py body: keep from the first dataclass onward.
  awk '
    /^@dataclass/ {emit=1}
    emit {print}
  ' "$SPEC/signal_types.py"
  echo ''
  echo ''
  # governance/signals.py body: keep the gather_signals def onward.
  awk '
    /^def gather_signals/ {emit=1}
    emit {print}
  ' "$GOV/signals.py"
} > "$DEST/signals.py"

# --- budget.py / pipeline.py : already self-contained pure modules -----------
# governance/budget.py and spec/pipeline.py carry no cross-layer imports, so
# copy verbatim (drop the __all__ line is unnecessary — they import cleanly).
cp "$GOV/budget.py" "$DEST/budget.py"
cp "$SPEC/pipeline.py" "$DEST/pipeline.py"

SHA="$(git -C "$MS" rev-parse --short HEAD 2>/dev/null || echo unknown)"
{
  echo "mainspring @ $SHA"
  echo "Assembled flat (post-carve) from:"
  echo "  spec/policy_types.py + governance/policy.py   -> policy.py"
  echo "  spec/signal_types.py + governance/signals.py  -> signals.py"
  echo "  governance/budget.py                          -> budget.py"
  echo "  spec/pipeline.py                              -> pipeline.py"
  echo "(ORM-free deterministic PolicyEngine + weighted decision-point cascade"
  echo " + its pure deps. Cross-layer imports rewritten to local relative ones.)"
  echo "Re-vendor: MAINSPRING_DIR=... bash backend/scripts/vendor_mainspring_policy.sh"
} > "$DEST/MAINSPRING_REF.txt"

echo "vendored mainspring policy engine @ $SHA -> $DEST"
