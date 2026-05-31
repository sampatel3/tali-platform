"""Bias-audit seam — the brand-agnostic, ORM-free fairness surface.

This is the convergence seam (ADR-0010, cut #4): the minimal, dependency-light
contract a brand (e.g. tali-platform) imports to evaluate the *fairness verdict*
(``passed`` + ``violations``) a candidate model produces on group metrics —
WITHOUT pulling in mainspring's ``Case``/``PolicyVersion``/``Session``/ORM
machinery. Cut #4 uses it for **shadow comparison**: the brand keeps its own
``decision_policy/bias_audit.py`` but ALSO scores the same per-group selection
rates through mainspring's verdict logic and logs whether the two fairness
verdicts agree, before any cutover.

Why a thin seam rather than a verbatim copy: mainspring's
``services/bias_audit.py`` ``audit()`` is **not** ORM-free — it runs
``db.execute(select(Case)...)`` to load + predict cases inline, so its verdict
logic is entangled with a DB session. The dataclasses (``GroupMetrics``,
``BiasAuditResult``) and the constants below ARE ORM-free and are copied
verbatim. The pure verdict function :func:`evaluate_demographic_parity` lifts
mainspring's *exact* demographic-parity rule out of ``audit()`` (group rate vs
GLOBAL rate, ``abs(group_rate - global_rate) > MAX_PARITY_GAP``, groups under
``MIN_GROUP_N`` skipped) so the shadow can run it on metrics tali already
computed, with no mainspring DB session.

Importable standalone: depends only on the stdlib, so a consumer can vendor
``seam.py`` and nothing else.

PARITY NOTE (surfaced by the shadow, tracked under ADR-0010 / ADR-0002): the two
engines do NOT share a fairness definition yet.
  * tali  — PAIRWISE across protected attributes: 4/5ths disparate-impact ratio
    + selection-rate gap + outcome gap + calibration (ECE) gap, default gaps
    ~0.05, ratio_min 0.80.
  * mainspring (here) — group-vs-GLOBAL demographic-parity, default gap 0.15
    (plus an equalized-odds mode needing labels).
The shadow compares the boolean ``passed`` verdict on the SAME per-group
selection rates; a schema-translation TODO (mapping tali's pairwise/attr-keyed
metrics onto mainspring's group-vs-global shape, and reconciling thresholds)
must be closed before any cutover. Tali must contribute the richer pairwise
audit UP into the substrate first per ADR-0002.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable


# --- copied verbatim from mainspring services/bias_audit.py (ORM-free) -------
AUDIT_GROUP_FIELD = "_audit_group"
MAX_PARITY_GAP = 0.15           # max abs difference in positive rate vs global
MAX_ODDS_GAP = 0.20             # max abs difference in TPR/FPR vs global
MIN_GROUP_N = 5                 # minimum cases per group to score


@dataclass(frozen=True)
class GroupMetrics:
    group: str
    n: int
    positive_rate: float
    tpr: Optional[float]          # true positive rate (None if no labels)
    fpr: Optional[float]          # false positive rate

    def as_dict(self) -> dict[str, Any]:
        return {
            "group": self.group, "n": self.n,
            "positive_rate": round(self.positive_rate, 3),
            "tpr": round(self.tpr, 3) if self.tpr is not None else None,
            "fpr": round(self.fpr, 3) if self.fpr is not None else None,
        }


@dataclass(frozen=True)
class BiasAuditResult:
    candidate_id: int
    scoring: str                              # "demographic_parity" | "equalized_odds"
    n_groups: int
    global_positive_rate: float
    group_metrics: list[GroupMetrics]
    violations: list[str] = field(default_factory=list)
    passed: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "scoring": self.scoring,
            "n_groups": self.n_groups,
            "global_positive_rate": round(self.global_positive_rate, 3),
            "group_metrics": [g.as_dict() for g in self.group_metrics],
            "violations": list(self.violations),
            "passed": self.passed,
        }


# --- thin ORM-free seam: mainspring's demographic-parity verdict, lifted -----
# out of ``audit()`` so it runs on per-group selection rates the brand already
# has, with no Session / Case / PolicyVersion. The rule is mainspring's exact
# one: each group whose n >= MIN_GROUP_N must sit within ``max_parity_gap`` of
# the GLOBAL positive (selection) rate.
@dataclass(frozen=True)
class GroupRate:
    """One group's selection/positive rate + sample size — the ORM-free input
    the brand feeds in (it already computed these in its own audit)."""

    group: str
    n: int
    positive_rate: float


def evaluate_demographic_parity(
    *,
    candidate_id: int,
    group_rates: Sequence[GroupRate],
    global_positive_rate: float,
    max_parity_gap: float = MAX_PARITY_GAP,
    min_group_n: int = MIN_GROUP_N,
) -> BiasAuditResult:
    """Mainspring's demographic-parity verdict on pre-computed group rates.

    Mirrors ``services/bias_audit.py:audit()`` (the ``scoring ==
    "demographic_parity"`` branch) WITHOUT a DB: groups under ``min_group_n``
    are skipped; every scored group must be within ``max_parity_gap`` of the
    global rate or it is a violation; ``passed`` iff no violations.
    """
    group_metrics: list[GroupMetrics] = []
    violations: list[str] = []
    for gr in group_rates:
        if gr.n < min_group_n:
            continue
        group_metrics.append(GroupMetrics(
            group=gr.group, n=gr.n, positive_rate=gr.positive_rate,
            tpr=None, fpr=None,
        ))
        gap = abs(gr.positive_rate - global_positive_rate)
        if gap > max_parity_gap:
            violations.append(
                f"group {gr.group!r}: positive_rate {gr.positive_rate:.2f} vs "
                f"global {global_positive_rate:.2f} — gap {gap:.2f} > {max_parity_gap:.2f}"
            )
    return BiasAuditResult(
        candidate_id=candidate_id,
        scoring="demographic_parity",
        n_groups=len(group_metrics),
        global_positive_rate=global_positive_rate,
        group_metrics=group_metrics,
        violations=violations,
        passed=not violations,
    )


@runtime_checkable
class BiasAuditor(Protocol):
    """The convergence contract: a fairness verdict over pre-computed group
    rates, independent of how the brand loaded/predicted the cases (mainspring
    keys on ``Case``/``PolicyVersion``/``Session``; tali on ``AuditExample``s +
    its own thresholds). Both can satisfy this shape once the schema is mapped.
    """

    def evaluate_demographic_parity(
        self,
        *,
        candidate_id: int,
        group_rates: Sequence[GroupRate],
        global_positive_rate: float,
    ) -> BiasAuditResult:
        ...


def group_rates_from_mapping(rates: Mapping[str, tuple[int, float]]) -> list[GroupRate]:
    """Convenience: build :class:`GroupRate` list from ``{group: (n, rate)}``."""
    return [GroupRate(group=g, n=int(n), positive_rate=float(r)) for g, (n, r) in rates.items()]


__all__ = [
    "AUDIT_GROUP_FIELD",
    "MAX_PARITY_GAP",
    "MAX_ODDS_GAP",
    "MIN_GROUP_N",
    "GroupMetrics",
    "BiasAuditResult",
    "GroupRate",
    "evaluate_demographic_parity",
    "BiasAuditor",
    "group_rates_from_mapping",
]
