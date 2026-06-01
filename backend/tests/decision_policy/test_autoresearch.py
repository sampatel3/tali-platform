"""Autoresearch loop — the bias-gated hyperparameter search (offline, no DB).

These lock the loop's contract:
  - the returned winner is always bias-clean (the constraint is hard);
  - the winner is never worse than the production baseline;
  - bias-failing candidates are discarded, never kept;
  - the search is deterministic.
"""

from __future__ import annotations

from app.decision_policy import autoresearch
from app.decision_policy.bias_audit import AuditExample, BiasThresholds
from app.decision_policy.fitted_policy import TrainingExample


THRESHOLDS = BiasThresholds(protected_attributes=("gender",))


def _train_and_gold() -> tuple[list[TrainingExample], list[TrainingExample]]:
    """A learnable-but-noisy 1-D problem so hyperparameters actually move the
    holdout log-loss (clean separation would make every config tie)."""
    train, gold = [], []
    for i in range(120):
        x = (i % 10) / 10.0
        # Mostly separable at 0.5, with a few deliberate label flips so L2 /
        # calibration choices change the holdout loss.
        label = 1.0 if x >= 0.5 else 0.0
        if i % 17 == 0:
            label = 1.0 - label
        train.append(TrainingExample(features={"x": x}, label=label))
    for i in range(40):
        x = (i % 10) / 10.0
        gold.append(TrainingExample(features={"x": x}, label=1.0 if x >= 0.5 else 0.0))
    return train, gold


def _balanced_audit() -> list[AuditExample]:
    """F and M segments with identical distributions → no parity gap can fire."""
    examples = []
    pattern = [(0.1, 0.0), (0.4, 0.0), (0.6, 1.0), (0.9, 1.0)] * 6  # 24 per segment
    for x, label in pattern:
        examples.append(AuditExample(features={"x": x}, label=label, segments={"gender": "F"}))
        examples.append(AuditExample(features={"x": x}, label=label, segments={"gender": "M"}))
    return examples


def _biased_audit() -> list[AuditExample]:
    """F pinned far below threshold, M far above → disparate impact for any
    reasonable fit. Exercises the bias-discard path."""
    return [
        AuditExample(features={"x": -10.0}, label=0.0, segments={"gender": "F"})
        for _ in range(20)
    ] + [
        AuditExample(features={"x": 10.0}, label=1.0, segments={"gender": "M"})
        for _ in range(20)
    ]


def test_search_accepts_and_winner_is_bias_clean():
    train, gold = _train_and_gold()
    result = autoresearch.search(
        train_examples=train,
        gold_set=gold,
        audit_examples=_balanced_audit(),
        thresholds=THRESHOLDS,
    )
    assert result.accepted is True
    assert result.best_model is not None
    assert result.best_log_loss is not None
    # Every trial the loop KEPT must have cleared the bias gate.
    assert all(t.bias_passed for t in result.trials if t.kept)


def test_winner_never_worse_than_baseline():
    train, gold = _train_and_gold()
    result = autoresearch.search(
        train_examples=train,
        gold_set=gold,
        audit_examples=_balanced_audit(),
        thresholds=THRESHOLDS,
    )
    baseline = result.baseline_log_loss
    assert baseline is not None
    # The baseline is the anchor and only strict improvements are kept, so the
    # winner can only match or beat it.
    assert result.best_log_loss <= baseline + autoresearch.MIN_IMPROVEMENT


def test_search_explores_the_grid():
    train, gold = _train_and_gold()
    result = autoresearch.search(
        train_examples=train,
        gold_set=gold,
        audit_examples=_balanced_audit(),
        thresholds=THRESHOLDS,
    )
    # Baseline + at least a few coordinate-descent probes.
    assert len(result.trials) > 3
    assert any(t.config == autoresearch.BASELINE for t in result.trials)


def test_bias_failing_candidates_are_discarded():
    train, gold = _train_and_gold()
    result = autoresearch.search(
        train_examples=train,
        gold_set=gold,
        audit_examples=_biased_audit(),
        thresholds=THRESHOLDS,
    )
    # Some config tripped the audit on this skewed holdout...
    assert any(not t.bias_passed for t in result.trials)
    # ...and nothing bias-failing was ever kept.
    assert all(t.bias_passed for t in result.trials if t.kept)
    # If the loop accepted a winner at all, it is bias-clean by construction.
    if result.accepted:
        assert result.best_model is not None


def test_search_is_deterministic():
    train, gold = _train_and_gold()
    audit = _balanced_audit()
    a = autoresearch.search(
        train_examples=train, gold_set=gold, audit_examples=audit, thresholds=THRESHOLDS
    )
    b = autoresearch.search(
        train_examples=train, gold_set=gold, audit_examples=audit, thresholds=THRESHOLDS
    )
    assert a.best_config == b.best_config
    assert [t.config for t in a.trials] == [t.config for t in b.trials]
    assert a.best_log_loss == b.best_log_loss


# ---------------------------------------------------------------------------
# Agentic path — proposer-driven loop (no network; scripted / mocked proposers).
# ---------------------------------------------------------------------------


class _ScriptedProposer:
    """Replays a fixed list of configs, then stops — stands in for the LLM."""

    def __init__(self, configs):
        self._configs = list(configs)
        self._i = 0

    def propose(self, *, history, best_config, best_log_loss):
        if self._i >= len(self._configs):
            return autoresearch.Proposal(config=best_config, stop=True)
        cfg = self._configs[self._i]
        self._i += 1
        return autoresearch.Proposal(config=cfg, rationale=f"scripted #{self._i}")


