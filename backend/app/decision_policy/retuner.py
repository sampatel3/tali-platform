"""Heuristic retuner — DEPRECATED. Replaced by the fitted policy model (§6).

DEPRECATION (single-version cleanup, May 2026)
----------------------------------------------
Per §6 + §9 of ``recruitment_system_architecture.md`` the fitted policy
model (``app.decision_policy.fitted_policy``) is the canonical
replacement. The fitted model handles signal composition and threshold
adaptation through learned coefficients with isotonic calibration —
the pattern shifts in this module are subsumed.

Sunset target: when at least one ``PolicyVersion`` has been promoted
(``status='live'``) for ≥60 days via the promotion gate, this module
and ``nightly_retune.py`` can be deleted. Until then it remains the
ONLY active learning path — the fitted-policy nightly fit produces
candidate rows but nothing has been promoted yet.

DO NOT add new pattern shifts here. New learning logic belongs in
``fitted_policy.py``.

----------------------------------------------

Original docstring follows.

Public surface:

  ``Retuner`` (Protocol) — swappable interface; v2 plugs in a learned
  implementation without touching the aggregator or the engine.

  ``HeuristicRetuner`` — v1 implementation. Per decision point:
    - count weighted disagreements per pattern,
    - apply deterministic shifts:
        * manual-send-on-would-reject → loosen role_fit_min
        * manual-reject-on-would-send → tighten role_fit_min
        * manual-advance-on-would-reject-post-assessment → loosen taali_score_min
        * manual-reject-on-would-advance → tighten taali_score_min
        * failure_mode='wrong_threshold' → larger shift in same direction
        * failure_mode='missing_signal' + graph weight low → bump
          weights.graph_prior_p_advance by 0.05 (capped at 0.4),
          re-normalise other weights to keep sum = 1.0
    - cap any single shift at MAX_SHIFT_PER_DIMENSION (default 5%).
"""

from __future__ import annotations

import copy
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from .feedback_aggregator import AggregatedSignals, Signal
from .schema import PolicyJson


logger = logging.getLogger("taali.decision_policy.retuner")


# Defaults tuned for v1. Adjustable via Organization workspace_settings
# (see CLAUDE.md §10) without code changes.
MAX_SHIFT_PER_DIMENSION = 5.0
MAX_GRAPH_PRIOR_WEIGHT = 0.4
MIN_SIGNALS_FOR_RETUNE = 10


@dataclass
class _Shift:
    field_path: str  # e.g. 'send_assessment.thresholds.role_fit_min'
    old_value: float
    new_value: float
    cause_summary: str
    contributing_source_ids: list[int] = field(default_factory=list)


@dataclass
class RetuneProposal:
    new_policy_json: dict[str, Any]
    shifts: list[_Shift]
    signal_count: int
    weighted_signal_total: float

    @property
    def has_changes(self) -> bool:
        return bool(self.shifts)


class Retuner(Protocol):
    def propose(
        self, current: PolicyJson, signals: AggregatedSignals
    ) -> RetuneProposal | None:
        ...


# ---------------------------------------------------------------------------
# Heuristic implementation
# ---------------------------------------------------------------------------


