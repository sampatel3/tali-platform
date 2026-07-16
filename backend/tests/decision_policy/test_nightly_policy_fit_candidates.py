from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.decision_policy import nightly_policy_fit
from app.decision_policy.fitted_policy import FittedModel, TrainingExample
from app.models.organization import Organization
from app.models.policy_version import POLICY_VERSION_STATUSES, PolicyVersion


def _training_examples(*, changed: bool = False) -> list[TrainingExample]:
    rows = [
        TrainingExample(
            features={
                "role_fit_score": float(index),
                "pre_screen_score": float(index + 5),
            },
            label=1.0 if index >= 25 else 0.0,
            weight=1.0,
        )
        for index in range(60)
    ]
    if changed:
        rows[0] = TrainingExample(
            features={"role_fit_score": 99.0, "pre_screen_score": 99.0},
            label=1.0,
            weight=1.0,
        )
    return rows


def test_policy_version_status_contract_includes_superseded():
    assert "superseded" in POLICY_VERSION_STATUSES


def test_equivalent_candidate_reuses_before_model_or_agentic_search(db, monkeypatch):
    org = Organization(
        name="Fit Reuse Org",
        slug=f"fit-reuse-{id(db)}",
        workspace_settings={"decision_policy_autoresearch": "agentic"},
    )
    db.add(org)
    db.flush()
    examples = _training_examples()
    calls = []
    events = []

    monkeypatch.setattr(
        nightly_policy_fit,
        "_collect_training_data",
        lambda *args, **kwargs: list(examples),
    )
    real_lock = nightly_policy_fit._lock_organization_for_fit

    def tracked_lock(*args, **kwargs):
        events.append("lock")
        return real_lock(*args, **kwargs)

    monkeypatch.setattr(
        nightly_policy_fit,
        "_lock_organization_for_fit",
        tracked_lock,
    )

    def fake_fit(*args, **kwargs):
        events.append("fit")
        calls.append(kwargs)
        return FittedModel(coefs={"role_fit_score": 0.1}), {"loss": 0.2}

    monkeypatch.setattr(nightly_policy_fit, "_fit_candidate_model", fake_fit)
    since = datetime.now(timezone.utc) - timedelta(days=90)

    first = nightly_policy_fit.fit_for_org(
        db, organization_id=int(org.id), since=since, role_id=None
    )
    assert first is not None
    stale_duplicate = PolicyVersion(
        organization_id=org.id,
        role_id=None,
        model_kind="logistic_pooled",
        model_json={"coefs": {"stale": 1.0}},
        status="candidate",
    )
    db.add(stale_duplicate)
    db.flush()
    second = nightly_policy_fit.fit_for_org(
        db, organization_id=int(org.id), since=since, role_id=None
    )

    assert second is not None
    assert second.id == first.id
    assert stale_duplicate.status == "superseded"
    assert len(calls) == 1
    assert events == ["lock", "fit", "lock"]
    assert first.metrics_json["fit_contract_version"] == (
        nightly_policy_fit.FIT_CONTRACT_VERSION
    )
    assert len(first.metrics_json["training_fingerprint"]) == 64
    assert first.metrics_json["activation_status"] == "dormant_fail_closed"
    assert (
        db.query(PolicyVersion)
        .filter(
            PolicyVersion.organization_id == org.id,
            PolicyVersion.role_id.is_(None),
            PolicyVersion.status == "candidate",
        )
        .count()
        == 1
    )


def test_changed_inputs_supersede_pending_candidate_but_preserve_shadow(db, monkeypatch):
    org = Organization(name="Fit Supersede Org", slug=f"fit-super-{id(db)}")
    db.add(org)
    db.flush()
    current_examples = {"rows": _training_examples()}

    monkeypatch.setattr(
        nightly_policy_fit,
        "_collect_training_data",
        lambda *args, **kwargs: list(current_examples["rows"]),
    )
    monkeypatch.setattr(
        nightly_policy_fit,
        "_fit_candidate_model",
        lambda *args, **kwargs: (
            FittedModel(coefs={"role_fit_score": 0.1}),
            {"loss": 0.2},
        ),
    )
    since = datetime.now(timezone.utc) - timedelta(days=90)
    first = nightly_policy_fit.fit_for_org(
        db, organization_id=int(org.id), since=since, role_id=None
    )
    assert first is not None

    manual_shadow = PolicyVersion(
        organization_id=org.id,
        role_id=None,
        model_kind="logistic_pooled",
        model_json={"coefs": {"manual": 1.0}},
        status="shadow",
    )
    db.add(manual_shadow)
    db.flush()
    current_examples["rows"] = _training_examples(changed=True)

    replacement = nightly_policy_fit.fit_for_org(
        db, organization_id=int(org.id), since=since, role_id=None
    )

    assert replacement is not None
    assert replacement.id != first.id
    assert replacement.status == "candidate"
    assert first.status == "superseded"
    assert first.archived_at is not None
    assert manual_shadow.status == "shadow"
    assert (
        db.query(PolicyVersion)
        .filter(
            PolicyVersion.organization_id == org.id,
            PolicyVersion.role_id.is_(None),
            PolicyVersion.status == "candidate",
        )
        .count()
        == 1
    )


def test_feature_extraction_prefers_server_owned_flattened_scores():
    decision = SimpleNamespace(
        evidence={
            "pre_screen_score": 0.0,
            "role_fit_score": 87.0,
            "taali_score": 78.0,
            "scores": {
                "pre_screen": {"score": 99.0},
                "cv_scoring": {"score": 12.0},
                "assessment_scoring": {"score": 11.0},
            },
        },
        confidence=0.0,
    )

    features = nightly_policy_fit._features_for_decision(decision)

    assert features["pre_screen_score"] == 0.0
    assert features["role_fit_score"] == 87.0
    assert features["taali_score"] == 78.0
    assert features["decision_confidence"] == 0.0


def test_feature_extraction_normalizes_legacy_nested_scores():
    decision = SimpleNamespace(
        evidence={
            "scores": {
                "pre_screen": {
                    "output": {"score": 42.0, "uncertainty": 0.2},
                    "confidence": 0.8,
                },
                "cv_scoring": {"score": 0.0, "uncertainty": 0.0},
                "assessment_scoring": {
                    "taali_score": 83.0,
                    "assessment_score": 72.0,
                },
                "graph_priors": {"p_advance": 0.7, "p_hired": 0.4},
                "custom_agent": {"confidence": 0.3, "uncertainty": False},
            }
        },
        confidence=0.6,
    )

    features = nightly_policy_fit._features_for_decision(decision)

    assert features == {
        "pre_screen_score": 42.0,
        "pre_screen_uncertainty": 0.2,
        "role_fit_score": 0.0,
        "cv_scoring_uncertainty": 0.0,
        "taali_score": 83.0,
        "assessment_score": 72.0,
        "graph_prior_p_advance": 0.7,
        "graph_prior_p_hired": 0.4,
        "custom_agent_score": 0.3,
        "decision_confidence": 0.6,
    }
