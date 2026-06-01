"""Autoresearch — a fairness-gated search loop over fitted models.

The iterative sibling of :func:`learning.retune`. Where ``retune`` proposes ONE
shifted policy and gates it, autoresearch *searches*: propose a config, fit it,
score it on a held-out set, require the fairness audit to PASS, keep it iff it
beats the incumbent, and repeat — keeping the trial log as memory.

The shape mirrors Karpathy's autoresearch: a constraint + one mechanical metric
+ an autonomous keep/discard loop = compounding gains. Here the constraint is
the fairness audit — a HARD guardrail, never an objective. Its thresholds are
compliance constants; a config that lowers the metric but trips the audit is
discarded, never kept. Auto-applying the winner still runs the downstream
promotion gate — this loop only ever produces a *candidate*.

Pure engine logic: stdlib only, no ORM, no LLM, no platform machinery. Brands
inject everything domain-specific as callables, so the substrate owns the loop
while each brand keeps its own model, metric, and fairness rule:

- ``fit_fn(config) -> artifact``    train one config (closes over the data).
- ``score_fn(artifact) -> float``   held-out error; lower is better by default.
- ``audit_fn(artifact) -> (passed, n_violations)``  the fairness constraint.
- a :class:`Proposer`               sequences experiments (grid walk, or an LLM).

The artifact is opaque to the loop — a brand may return ``(model, metrics)`` and
read both off :attr:`SearchResult.best_artifact`, so no redundant refit is needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence

# An opaque hyperparameter point. The loop only ever compares configs for
# equality and hands them back to the brand's ``fit_fn`` / ``Proposer``.
Config = dict

# Minimum metric improvement worth keeping — guards against churning the policy
# for noise-level wins.
MIN_IMPROVEMENT = 1e-4


@dataclass
class Trial:
    """One fit-evaluate step in the loop."""

    config: Config
    score: float | None  # None when the fit could not be scored
    constraint_passed: bool
    violations: int
    kept: bool
    note: str = ""


@dataclass
class Proposal:
    """What a proposer wants tried next. ``stop`` ends the search."""

    config: Config
    rationale: str = ""
    stop: bool = False


class Proposer(Protocol):
    """Decides the next config given the trial history. ``None``/``stop`` ends it."""

    def propose(
        self,
        *,
        history: Sequence[Trial],
        best_config: Config,
        best_score: float | None,
    ) -> Proposal | None: ...


@dataclass
class SearchResult:
    """Outcome of a search. ``accepted`` is False when nothing cleared the gate."""

    accepted: bool
    best_config: Config
    best_artifact: Any
    best_score: float | None
    trials: list[Trial] = field(default_factory=list)
    baseline_config: Config | None = None

    @property
    def baseline_score(self) -> float | None:
        for t in self.trials:
            if t.config == self.baseline_config:
                return t.score
        return None


@dataclass
class GridProposer:
    """Deterministic coordinate descent over a per-knob value grid.

    Each call rescans ``grid`` in fixed order against the current incumbent and
    the trial history, returning the first untried ``(knob, value)`` that differs
    from the incumbent — so an accepted improvement narrows subsequent proposals.
    Returns ``None`` (ends the search) once every off-incumbent grid neighbour of
    the incumbent has been tried.
    """

    grid: dict[str, list]

    def propose(
        self,
        *,
        history: Sequence[Trial],
        best_config: Config,
        best_score: float | None,
    ) -> Proposal | None:
        tried = [t.config for t in history]
        for knob, values in self.grid.items():
            for value in values:
                if best_config.get(knob) == value:
                    continue
                cand = {**best_config, knob: value}
                if cand in tried:
                    continue
                return Proposal(config=cand, rationale=f"{knob}={value}")
        return None


def search(
    *,
    baseline_config: Config,
    fit_fn: Callable[[Config], Any],
    score_fn: Callable[[Any], float | None],
    audit_fn: Callable[[Any], tuple[bool, int]],
    proposer: Proposer,
    max_iters: int = 50,
    min_improvement: float = MIN_IMPROVEMENT,
    minimize: bool = True,
) -> SearchResult:
    """Run the fairness-gated search.

    Anchors on ``baseline_config`` (so the winner can only match or beat what
    ships today), then lets ``proposer`` drive: fit each proposal, score it,
    audit it, and *keep* it iff it (a) clears the audit and (b) strictly beats
    the incumbent score by ``min_improvement``. Otherwise discard. The returned
    winner is always constraint-clean; if nothing — not even the baseline —
    clears the audit, ``accepted`` is False and ``best_artifact`` is None.
    """
    trials: list[Trial] = []

    def run(cfg: Config, note: str) -> tuple[Trial, Any]:
        artifact = fit_fn(cfg)
        score = score_fn(artifact) if artifact is not None else None
        passed, n_viol = audit_fn(artifact) if artifact is not None else (False, 0)
        t = Trial(
            config=dict(cfg),
            score=score,
            constraint_passed=bool(passed),
            violations=int(n_viol),
            kept=False,
            note=note,
        )
        trials.append(t)
        return t, artifact

    def beats(candidate: float, incumbent: float) -> bool:
        return (
            candidate < incumbent - min_improvement
            if minimize
            else candidate > incumbent + min_improvement
        )

    base_t, base_art = run(baseline_config, "baseline")
    best_config = dict(baseline_config)
    best_artifact: Any = None
    best_score: float | None = None
    if base_t.constraint_passed and base_t.score is not None:
        base_t.kept = True
        best_artifact = base_art
        best_score = base_t.score

    for _ in range(max_iters):
        proposal = proposer.propose(
            history=trials, best_config=best_config, best_score=best_score
        )
        if proposal is None or proposal.stop:
            break
        cand = dict(proposal.config)
        if any(t.config == cand for t in trials):
            # Proposer repeated a tried config — treat as a convergence signal.
            break
        t, art = run(cand, (proposal.rationale or "")[:160])
        if not t.constraint_passed:
            t.note += " [discarded: constraint]"
            continue
        if t.score is None:
            t.note += " [discarded: unscored]"
            continue
        if best_score is None or beats(t.score, best_score):
            t.kept = True
            best_config = cand
            best_artifact = art
            best_score = t.score
        else:
            t.note += " [discarded: no improvement]"

    return SearchResult(
        accepted=best_score is not None,
        best_config=best_config,
        best_artifact=best_artifact,
        best_score=best_score,
        trials=trials,
        baseline_config=dict(baseline_config),
    )


def summarize(result: SearchResult, *, mode: str = "grid") -> dict:
    """Compact, JSON-safe trace of a search for a brand's metrics column."""
    return {
        "mode": mode,
        "accepted": result.accepted,
        "n_trials": len(result.trials),
        "best_config": dict(result.best_config),
        "best_score": result.best_score,
        "baseline_score": result.baseline_score,
        "kept_configs": [dict(t.config) for t in result.trials if t.kept],
    }


__all__ = [
    "Config",
    "GridProposer",
    "MIN_IMPROVEMENT",
    "Proposal",
    "Proposer",
    "SearchResult",
    "Trial",
    "search",
    "summarize",
]
