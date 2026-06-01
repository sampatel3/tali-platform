"""Unit tests for the ``deliverable`` schema extension.

Two slices:

- ``validate_deliverable``: catches malformed deliverable blocks at
  load time — missing kind, unsupported kind, primary_artifact that
  doesn't exist in repo_structure.files. These bugs are deployment-
  fatal, so they must fail boot, not the first candidate session.

- ``resolve_deliverable_kind``: the back-compat default. Tasks without
  a deliverable block (the 4 pilot engineering tasks today) must
  resolve to ``"code"`` so the existing IDE flow keeps working without
  schema changes.
"""

from __future__ import annotations

import pytest

from app.services.task_spec_loader import (
    resolve_deliverable_kind,
    validate_deliverable,
)


REPO_FILES = {
    "README.md": "# task",
    "DECISION_MEMO.md": "# memo",
    "src/main.py": "def run(): ...",
}


class TestValidateDeliverable:
    def test_none_is_valid(self):
        # Optional field: a task with no deliverable is a code-kind task
        # by default (back-compat for the engineering pilot tasks).
        assert validate_deliverable(None, REPO_FILES) == []

    def test_non_dict_is_invalid(self):
        errors = validate_deliverable("doc", REPO_FILES)
        assert any("must be an object" in e for e in errors)

    def test_valid_doc_kind_passes(self):
        block = {"kind": "doc", "primary_artifact": "DECISION_MEMO.md"}
        assert validate_deliverable(block, REPO_FILES) == []

    def test_valid_code_kind_passes(self):
        block = {"kind": "code", "primary_artifact": "src/main.py"}
        assert validate_deliverable(block, REPO_FILES) == []

    def test_missing_kind_is_caught(self):
        block = {"primary_artifact": "DECISION_MEMO.md"}
        errors = validate_deliverable(block, REPO_FILES)
        assert any("kind is required" in e for e in errors)

    def test_unsupported_kind_is_caught(self):
        block = {"kind": "spreadsheet", "primary_artifact": "DECISION_MEMO.md"}
        errors = validate_deliverable(block, REPO_FILES)
        assert any("kind must be one of" in e for e in errors)
        # Error message should hint at supported kinds — helps the
        # autogen pipeline (or a human author) figure out what to do.
        assert any("'code'" in e and "'doc'" in e for e in errors)

    def test_missing_primary_artifact_is_caught(self):
        block = {"kind": "doc"}
        errors = validate_deliverable(block, REPO_FILES)
        assert any("primary_artifact is required" in e for e in errors)

    def test_empty_primary_artifact_is_caught(self):
        block = {"kind": "doc", "primary_artifact": "   "}
        errors = validate_deliverable(block, REPO_FILES)
        assert any("primary_artifact must be a non-empty string" in e for e in errors)

    def test_primary_artifact_must_exist_in_repo_files(self):
        # The classic failure mode: task spec declares DECISION_MEMO.md
        # but the candidate's workspace doesn't ship that file. The
        # editor would auto-open a nonexistent file and the candidate
        # would see an empty buffer. Catch this at boot.
        block = {"kind": "doc", "primary_artifact": "GHOST.md"}
        errors = validate_deliverable(block, REPO_FILES)
        assert any("must match a file in repo_structure.files" in e for e in errors)

    def test_skips_repo_check_when_no_files_provided(self):
        # Some callers might validate the deliverable block before the
        # repo_structure has been normalised. Skip the existence check
        # in that case — the main validator's _validate_repo_structure
        # path catches the empty case separately.
        block = {"kind": "doc", "primary_artifact": "any.md"}
        assert validate_deliverable(block, {}) == []


class TestResolveDeliverableKind:
    def test_none_defaults_to_code(self):
        # Back-compat: the 4 pilot engineering tasks don't ship a
        # deliverable block. They must resolve to code so the existing
        # IDE flow keeps working without spec edits.
        assert resolve_deliverable_kind(None) == "code"

    def test_empty_dict_defaults_to_code(self):
        assert resolve_deliverable_kind({}) == "code"

    def test_missing_kind_defaults_to_code(self):
        assert resolve_deliverable_kind({"primary_artifact": "x.md"}) == "code"

    def test_unsupported_kind_defaults_to_code(self):
        # Defensive: a malformed kind shouldn't crash the runtime.
        # The loader-level validator catches this at boot, but the
        # resolver is called from the request path and must not raise.
        assert resolve_deliverable_kind({"kind": "spreadsheet"}) == "code"

    def test_explicit_doc_resolves_to_doc(self):
        assert resolve_deliverable_kind({"kind": "doc"}) == "doc"

    def test_non_dict_defaults_to_code(self):
        assert resolve_deliverable_kind("doc") == "code"
        assert resolve_deliverable_kind([]) == "code"


class TestPilotTaskSpecs:
    """Sanity-check that the canonical task catalog lands on the kinds
    we expect after the multi-role conversion."""

    def test_scrum_master_is_doc_kind(self):
        import json
        from pathlib import Path
        spec_path = (
            Path(__file__).resolve().parents[1]
            / "tasks" / "scrum_master_sprint_recovery_scenario.json"
        )
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        d = spec.get("deliverable")
        assert d is not None
        assert d["kind"] == "doc"
        assert d["primary_artifact"] == "HANDBACK.md"
        # And the primary_artifact actually exists in the repo.
        assert "HANDBACK.md" in spec["repo_structure"]["files"]

    def test_pm_stakeholder_conflict_is_doc_kind(self):
        import json
        from pathlib import Path
        spec_path = (
            Path(__file__).resolve().parents[1]
            / "tasks" / "product_mgmt_stakeholder_conflict.json"
        )
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        d = spec.get("deliverable")
        assert d is not None
        assert d["kind"] == "doc"
        assert d["primary_artifact"] == "DECISION_MEMO.md"
        assert "DECISION_MEMO.md" in spec["repo_structure"]["files"]
        # PM task must also ship decision_points for the interrogator
        # — otherwise the 0.20-weighted design_decisions_articulated
        # dim has nothing to grade.
        assert isinstance(spec.get("decision_points"), list)
        assert len(spec["decision_points"]) >= 2

    @pytest.mark.parametrize("task_id", [
        "data_eng_data_quality_contract_framework",
        "ai_eng_genai_production_readiness",
        "data_eng_aws_glue_pipeline_recovery",
        "ai_eng_rag_eval_harness",
    ])
    def test_engineering_pilot_tasks_resolve_to_code_kind(self, task_id):
        """The 4 engineering pilot tasks intentionally do NOT ship a
        deliverable block; they must resolve to code-kind by default.
        Adding deliverable.kind:code later is fine but not required."""
        import json
        from pathlib import Path
        spec_path = (
            Path(__file__).resolve().parents[1] / "tasks" / f"{task_id}.json"
        )
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        # Either: absent (resolves to code) OR explicitly kind:code.
        resolved = resolve_deliverable_kind(spec.get("deliverable"))
        assert resolved == "code"
