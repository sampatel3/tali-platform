"""Promotion-gate seam — the brand-agnostic, ORM-free surface a brand binds to.

This is the convergence seam (ADR-0010, cut #3): the minimal, dependency-light
contract a brand (e.g. tali-platform) imports to evaluate mainspring's
**promotion-gate decision** on the same policy-version metrics, WITHOUT pulling
in mainspring's ``Session``/``PolicyVersion``/ORM machinery or its DB-bound
sub-evaluators (``run_shadow`` / ``evaluate_holdout`` / ``audit``).

Why a thin seam rather than a copy of ``run_gate``: mainspring's
``platform/services/promotion_gate.py`` ``run_gate`` is ORM-coupled — it takes a
``Session``, queries rows, mutates ``PolicyVersion.status`` and writes an
``AuditEvent``. The *decision* embedded in it, though, is pure: three sub-checks
(shadow / holdout / bias) each produce a ``passed`` boolean, and the gate
composes them into a pass/fail plus a resulting policy status. That pure rule —
extracted verbatim from mainspring ``run_gate`` (master @ fa03ca7) — is what the
metering-style shadow comparator needs, and it is captured here.

Importable standalone: depends only on the stdlib, so a consumer can vendor
``seam.py`` and nothing else. Carries no I/O, no Session, no ORM symbol — it sits
cleanly on either side of the convergence CI gate.

Mirrors mainspring's gate composition exactly:

    passed = shadow.passed and holdout.passed and bias.passed
    status = FAILED_GATE            if not passed
             ACTIVE  (promoted)     if passed and auto_apply
             GATED   (not promoted) if passed and not auto_apply
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# Mainspring ``PolicyStatus`` values relevant to a gate outcome. Mirrors
# mainspring/platform/models/policy_version.py ``PolicyStatus`` (the string
# values, kept ORM-free — no SQLAlchemy ``Enum`` binding).
STATUS_ACTIVE = "active"
STATUS_GATED = "gated"
STATUS_FAILED_GATE = "failed_gate"


@dataclass(frozen=True)
class SubCheck:
    """One of the gate's three sub-evaluations, reduced to the only thing the
    composition rule reads: did it pass, and why. Mirrors the shared shape of
    mainspring's ``ShadowResult`` / ``HoldoutResult`` / ``BiasAuditResult`` —
    each exposes a ``passed`` bool and a list of reason/violation strings."""

    passed: bool
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GateDecision:
    """What mainspring's gate would decide for these sub-checks — the ORM-free
    core of ``run_gate``'s return + status mutation, with no row writes.

    ``passed`` is the composite (all three sub-checks passed). ``status`` is the
    ``PolicyStatus`` value the candidate would be flipped to. ``promoted`` is
    True only on a full pass with ``auto_apply`` (mainspring flips straight to
    ACTIVE); a passing gate without auto-apply lands in GATED, not promoted."""

    passed: bool
    status: str
    promoted: bool
    reasons: list[str] = field(default_factory=list)


def evaluate_gate(
    *,
    shadow: SubCheck,
    holdout: SubCheck,
    bias: SubCheck,
    auto_apply: bool = False,
) -> GateDecision:
    """The pure promotion-gate decision, extracted from mainspring ``run_gate``.

    Composes the three sub-checks identically to mainspring:

    * ``passed = shadow.passed and holdout.passed and bias.passed``
    * not passed → ``FAILED_GATE`` (never promoted)
    * passed and ``auto_apply`` → ``ACTIVE`` (promoted)
    * passed and not ``auto_apply`` → ``GATED`` (awaiting human activation)

    Reasons are aggregated in mainspring's prefix style (``shadow:`` /
    ``holdout:`` / ``bias:``) so a brand can diff reason provenance too, not just
    the boolean. No Session, no row mutation — that lives in mainspring's
    ORM-bound wrapper, not on the seam.
    """
    reasons: list[str] = []
    reasons.extend(f"shadow: {r}" for r in shadow.reasons if r != "passed")
    reasons.extend(
        f"holdout: {r}" for r in holdout.reasons if not r.startswith("passed")
    )
    reasons.extend(f"bias: {v}" for v in bias.reasons)

    passed = shadow.passed and holdout.passed and bias.passed
    if not passed:
        return GateDecision(
            passed=False,
            status=STATUS_FAILED_GATE,
            promoted=False,
            reasons=reasons,
        )
    if auto_apply:
        return GateDecision(
            passed=True,
            status=STATUS_ACTIVE,
            promoted=True,
            reasons=reasons or ["all checks passed"],
        )
    return GateDecision(
        passed=True,
        status=STATUS_GATED,
        promoted=False,
        reasons=reasons or ["all checks passed"],
    )


@runtime_checkable
class PromotionGate(Protocol):
    """The convergence contract: a promotion-gate decision independent of how the
    sub-checks are sourced (mainspring runs them against a ``Session`` + ORM
    rows; tali against its own ``PolicyVersion`` tables + fitted models). Both
    can satisfy this shape, so a call site can bind to the seam and the
    implementation can be swapped underneath without touching it."""

    def evaluate_gate(
        self,
        *,
        shadow: SubCheck,
        holdout: SubCheck,
        bias: SubCheck,
        auto_apply: bool = False,
    ) -> GateDecision:
        ...


__all__ = [
    "SubCheck",
    "GateDecision",
    "evaluate_gate",
    "PromotionGate",
    "STATUS_ACTIVE",
    "STATUS_GATED",
    "STATUS_FAILED_GATE",
]