class HeuristicRetuner:
    """v1: per-pattern deterministic threshold/weight shifts."""

    def __init__(
        self,
        *,
        max_shift_per_dimension: float = MAX_SHIFT_PER_DIMENSION,
        max_graph_prior_weight: float = MAX_GRAPH_PRIOR_WEIGHT,
        min_signals: int = MIN_SIGNALS_FOR_RETUNE,
    ) -> None:
        self.max_shift = float(max_shift_per_dimension)
        self.max_graph_weight = float(max_graph_prior_weight)
        self.min_signals = int(min_signals)

    def propose(
        self, current: PolicyJson, signals: AggregatedSignals
    ) -> RetuneProposal | None:
        if signals.total_weighted < self.min_signals:
            logger.info(
                "retune skip: weighted_total=%.2f < min_signals=%d",
                signals.total_weighted, self.min_signals,
            )
            return None

        new_json = current.model_dump()
        shifts: list[_Shift] = []

        # Bucket signals by pattern + decision point for clean math.
        per_pattern = _bucket(signals.signals)

        # 1. Threshold shifts driven by recruiter disagreement patterns.
        shifts.extend(self._threshold_shifts_from_patterns(new_json, per_pattern))

        # 2. Threshold shifts driven by explicit teach feedback.
        shifts.extend(self._threshold_shifts_from_failures(new_json, per_pattern))

        # 3. Graph-prior weight bump from missing-signal failures.
        shifts.extend(self._graph_weight_bump(new_json, per_pattern))

        # 4. Stamp metadata.
        new_json.setdefault("metadata", {})
        new_json["metadata"]["trained_from_feedback_ids"] = signals.feedback_ids
        new_json["metadata"]["trained_from_manual_decision_count"] = signals.manual_count
        from datetime import datetime, timezone

        new_json["metadata"]["trained_at"] = datetime.now(timezone.utc).isoformat()
        notes_lines = [
            f"Retuned from {signals.teach_count} teach + "
            f"{signals.manual_count} manual + {signals.override_count} override signals."
        ]
        for shift in shifts:
            notes_lines.append(f"  • {shift.field_path}: {shift.cause_summary}")
        new_json["metadata"]["notes"] = "\n".join(notes_lines)

        return RetuneProposal(
            new_policy_json=new_json,
            shifts=shifts,
            signal_count=len(signals.signals),
            weighted_signal_total=signals.total_weighted,
        )

    # ------------------------------------------------------------------
    # Pattern-driven threshold shifts
    # ------------------------------------------------------------------

    def _threshold_shifts_from_patterns(
        self,
        new_json: dict[str, Any],
        per_pattern: dict[str, dict[str | None, list[Signal]]],
    ) -> list[_Shift]:
        shifts: list[_Shift] = []
        # send_assessment thresholds.
        shifts.extend(
            self._shift_threshold(
                new_json,
                point="send_assessment",
                threshold_key="role_fit_min",
                pattern="manual-send-on-would-reject",
                direction=-1,  # loosen
                signals=per_pattern.get("manual-send-on-would-reject", {}),
            )
        )
        shifts.extend(
            self._shift_threshold(
                new_json,
                point="send_assessment",
                threshold_key="role_fit_min",
                pattern="manual-reject-on-would-send",
                direction=+1,  # tighten
                signals=per_pattern.get("manual-reject-on-would-send", {}),
            )
        )
        # advance thresholds.
        shifts.extend(
            self._shift_threshold(
                new_json,
                point="advance_to_interview",
                threshold_key="taali_score_min",
                pattern="manual-advance-on-would-reject-post-assessment",
                direction=-1,
                signals=per_pattern.get(
                    "manual-advance-on-would-reject-post-assessment", {}
                ),
            )
        )
        shifts.extend(
            self._shift_threshold(
                new_json,
                point="advance_to_interview",
                threshold_key="taali_score_min",
                pattern="manual-reject-on-would-advance",
                direction=+1,
                signals=per_pattern.get("manual-reject-on-would-advance", {}),
            )
        )
        return shifts

    # ------------------------------------------------------------------
    # Explicit-teach threshold shifts
    # ------------------------------------------------------------------

    def _threshold_shifts_from_failures(
        self,
        new_json: dict[str, Any],
        per_pattern: dict[str, dict[str | None, list[Signal]]],
    ) -> list[_Shift]:
        shifts: list[_Shift] = []
        # ``wrong_threshold`` failures: larger shift in same direction
        # as the most-recent teach correction. Without an embedded
        # signed magnitude in correction_text, we use the sign of the
        # decision_type (reject → loosen, send → tighten — i.e. the
        # recruiter says "you set the bar wrong, fix it the other
        # way"). For v1 we conservatively apply a +/- max_shift to the
        # send_assessment role_fit_min based on count alone.
        wt_signals = list(_iter_signals(per_pattern, "failure-mode:wrong_threshold"))
        if wt_signals:
            # Direction defaults to -1 (loosen) — the more common
            # recruiter complaint is "agent is too strict". Phase 6 UI
            # captures explicit direction.
            shifts.extend(
                self._shift_threshold(
                    new_json,
                    point="send_assessment",
                    threshold_key="role_fit_min",
                    pattern="failure-mode:wrong_threshold",
                    direction=-1,
                    signals={"send_assessment": wt_signals},
                    extra_magnitude=1.5,  # larger than pattern-only shifts
                )
            )
        return shifts

    # ------------------------------------------------------------------
    # Graph-prior weight bump
    # ------------------------------------------------------------------

    def _graph_weight_bump(
        self,
        new_json: dict[str, Any],
        per_pattern: dict[str, dict[str | None, list[Signal]]],
    ) -> list[_Shift]:
        shifts: list[_Shift] = []
        ms_signals = list(_iter_signals(per_pattern, "failure-mode:missing_signal"))
        if not ms_signals:
            return shifts
        # Bump graph_prior_p_advance weight on send_assessment by 0.05
        # (capped). Re-normalise the other weights so the total stays 1.0.
        point = (new_json.get("decision_points") or {}).get("send_assessment")
        if not isinstance(point, dict):
            return shifts
        weights = dict(point.get("weights") or {})
        if not weights:
            return shifts
        current_graph = float(weights.get("graph_prior_p_advance", 0.0) or 0.0)
        target = min(self.max_graph_weight, current_graph + 0.05)
        if abs(target - current_graph) < 1e-6:
            return shifts
        delta = target - current_graph
        # Subtract the delta proportionally from the other weights.
        other_keys = [k for k in weights if k != "graph_prior_p_advance"]
        other_total = sum(weights[k] for k in other_keys)
        if other_total <= 0.0:
            return shifts
        for k in other_keys:
            weights[k] = max(0.0, weights[k] - delta * (weights[k] / other_total))
        weights["graph_prior_p_advance"] = target
        # Renormalise to absorb floating-point drift.
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
        old = dict(point.get("weights") or {})
        point["weights"] = weights
        shifts.append(
            _Shift(
                field_path="send_assessment.weights.graph_prior_p_advance",
                old_value=current_graph,
                new_value=weights["graph_prior_p_advance"],
                cause_summary=(
                    f"{len(ms_signals)} missing-signal teach event(s) — "
                    "bumping graph prior weight."
                ),
                contributing_source_ids=[int(s.source_id) for s in ms_signals],
            )
        )
        # Surface the renormalisation as a single annotation.
        for k in other_keys:
            if abs((old.get(k, 0.0)) - weights[k]) > 1e-6:
                shifts.append(
                    _Shift(
                        field_path=f"send_assessment.weights.{k}",
                        old_value=float(old.get(k, 0.0)),
                        new_value=float(weights[k]),
                        cause_summary="renormalised after graph-weight bump",
                    )
                )
        return shifts

    # ------------------------------------------------------------------
    # Shared shift logic
    # ------------------------------------------------------------------

    def _shift_threshold(
        self,
        new_json: dict[str, Any],
        *,
        point: str,
        threshold_key: str,
        pattern: str,
        direction: int,
        signals: dict[str | None, list[Signal]] | list[Signal],
        extra_magnitude: float = 1.0,
    ) -> list[_Shift]:
        if isinstance(signals, dict):
            point_signals = signals.get(point) or signals.get(None) or []
        else:
            point_signals = signals
        if not point_signals:
            return []
        weighted_count = sum(s.weight for s in point_signals)
        if weighted_count <= 0:
            return []
        # Magnitude grows with weighted count via tanh — saturates at
        # max_shift quickly so a flood of signals never overshoots.
        magnitude = self.max_shift * extra_magnitude * math.tanh(weighted_count / 5.0)
        magnitude = min(self.max_shift * extra_magnitude, magnitude)
        delta = direction * magnitude

        point_obj = (new_json.get("decision_points") or {}).get(point)
        if not isinstance(point_obj, dict):
            return []
        thresholds = dict(point_obj.get("thresholds") or {})
        if threshold_key not in thresholds:
            return []
        old_value = float(thresholds[threshold_key])
        new_value = max(0.0, min(100.0, old_value + delta))
        if abs(new_value - old_value) < 1e-6:
            return []
        thresholds[threshold_key] = new_value
        point_obj["thresholds"] = thresholds
        return [
            _Shift(
                field_path=f"{point}.thresholds.{threshold_key}",
                old_value=old_value,
                new_value=new_value,
                cause_summary=(
                    f"{len(point_signals)} '{pattern}' signal(s) "
                    f"(weighted={weighted_count:.1f}); "
                    f"shift {delta:+.2f}"
                ),
                contributing_source_ids=[int(s.source_id) for s in point_signals],
            )
        ]


# ---------------------------------------------------------------------------
# Bucketing helpers
# ---------------------------------------------------------------------------


def _bucket(
    signals: list[Signal],
) -> dict[str, dict[str | None, list[Signal]]]:
    out: dict[str, dict[str | None, list[Signal]]] = {}
    for s in signals:
        per_point = out.setdefault(s.disagreement_pattern, {})
        per_point.setdefault(s.decision_point, []).append(s)
    return out


def _iter_signals(
    per_pattern: dict[str, dict[str | None, list[Signal]]], pattern: str
) -> Iterable[Signal]:
    points = per_pattern.get(pattern) or {}
    for sigs in points.values():
        for s in sigs:
            yield s


__all__ = [
    "MAX_GRAPH_PRIOR_WEIGHT",
    "MAX_SHIFT_PER_DIMENSION",
    "MIN_SIGNALS_FOR_RETUNE",
    "HeuristicRetuner",
    "RetuneProposal",
    "Retuner",
]
