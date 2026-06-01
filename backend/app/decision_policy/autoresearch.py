"""Autoresearch — tali's brand adapter over the mainspring search loop.

The loop mechanics — propose → fit → score → audit → keep/discard, with the
trial log as memory — live in the substrate (``mainspring.core.autoresearch``,
vendored at ``vendor/mainspring_autoresearch``). This module supplies the
tali-specific pieces and re-exports a brand-typed surface:

  - **Editable surface** (Karpathy's ``train.py``): the fit hyperparameters in
    :class:`HyperConfig` — L2, learning rate, iterations, pooling saturation,
    and whether isotonic calibration is applied. The production fit
    (:func:`fitted_policy.fit_model`) hard-codes these; here we search them.
  - **Metric** (Karpathy's ``val_bpb``): gold-set holdout log-loss — *lower is
    better*. This is exactly the number the Phase-5 promotion gate already judges.
  - **Constraint** (the fairness-specific part): the bias audit must *pass* on
    the protected-attribute holdout. The EEOC 4/5ths verdict and its thresholds
    are compliance-signed constants — a hard guardrail we never tune, not an
    objective. A candidate that lowers log-loss but trips the bias audit is
    *discarded*, never kept (the substrate loop enforces this).

tali injects ``fit_fn`` / ``score_fn`` / ``audit_fn`` closures and a proposer;
the substrate owns the algorithm so cadence (or any brand) inherits the same
loop. This module is offline and side-effect-free — it fits in-memory candidate
models and returns the winner; persisting/promoting stays with the promotion gate.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, Sequence

from vendor.mainspring_autoresearch.seam import GridProposer as _CoreGridProposer
from vendor.mainspring_autoresearch.seam import Proposal as _CoreProposal
from vendor.mainspring_autoresearch.seam import search as _core_search

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


def _make_callables(
    *,
    train_examples: Sequence[TrainingExample],
    gold_set: Sequence[TrainingExample],
    audit_examples: Sequence[AuditExample],
    thresholds: BiasThresholds,
    role_id: int | None,
):
    """Build the brand-specific (fit, score, audit) callables the loop injects.

    The artifact is the ``(model, metrics)`` pair, so the kept winner carries its
    metrics dict back with no redundant refit. ``score_fn`` returns the gold-set
    holdout log-loss (lower better); ``audit_fn`` returns ``(passed, n_violations)``
    from the EEOC bias verdict on the protected-attribute holdout.
    """

    def fit_fn(cfg: dict):
        hp = HyperConfig(**cfg)
        return fit_model(
            train_examples,
            role_id=role_id,
            gold_set=gold_set,
            l2=hp.l2,
            learning_rate=hp.learning_rate,
            max_iter=hp.max_iter,
            pooling_saturation=hp.pooling_saturation,
            calibrate=hp.calibrate,
        )

    def score_fn(artifact) -> float | None:
        _model, metrics = artifact
        return (metrics or {}).get("holdout_log_loss")

    def audit_fn(artifact) -> tuple[bool, int]:
        model, _metrics = artifact
        _, violations = audit(model=model, examples=audit_examples, thresholds=thresholds)
        return (len(violations) == 0, len(violations))

    return fit_fn, score_fn, audit_fn


def _to_trial(ct) -> Trial:
    """Map a substrate ``Trial`` (opaque dict config) to tali's HyperConfig Trial."""
    return Trial(
        config=HyperConfig(**ct.config),
        log_loss=ct.score,
        bias_passed=ct.constraint_passed,
        violations=ct.violations,
        kept=ct.kept,
        note=ct.note,
    )


def _to_result(cr) -> SearchResult:
    model, metrics = cr.best_artifact if cr.best_artifact is not None else (None, None)
    return SearchResult(
        accepted=cr.accepted,
        best_config=HyperConfig(**cr.best_config),
        best_model=model,
        best_log_loss=cr.best_score,
        trials=[_to_trial(t) for t in cr.trials],
        best_metrics=metrics,
    )


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
    """Run the bias-gated hyperparameter search via the mainspring loop.

    Default (``proposer=None``): the substrate's :class:`GridProposer` walks
    :data:`SEARCH_GRID` by coordinate descent. Agentic (``proposer`` supplied,
    e.g. :class:`LLMProposer`): the brand proposer reads the trial history and
    reasons about the next config. Either way the substrate loop fits, scores,
    and bias-gates every proposal identically, anchored on :data:`BASELINE`: the
    winner is always bias-clean, and ``accepted`` is False with ``best_model=None``
    if nothing clears the gate.
    """
    thr = thresholds or load_thresholds()
    fit_fn, score_fn, audit_fn = _make_callables(
        train_examples=train_examples,
        gold_set=gold_set,
        audit_examples=audit_examples,
        thresholds=thr,
        role_id=role_id,
    )
    core_proposer = (
        _CoreGridProposer(grid=SEARCH_GRID)
        if proposer is None
        else _BrandProposerAdapter(proposer)
    )
    cr = _core_search(
        baseline_config=asdict(BASELINE),
        fit_fn=fit_fn,
        score_fn=score_fn,
        audit_fn=audit_fn,
        proposer=core_proposer,
        max_iters=max_iters,
        min_improvement=MIN_IMPROVEMENT,
        minimize=True,
    )
    if not cr.accepted:
        logger.warning(
            "autoresearch: no bias-clean config found over %d trials; "
            "returning baseline unaccepted",
            len(cr.trials),
        )
    return _to_result(cr)


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


class _BrandProposerAdapter:
    """Bridges a tali :class:`Proposer` to the substrate's proposer protocol.

    The substrate loop speaks opaque ``dict`` configs and ``best_score``; tali's
    proposers (the :class:`LLMProposer`, test scripts) speak :class:`HyperConfig`
    and ``best_log_loss``. This converts at the boundary and clamps every proposal
    into safe bounds before it reaches a fit — so a hallucinated LLM value can
    never blow up training time or destabilise the model.
    """

    def __init__(self, inner: "Proposer") -> None:
        self._inner = inner

    def propose(self, *, history, best_config, best_score):
        proposal = self._inner.propose(
            history=[_to_trial(t) for t in history],
            best_config=HyperConfig(**best_config),
            best_log_loss=best_score,
        )
        if proposal is None:
            return None
        if proposal.stop:
            return _CoreProposal(config=best_config, stop=True, rationale=proposal.rationale)
        cand = _clamp_config(proposal.config)
        return _CoreProposal(config=asdict(cand), rationale=proposal.rationale)


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
