"""Migration 086 patches existing policies with the prescreen-queue rule.

The migration also stamps ``reject_reason`` onto existing reject rules.
Pre-pilot, no recruiter has fine-tuned a policy, so this is safe. The
test exercises the migration's ``_patch_policy`` helper directly so we
don't need Alembic against SQLite.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _load_migration():
    """Import the migration module by file path — it's not in a package."""
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "086_add_auto_reject_prescreen_to_roles.py"
    )
    spec = importlib.util.spec_from_file_location("migration_086", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["migration_086"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _legacy_policy_json() -> dict:
    return {
        "schema_version": "v1",
        "decision_points": {
            "reject": {
                "thresholds": {"role_fit_max": 30.0},
                "weights": {"role_fit_score": 1.0},
                "rules": [
                    {
                        "if": "pre_screen_auto_reject_eligible",
                        "then": "auto_reject",
                        "priority": 70,
                        "reason_template": "auto reject",
                    },
                    {
                        "if": "role_fit_score <= role_fit_max AND no_pending_assessment",
                        "then": "queue_reject_decision",
                        "priority": 50,
                        "reason_template": "queue reject",
                    },
                ],
                "confidence_floor": 0.6,
            },
        },
    }


def test_patch_inserts_new_prescreen_queue_rule():
    mod = _load_migration()
    legacy = _legacy_policy_json()
    patched, changed = mod._patch_policy(legacy)
    assert changed
    rules = patched["decision_points"]["reject"]["rules"]
    new_rule = next(
        r for r in rules if r.get("if") == mod._PRESCREEN_RULE["if"]
    )
    assert new_rule["then"] == "queue_skip_assessment_reject_decision"
    assert new_rule["reject_reason"] == "pre_screen_below_threshold"
    assert new_rule["priority"] == 60


def test_patch_stamps_reject_reason_on_existing_rules():
    mod = _load_migration()
    legacy = _legacy_policy_json()
    patched, _ = mod._patch_policy(legacy)
    rules = patched["decision_points"]["reject"]["rules"]
    auto_reject_rule = next(
        r for r in rules if r.get("if") == "pre_screen_auto_reject_eligible"
    )
    role_fit_rule = next(
        r for r in rules if "role_fit_score" in r.get("if", "")
    )
    assert auto_reject_rule["reject_reason"] == "pre_screen_below_threshold"
    assert role_fit_rule["reject_reason"] == "role_fit_low"


def test_patch_is_idempotent():
    mod = _load_migration()
    patched_once, changed_first = mod._patch_policy(_legacy_policy_json())
    assert changed_first
    patched_twice, changed_again = mod._patch_policy(patched_once)
    assert not changed_again
    assert patched_once == patched_twice


def test_patch_preserves_existing_reject_reason_on_rules():
    mod = _load_migration()
    policy = _legacy_policy_json()
    # Pre-set a reject_reason; migration should not overwrite.
    policy["decision_points"]["reject"]["rules"][0]["reject_reason"] = "custom"
    patched, _ = mod._patch_policy(policy)
    auto_reject_rule = next(
        r
        for r in patched["decision_points"]["reject"]["rules"]
        if r.get("if") == "pre_screen_auto_reject_eligible"
    )
    assert auto_reject_rule["reject_reason"] == "custom"