def test_agentic_loop_runs_and_winner_is_bias_clean():
    train, gold = _train_and_gold()
    proposer = _ScriptedProposer([
        autoresearch.HyperConfig(calibrate=False),               # worse — discarded
        autoresearch.HyperConfig(l2=0.0, max_iter=400),          # candidate
        autoresearch.HyperConfig(learning_rate=0.1, max_iter=600),
    ])
    result = autoresearch.search(
        train_examples=train, gold_set=gold, audit_examples=_balanced_audit(),
        thresholds=THRESHOLDS, proposer=proposer,
    )
    assert result.accepted is True
    # Baseline + the three scripted proposals all evaluated.
    assert len(result.trials) == 4
    # Guardrail unchanged: nothing bias-failing is ever kept; winner is clean.
    assert all(t.bias_passed for t in result.trials if t.kept)
    assert result.best_log_loss <= result.baseline_log_loss + autoresearch.MIN_IMPROVEMENT


def test_agentic_loop_honours_stop():
    train, gold = _train_and_gold()

    class _Stop:
        def propose(self, *, history, best_config, best_log_loss):
            return autoresearch.Proposal(config=best_config, stop=True)

    result = autoresearch.search(
        train_examples=train, gold_set=gold, audit_examples=_balanced_audit(),
        thresholds=THRESHOLDS, proposer=_Stop(),
    )
    assert len(result.trials) == 1  # baseline only
    assert result.trials[0].config == autoresearch.BASELINE


def test_agentic_clamps_out_of_bounds_proposal():
    train, gold = _train_and_gold()
    # Wild values the loop must clamp before fitting.
    proposer = _ScriptedProposer([
        autoresearch.HyperConfig(l2=-5.0, learning_rate=99.0, max_iter=10**9, pooling_saturation=0),
    ])
    result = autoresearch.search(
        train_examples=train, gold_set=gold, audit_examples=_balanced_audit(),
        thresholds=THRESHOLDS, proposer=proposer,
    )
    proposed = result.trials[1].config
    assert 0.0 <= proposed.l2 <= 1.0
    assert 1e-3 <= proposed.learning_rate <= 0.5
    assert 50 <= proposed.max_iter <= 1000
    assert 10 <= proposed.pooling_saturation <= 500


def test_llm_proposer_maps_structured_output(monkeypatch):
    """LLMProposer drives the loop using a mocked metered structured call."""
    import app.llm as llm_mod
    from app.llm import StructuredResult
    from types import SimpleNamespace

    calls = {"n": 0}

    def fake_generate_structured(client, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            value = SimpleNamespace(
                l2=0.0, learning_rate=0.1, max_iter=400, pooling_saturation=100,
                calibrate=True, rationale="try less reg + more iters", stop=False,
            )
        else:
            value = SimpleNamespace(
                l2=0.01, learning_rate=0.05, max_iter=200, pooling_saturation=100,
                calibrate=True, rationale="converged", stop=True,
            )
        return StructuredResult(value=value, ok=True)

    monkeypatch.setattr(llm_mod, "generate_structured", fake_generate_structured)

    train, gold = _train_and_gold()
    proposer = autoresearch.LLMProposer(client=object(), organization_id=1)
    result = autoresearch.search(
        train_examples=train, gold_set=gold, audit_examples=_balanced_audit(),
        thresholds=THRESHOLDS, proposer=proposer,
    )
    assert calls["n"] >= 1  # the agent was consulted
    assert result.accepted is True
    assert all(t.bias_passed for t in result.trials if t.kept)


# ---------------------------------------------------------------------------
# Wiring into the nightly fitter (nightly_policy_fit._fit_candidate_model).
# ---------------------------------------------------------------------------


def _org(db, settings):
    from app.models.organization import Organization

    org = Organization(name="AR Org", slug=f"ar-{id(settings)}-{id(db)}", workspace_settings=settings)
    db.add(org)
    db.flush()
    return org


def test_fitter_flag_off_is_plain_one_shot_fit(db):
    from app.decision_policy import nightly_policy_fit as npf

    org = _org(db, {})  # no autoresearch flag
    train, gold = _train_and_gold()
    model, metrics = npf._fit_candidate_model(
        db, organization_id=int(org.id), role_id=None, train=train, gold=gold
    )
    assert model is not None
    assert "autoresearch" not in metrics  # untouched one-shot path


def test_fitter_grid_mode_records_trace_and_improves_or_holds(db, monkeypatch):
    from app.decision_policy import nightly_policy_fit as npf

    monkeypatch.setattr(npf, "load_audit_examples", lambda org: _balanced_audit())
    org = _org(db, {"decision_policy_autoresearch": "grid"})
    train, gold = _train_and_gold()
    model, metrics = npf._fit_candidate_model(
        db, organization_id=int(org.id), role_id=None, train=train, gold=gold
    )
    assert model is not None
    trace = metrics["autoresearch"]
    assert trace["mode"] == "grid"
    assert trace["accepted"] is True
    assert trace["best_log_loss"] <= trace["baseline_log_loss"] + autoresearch.MIN_IMPROVEMENT


def test_fitter_cold_start_holdout_falls_back_to_baseline(db):
    from app.decision_policy import nightly_policy_fit as npf

    # No monkeypatch → load_audit_examples returns [] for an org with no holdout
    # configured, so the bias gate fails closed and the search accepts nothing.
    org = _org(db, {"decision_policy_autoresearch": "grid"})
    train, gold = _train_and_gold()
    model, metrics = npf._fit_candidate_model(
        db, organization_id=int(org.id), role_id=None, train=train, gold=gold
    )
    assert model is not None  # still get a usable baseline candidate
    assert metrics["autoresearch"]["accepted"] is False
