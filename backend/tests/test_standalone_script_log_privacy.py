from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATHS = (
    BACKEND_ROOT / "scripts" / "fast_prescreen_roles.py",
    BACKEND_ROOT / "scripts" / "shadow_rescore_assessments.py",
    BACKEND_ROOT / "scripts" / "resend_failed_invites.py",
    BACKEND_ROOT / "scripts" / "backfill_application_score_cache.py",
    BACKEND_ROOT / "app" / "scripts" / "backfill_cv_sections.py",
    BACKEND_ROOT / "app" / "scripts" / "score_advanced_for_training.py",
)


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        owner = func.value
        if isinstance(owner, ast.Name):
            return f"{owner.id}.{func.attr}"
        return func.attr
    return ""


@pytest.mark.parametrize("path", SCRIPT_PATHS, ids=lambda path: path.name)
def test_standalone_scripts_use_central_logging_without_plaintext_fallbacks(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    names = [_call_name(call) for call in calls]

    assert "logging.basicConfig" not in names
    assert "basicConfig" not in names
    assert "print" not in names
    assert not any(name.endswith(".exception") or name == "exception" for name in names)
    assert "setup_logging" in names


def test_cv_section_backfill_records_only_stable_exception_type(monkeypatch, caplog):
    from app.scripts import backfill_cv_sections

    secret = "provider-response bearer-secret candidate-private-cv"
    monkeypatch.setattr(
        "app.platform.logging.setup_logging",
        logging.getLogger,
    )
    monkeypatch.setattr(
        backfill_cv_sections,
        "_process_one",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    result = backfill_cv_sections.main(["--application-id", "73", "--dry-run"])

    assert result == 1
    assert secret not in caplog.text
    assert "application_id=73" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
