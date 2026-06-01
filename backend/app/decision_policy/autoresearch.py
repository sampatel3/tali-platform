"""Autoresearch — a bias-gated search loop over the fitted-policy hyperparameters.

Inspired by Karpathy's ``autoresearch`` (constraint + one mechanical metric +
autonomous keep/discard loop). The mapping onto tali's decision policy:

  - **Editable surface** (Karpathy's ``train.py``): the fit hyperparameters in
    :class:`HyperConfig` — L2, learning rate, iterations, pooling saturation,
    and whether isotonic calibration is applied. The production fit
    (:func:`fitted_policy.fit_model`) hard-codes these; here we search them.
  - **Metric** (Karpathy's ``val_bpb``): gold-set holdout log-loss — *lower is
    better*. This is exactly the number the Phase-5 promotion gate already
    judges (:func:`promotion_gate.evaluate_gold_set`).
  - **Constraint** (the fairness-specific part): the bias audit must *pass* on
    the protected-attribute holdout. The EEOC 4/5ths verdict and its thresholds
    are compliance-signed constants — they are a hard guardrail we never tune,
    not an objective we optimise. A candidate that lowers log-loss but trips the
    bias audit is *discarded*, never kept.
  - **Keep / discard + memory** (Karpathy's git): greedy coordinate descent —
    accept a one-knob change iff it strictly improves the metric *and* clears the
    bias gate; otherwise revert. Every trial is logged in :attr:`SearchResult.trials`.

This module is deliberately offline and side-effect-free: it fits in-memory
candidate models and returns the winner. It does not write ``PolicyVersion``
rows or flip anything live — that remains the job of the existing promotion
gate, which this loop feeds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, Sequence

from .bias_audit import AuditExample, BiasThresholds, audit, load_thresholds
from .fitted_policy import FittedModel, TrainingExample, fit_model

logger = logging.getLogger("taali.decision_policy.autoresearch")


# Minimum log-loss improvement to bother keeping a change. Guards against
# accepting noise-level wins that would just churn the policy.
MIN_IMPROVEMENT = 1e-4


@dataclass(frozen=True)
class HyperConfig:
    """The editable surface — one point in fit-hyperparameter space."""

    l2: float = 0.01
    learning_rate: float = 0.05
    max_iter: int = 200
    pooling_saturation: int = 100
    calibrate: bool = True


# The production defaults — the loop's starting point, so it can only ever
# improve on (or match) what ships today.
BASELINE = HyperConfig()

# Candidate values per knob. The loop tries each alternative against the
# current best, one knob at a time (coordinate descent over this grid).
SEARCH_GRID: dict[str, list] = {
    "l2": [0.0, 0.001, 0.01, 0.1],
    "learning_rate": [0.02, 0.05, 0.1],
    "max_iter": [200, 400],
    "pooling_saturation": [50, 100, 200],
    "calibrate": [True, False],
}


@dataclass
class Trial:
    """One fit-evaluate step in the loop."""

    config: HyperConfig
    log_loss: float | None  # None when the fit could not be scored
    bias_passed: bool
    violations: int
    kept: bool
    note: str = ""


@dataclass
class SearchResult:
    """Outcome of a search. ``accepted`` is False when nothing cleared the gate."""

    accepted: bool
    best_config: HyperConfig
    best_model: FittedModel | None
    best_log_loss: float | None
    trials: list[Trial] = field(default_factory=list)
    # The fit metrics for the winning model (``fit_model``'s metrics dict), so a
    # caller can persist them on the PolicyVersion exactly as the one-shot fit does.
    best_metrics: dict | None = None

    @property
    def baseline_log_loss(self) -> float | None:
        for t in self.trials:
            if t.config == BASELINE:
                return t.log_loss
        return None


def summarize(result: "SearchResult", *, mode: str) -> dict:
    """Compact, JSON-safe trace of a search for ``PolicyVersion.metrics_json``."""
    from dataclasses import asdict

    return {
        "mode": mode,
        "accepted": result.accepted,
        "n_trials": len(result.trials),
        "best_config": asdict(result.best_config),
        "best_log_loss": result.best_log_loss,
        "baseline_log_loss": result.baseline_log_loss,
        "kept_configs": [asdict(t.config) for t in result.trials if t.kept],
    }


def _evaluate(
    config: HyperConfig,
    *,
    train_examples: Sequence[TrainingExample],
    gold_set: Sequence[TrainingExample],
    audit_examples: Sequence[AuditExample],
    thresholds: BiasThresholds,
    role_id: int | None,
) -> tuple[float | None, bool, int]:
    """Fit one config and return ``(holdout_log_loss, bias_passed, n_violations)``.

    The metric and the constraint are evaluated on the same fitted model:
    log-loss on the gold set, the bias verdict on the protected-attribute
    holdout. A fit that yields no holdout score returns ``(None, ...)`` and is
    treated as un-scorable (never kept).
    """
    model, metrics = fit_model(
        train_examples,
        role_id=role_id,
        gold_set=gold_set,
        l2=config.l2,
        learning_rate=config.learning_rate,
        max_iter=config.max_iter,
        pooling_saturation=config.pooling_saturation,
        calibrate=config.calibrate,
    )
    log_loss = metrics.get("holdout_log_loss")
    _, violations = audit(model=model, examples=audit_examples, thresholds=thresholds)
    return log_loss, (len(violations) == 0), len(violations)


def search(
    *,
    train_examples: Sequence[TrainingExample],
    gold_set: Sequence[TrainingExample],
    audit_examples: Sequence[AuditExample],
    role_id: int | None = None,
    thresholds: BiasThresholds | None = None,
    max_iters: int = 50,
    proposer: "Proposer | None" = None,
) -> SearchResult:
    """Run the bias-gated hyperparameter search.

    Default (``proposer=None``): greedy coordinate descent from :data:`BASELINE`
    — walk the search grid one knob at a time, fit a candidate, and *keep* it iff
    it (a) clears the bias audit and (b) strictly beats the current best holdout
    log-loss by at least :data:`MIN_IMPROVEMENT`. Otherwise discard and move on.

    Agentic (``proposer`` supplied): the same verify / keep-discard / log harness,
    but the *propose the next experiment* step is delegated to a :class:`Proposer`
    — e.g. :class:`LLMProposer`, which reads the full trial history and reasons
    about what to try next (and when to stop), rather than walking a fixed grid.
    The metric, the bias constraint, and the rollback semantics are identical, so
    the safety guarantees do not change: the returned winner is always bias-clean,
    and ``accepted`` is False with ``best_model=None`` if nothing clears the gate.
    """
    if proposer is not None:
        return _agentic_search(
            train_examples=train_examples,
            gold_set=gold_set,
            audit_examples=audit_examples,
            role_id=role_id,
            thresholds=thresholds,
            max_iters=max_iters,
            proposer=proposer,
        )

    thr = thresholds or load_thresholds()
    trials: list[Trial] = []

    def run(cfg: HyperConfig, note: str) -> Trial:
        log_loss, bias_ok, n_viol = _evaluate(
            cfg,
            train_examples=train_examples,
            gold_set=gold_set,
            audit_examples=audit_examples,
            thresholds=thr,
            role_id=role_id,
        )
        t = Trial(
            config=cfg,
            log_loss=log_loss,
            bias_passed=bias_ok,
            violations=n_viol,
            kept=False,
            note=note,
        )
        trials.append(t)
        return t

    # 1. Anchor on the baseline. It only becomes the incumbent if it is itself
    #    bias-clean and scorable; otherwise we have no valid incumbent yet and
    #    the first passing candidate takes the lead.
    base = run(BASELINE, "baseline")
    best_cfg = BASELINE
    best_model: FittedModel | None = None
    best_loss: float | None = None
    if base.bias_passed and base.log_loss is not None:
        base.kept = True
        best_loss = base.log_loss
        best_model = None  # refit lazily on return; cheap and keeps trials light

    # 2. Coordinate descent over the grid.
    iters = 0
    for knob, candidates in SEARCH_GRID.items():
        for value in candidates:
            if iters >= max_iters:
                break
            if getattr(best_cfg, knob) == value:
                continue  # already the incumbent value for this knob
            iters += 1
            cand = replace(best_cfg, **{knob: value})
            if any(t.config == cand for t in trials):
                continue  # already evaluated this exact point
            t = run(cand, f"{knob}={value}")
            if not t.bias_passed:
                t.note += " [discarded: bias]"
                continue
            if t.log_loss is None:
                t.note += " [discarded: unscorable]"
                continue
            improved = best_loss is None or t.log_loss < best_loss - MIN_IMPROVEMENT
            if improved:
                t.kept = True
                best_cfg = cand
                best_loss = t.log_loss
            else:
                t.note += " [discarded: no improvement]"

    accepted = best_loss is not None
    best_metrics: dict | None = None
    if accepted:
        # Refit the winner once so the caller gets the model object + metrics.
        best_model, best_metrics = fit_model(
            train_examples,
            role_id=role_id,
            gold_set=gold_set,
            l2=best_cfg.l2,
            learning_rate=best_cfg.learning_rate,
            max_iter=best_cfg.max_iter,
            pooling_saturation=best_cfg.pooling_saturation,
            calibrate=best_cfg.calibrate,
        )
        logger.info(
            "autoresearch: %d trials, best log_loss=%.5f at %s",
            len(trials),
            best_loss,
            best_cfg,
        )
    else:
        logger.warning(
            "autoresearch: no bias-clean config found over %d trials; "
            "returning baseline unaccepted",
            len(trials),
        )

    return SearchResult(
        accepted=accepted,
        best_config=best_cfg,
        best_model=best_model,
        best_log_loss=best_loss,
        trials=trials,
        best_metrics=best_metrics,
    )


# ---------------------------------------------------------------------------
# Agentic layer — an LLM proposes the next experiment instead of a fixed grid.
# ---------------------------------------------------------------------------


@dataclass
class Proposal:
    """One step the proposer wants the loop to take."""

    config: HyperConfig
    rationale: str = ""
    stop: bool = False  # proposer believes further search won't help


class Proposer(Protocol):
    """Decides the next config to try given everything seen so far.

    Return ``None`` or ``Proposal(stop=True)`` to end the search.
    """

    def propose(
        self,
        *,
        history: Sequence[Trial],
        best_config: HyperConfig,
        best_log_loss: float | None,
    ) -> Proposal | None: ...


# Defensive bounds — the agent's proposals are clamped into these before a fit,
# so a hallucinated value can never blow up training time or destabilise the fit.
_BOUNDS = {
    "l2": (0.0, 1.0),
    "learning_rate": (1e-3, 0.5),
    "max_iter": (50, 1000),
    "pooling_saturation": (10, 500),
}


def _clamp_config(cfg: HyperConfig) -> HyperConfig:
    def c(name: str, v):
        lo, hi = _BOUNDS[name]
        return type(v)(max(lo, min(hi, v)))

    return HyperConfig(
        l2=c("l2", float(cfg.l2)),
        learning_rate=c("learning_rate", float(cfg.learning_rate)),
        max_iter=c("max_iter", int(cfg.max_iter)),
        pooling_saturation=c("pooling_saturation", int(cfg.pooling_saturation)),
        calibrate=bool(cfg.calibrate),
    )


def _history_table(history: Sequence[Trial]) -> str:
    rows = ["idx | l2 | lr | max_iter | pool_sat | calib | log_loss | bias | kept"]
    for i, t in enumerate(history):
        c = t.config
        ll = f"{t.log_loss:.5f}" if t.log_loss is not None else "n/a"
        bias = "PASS" if t.bias_passed else f"FAIL({t.violations})"
        rows.append(
            f"{i} | {c.l2} | {c.learning_rate} | {c.max_iter} | "
            f"{c.pooling_saturation} | {c.calibrate} | {ll} | {bias} | {t.kept}"
        )
    return "\n".join(rows)


class LLMProposer:
    """Agentic proposer — Claude reads the trial log and reasons about the next fit.

    This is the genuinely *agentic* element: instead of walking :data:`SEARCH_GRID`,
    the model sees every config tried, its holdout log-loss, and whether it cleared
    the bias audit, then proposes the next experiment (or stops). It can suggest
    off-grid values and adapt its strategy to what worked — but it cannot weaken the
    guardrails: the loop still fits, scores, and bias-gates every proposal exactly
    as the mechanical search does. All calls go through the metered client.
    """

    SYSTEM = (
        "You are an optimisation researcher tuning a logistic decision model for a "
        "hiring platform. Goal: MINIMISE holdout log-loss (lower is better). HARD "
        "CONSTRAINT: a fit that fails the fairness/bias audit is useless no matter "
        "its log-loss — never chase a config similar to one marked bias=FAIL.\n\n"
        "Editable knobs and sane ranges:\n"
        "- l2 (0.0–1.0): L2 regularisation strength.\n"
        "- learning_rate (0.001–0.5): gradient step size.\n"
        "- max_iter (50–1000): gradient-descent iterations.\n"
        "- pooling_saturation (10–500): sqrt-shrinkage saturation for role pooling.\n"
        "- calibrate (bool): apply isotonic calibration on the holdout.\n\n"
        "Each turn, propose exactly ONE new config to try next, with a one-line "
        "rationale grounded in the trial log. Do not repeat a config already tried. "
        "Set stop=true once the log-loss has plateaued and further search is unlikely "
        "to beat the best result."
    )

    def __init__(
        self,
        client: Any,
        *,
        organization_id: int | None,
        role_id: int | None = None,
        model: str = "claude-sonnet-4-5",
        max_tokens: int = 1024,
    ) -> None:
        self._client = client
        self._organization_id = organization_id
        self._role_id = role_id
        self._model = model
        self._max_tokens = max_tokens

    def propose(
        self,
        *,
        history: Sequence[Trial],
        best_config: HyperConfig,
        best_log_loss: float | None,
    ) -> Proposal | None:
        # Imported lazily so the module stays importable without the LLM stack
        # (and so unit tests can exercise the loop with a scripted proposer).
        from pydantic import BaseModel, Field

        from ..llm import MeteringContext, generate_structured
        from ..services.pricing_service import Feature

        class ProposedConfig(BaseModel):
            l2: float = Field(ge=0.0, le=1.0)
            learning_rate: float = Field(ge=1e-3, le=0.5)
            max_iter: int = Field(ge=50, le=1000)
            pooling_saturation: int = Field(ge=10, le=500)
            calibrate: bool
            rationale: str = ""
            stop: bool = False

        best_ll = f"{best_log_loss:.5f}" if best_log_loss is not None else "none yet"
        user = (
            f"Best log-loss so far: {best_ll} at config {best_config}.\n\n"
            f"Trial log:\n{_history_table(history)}\n\n"
            "Propose the next config to try (or stop=true if converged)."
        )
        result = generate_structured(
            self._client,
            model=self._model,
            messages=[{"role": "user", "content": user}],
            output_model=ProposedConfig,
            metering=MeteringContext(
                feature=Feature.AGENT_AUTONOMOUS,
                organization_id=self._organization_id,
                role_id=self._role_id,
            ),
            max_tokens=self._max_tokens,
            system=self.SYSTEM,
            temperature=0.0,
            use_tool_use=True,
            tool_name="propose_config",
        )
        if not result.ok or result.value is None:
            logger.warning("LLMProposer: structured call failed (%s); stopping", result.error_reason)
            return Proposal(config=best_config, stop=True, rationale="proposer_error")
        v = result.value
        return Proposal(
            config=HyperConfig(
                l2=v.l2,
                learning_rate=v.learning_rate,
                max_iter=v.max_iter,
                pooling_saturation=v.pooling_saturation,
                calibrate=v.calibrate,
            ),
            rationale=v.rationale,
            stop=v.stop,
        )


def _agentic_search(
    *,
    train_examples: Sequence[TrainingExample],
    gold_set: Sequence[TrainingExample],
    audit_examples: Sequence[AuditExample],
    role_id: int | None,
    thresholds: BiasThresholds | None,
    max_iters: int,
    proposer: Proposer,
) -> SearchResult:
    """Proposer-driven variant of :func:`search`. Same verify/keep/gate/log harness."""
    thr = thresholds or load_thresholds()
    trials: list[Trial] = []

    def run(cfg: HyperConfig, note: str) -> Trial:
        log_loss, bias_ok, n_viol = _evaluate(
            cfg,
            train_examples=train_examples,
            gold_set=gold_set,
            audit_examples=audit_examples,
            thresholds=thr,
            role_id=role_id,
        )
        t = Trial(
            config=cfg, log_loss=log_loss, bias_passed=bias_ok,
            violations=n_viol, kept=False, note=note,
        )
        trials.append(t)
        return t

    base = run(BASELINE, "baseline")
    best_cfg = BASELINE
    best_loss: float | None = None
    if base.bias_passed and base.log_loss is not None:
        base.kept = True
        best_loss = base.log_loss

    for _ in range(max_iters):
        proposal = proposer.propose(
            history=trials, best_config=best_cfg, best_log_loss=best_loss
        )
        if proposal is None or proposal.stop:
            break
        cand = _clamp_config(proposal.config)
        if any(t.config == cand for t in trials):
            # Proposer repeated a tried config — treat as a convergence signal.
            break
        t = run(cand, (proposal.rationale or "agentic")[:160])
        if not t.bias_passed:
            t.note += " [discarded: bias]"
            continue
        if t.log_loss is None:
            t.note += " [discarded: unscorable]"
            continue
        if best_loss is None or t.log_loss < best_loss - MIN_IMPROVEMENT:
            t.kept = True
            best_cfg = cand
            best_loss = t.log_loss
        else:
            t.note += " [discarded: no improvement]"

    accepted = best_loss is not None
    best_model: FittedModel | None = None
    best_metrics: dict | None = None
    if accepted:
        best_model, best_metrics = fit_model(
            train_examples,
            role_id=role_id,
            gold_set=gold_set,
            l2=best_cfg.l2,
            learning_rate=best_cfg.learning_rate,
            max_iter=best_cfg.max_iter,
            pooling_saturation=best_cfg.pooling_saturation,
            calibrate=best_cfg.calibrate,
        )
        logger.info(
            "agentic autoresearch: %d trials, best log_loss=%.5f at %s",
            len(trials), best_loss, best_cfg,
        )
    return SearchResult(
        accepted=accepted,
        best_config=best_cfg,
        best_model=best_model,
        best_log_loss=best_loss,
        trials=trials,
        best_metrics=best_metrics,
    )


def make_llm_proposer(org: Any, *, role_id: int | None = None, **kwargs: Any) -> LLMProposer:
    """Convenience: build an :class:`LLMProposer` wired to the org's metered client."""
    from ..services.claude_client_resolver import get_client_for_org

    return LLMProposer(
        get_client_for_org(org),
        organization_id=int(org.id) if getattr(org, "id", None) is not None else None,
        role_id=role_id,
        **kwargs,
    )


__all__ = [
    "BASELINE",
    "HyperConfig",
    "LLMProposer",
    "MIN_IMPROVEMENT",
    "Proposal",
    "Proposer",
    "SEARCH_GRID",
    "SearchResult",
    "Trial",
    "make_llm_proposer",
    "search",
    "summarize",
]
