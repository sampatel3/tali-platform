"""TAA-28: the bias-audit holdout loader + run_for_all_orgs threading.

Before this seam, ``run_for_all_orgs`` called ``run_for_org`` with NO
``audit_examples``, so even with auto-apply enabled the EEOC bias audit
ran on ``[]`` and the gate fail-closed on a cold-start vacuum every night
(AUDIT_03 P3-TALI-01). These tests pin the two halves of the fix:

1. ``load_audit_examples`` resolves a compliance-curated JSON holdout from
   ``config/bias_audit_examples/<slug>.json`` (or ``default.json``), and
   returns ``[]`` when nothing is configured (safe cold-start default).
2. ``run_for_all_orgs`` resolves that holdout per-org and threads it into
   ``run_for_org`` → ``evaluate_auto_apply`` → ``audit`` so the bias audit
   runs on REAL data when a holdout is present.
"""

from __future__ import annotations

import json

from app.decision_policy import nightly_retune
from app.decision_policy.audit_examples import load_audit_examples
from app.decision_policy.bias_audit import AuditExample

from .conftest import bootstrap, make_org, make_role
from .test_nightly_retune import (
    _StubRetuner,
    _add_recent_run,
    _balanced_audit_examples,
    _make_fitted_candidate,
    _seed_gold_set,
)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _write_holdout(dir_path, name: str, rows: list[dict]) -> None:
    (dir_path / f"{name}.json").write_text(json.dumps(rows))


def test_loader_returns_empty_when_no_holdout(tmp_path, db):
    org = make_org(db, name="No Holdout Org")
    out = load_audit_examples(org, base_dir=tmp_path)
    assert list(out) == []


def test_loader_reads_org_specific_holdout(tmp_path, db):
    org = make_org(db, name="Has Holdout Org")
    _write_holdout(
        tmp_path,
        org.slug,
        [
            {"features": {"x": 0.9}, "label": 1, "segments": {"gender": "F"}},
            {"features": {"x": 0.1}, "label": 0, "segments": {"gender": "M"}},
        ],
    )
    out = list(load_audit_examples(org, base_dir=tmp_path))
    assert len(out) == 2
    assert all(isinstance(e, AuditExample) for e in out)
    assert out[0].features == {"x": 0.9}
    assert out[0].label == 1.0
    assert out[0].segments == {"gender": "F"}


def test_loader_falls_back_to_default(tmp_path, db):
    org = make_org(db, name="Default Fallback Org")
    _write_holdout(
        tmp_path,
        "default",
        [{"features": {"x": 0.5}, "label": 1, "segments": {"race": "white"}}],
    )
    out = list(load_audit_examples(org, base_dir=tmp_path))
    assert len(out) == 1
    assert out[0].segments == {"race": "white"}


def test_loader_org_specific_wins_over_default(tmp_path, db):
    org = make_org(db, name="Both Org")
    _write_holdout(tmp_path, "default", [
        {"features": {"x": 0.5}, "label": 1, "segments": {"gender": "F"}}
    ])
    _write_holdout(tmp_path, org.slug, [
        {"features": {"x": 0.2}, "label": 0, "segments": {"gender": "M"}},
        {"features": {"x": 0.8}, "label": 1, "segments": {"gender": "F"}},
    ])
    out = list(load_audit_examples(org, base_dir=tmp_path))
    assert len(out) == 2  # the org-specific file, not the 1-row default


def test_loader_skips_malformed_rows(tmp_path, db):
    org = make_org(db, name="Malformed Org")
    _write_holdout(
        tmp_path,
        org.slug,
        [
            {"features": {"x": 0.9}, "label": 1, "segments": {"gender": "F"}},  # ok
            {"label": 1, "segments": {"gender": "M"}},  # missing features
            {"features": {"x": 0.3}, "segments": {"gender": "M"}},  # missing label
            "not even a dict",
        ],
    )
    out = list(load_audit_examples(org, base_dir=tmp_path))
    assert len(out) == 1  # only the well-formed row survives


# ---------------------------------------------------------------------------
# run_for_all_orgs threads the loader's examples into the gate
# ---------------------------------------------------------------------------


def test_run_for_all_orgs_threads_loaded_examples_into_gate(db, monkeypatch):
    """End-to-end: with auto-apply on and a balanced holdout supplied by
    the loader, run_for_all_orgs activates the proposal — proving the
    examples reached evaluate_auto_apply rather than the old ``[]`` that
    fail-closed every night."""
    org = make_org(db, name="Wired Org")
    org.workspace_settings = {"decision_policy_auto_apply": True}
    role = make_role(db, org=org)
    bootstrap(db, org)
    _add_recent_run(db, org=org, role=role)
    _make_fitted_candidate(db, org=org)
    _seed_gold_set(db, org=org)
    db.flush()

    captured: dict[str, object] = {}

    def _fake_loader(o, **kwargs):
        captured["org_id"] = int(o.id)
        return _balanced_audit_examples()

    # Patch the loader the nightly module imported, and force the stub
    # retuner so a proposal with a real shift exists to activate.
    monkeypatch.setattr(nightly_retune, "load_audit_examples", _fake_loader)
    monkeypatch.setattr(
        nightly_retune, "HeuristicRetuner", lambda **kw: _StubRetuner()
    )

    results = nightly_retune.run_for_all_orgs(db)

    assert captured.get("org_id") == int(org.id), "loader was not consulted per-org"
    wired = [r for r in results if r.organization_id == int(org.id)]
    assert len(wired) == 1
    # Balanced holdout + gold set + fitted candidate → gate passes → activate.
    assert wired[0].activated is True, (
        "balanced loaded holdout should let the gate pass; instead: "
        f"{wired[0].gate_blocked_reason}"
    )


def test_run_for_all_orgs_empty_holdout_stays_fail_closed(db, monkeypatch):
    """The safe default: when the loader returns [] (no holdout configured)
    and auto-apply is on, the gate stays fail-closed (cold start) — the
    proposal is written inactive, never activated on a vacuum."""
    org = make_org(db, name="Unconfigured Org")
    org.workspace_settings = {"decision_policy_auto_apply": True}
    role = make_role(db, org=org)
    bootstrap(db, org)
    _add_recent_run(db, org=org, role=role)
    _make_fitted_candidate(db, org=org)
    _seed_gold_set(db, org=org)
    db.flush()

    monkeypatch.setattr(nightly_retune, "load_audit_examples", lambda o, **kw: [])
    monkeypatch.setattr(
        nightly_retune, "HeuristicRetuner", lambda **kw: _StubRetuner()
    )

    results = nightly_retune.run_for_all_orgs(db)
    wired = [r for r in results if r.organization_id == int(org.id)]
    assert len(wired) == 1
    assert wired[0].activated is False
    assert wired[0].gate_blocked_reason is not None
