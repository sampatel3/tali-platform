"""Unit tests for the JD→task-spec generator.

The generation call is mocked (it's a real Anthropic call in prod). Tests
pin: JSON extraction tolerance, the validate→repair loop, graceful
exhaustion, metering shape, and that a valid model output passes straight
through.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.task_spec_generator import (
    GeneratedSpecResult,
    generate_task_spec,
    _extract_json,
)
from app.services.usage_credit_reservations import (
    CreditReservation,
    InsufficientRoleBudgetError,
)
from app.services.usage_metering_service import InsufficientCreditsError


def _valid_spec() -> dict:
    """A minimal spec that passes validate_task_spec."""
    return {
        "task_id": "secops_vuln_triage",
        "name": "Vuln Triage",
        "role": "security_engineer",
        "duration_minutes": 30,
        "calibration_prompt": "Pick a triage order and defend it.",
        "scenario": "A scanner dumped 400 findings overnight. Triage them.",
        "deliverable": {"kind": "code", "primary_artifact": "src/triage.py", "submission_check": "test_runner"},
        "decision_points": [
            {
                "id": "triage_order",
                "headline": "Triage order.",
                "tension": "Severity vs exploitability vs blast radius conflict.",
                "options": [{"label": "CVSS", "summary": "by score"}, {"label": "Exploitability", "summary": "by KEV"}],
                "ask": "Pick one ordering and name what it under-serves.",
                "valid_commit": "names one ordering and the cost",
                "valid_reframes": ["argues for a blended score and names the weighting"],
                "anti_patterns": ["'do all'", "asks the agent to decide"],
            },
        ],
        "evaluation_rubric": {
            "problem_diagnosis": {"weight": 0.20, "lens": "decision", "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
            "design_decisions_articulated": {"weight": 0.40, "grader": "interrogation_outcome"},
            "triage_correctness": {"weight": 0.22, "lens": "deliverable", "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
            "remediation_quality": {"weight": 0.18, "lens": "deliverable", "criteria": {"excellent": "x", "good": "y", "poor": "z"}},
        },
        "expected_candidate_journey": {"read": ["read findings"], "decide": ["pick order"], "ship": ["implement"]},
        "interviewer_signals": {"strong_positive": ["owns the order"], "red_flags": ["delegates triage"]},
        "scoring_hints": {"calibration": "judgment-first"},
        "test_runner": {
            "command": "./.venv/bin/python -m pytest -q --tb=short",
            "working_dir": "/workspace/vuln-triage",
            "parse_pattern": r"(?P<passed>\d+) passed|(?P<failed>\d+) failed",
            "timeout_seconds": 90,
        },
        "workspace_bootstrap": {
            "commands": ["python3 -m venv .venv", "./.venv/bin/pip install -r requirements.txt"],
            "working_dir": "/workspace/vuln-triage",
            "timeout_seconds": 180,
            "must_succeed": True,
        },
        "repo_structure": {
            "name": "vuln-triage",
            "files": {
                "README.md": "# Vuln triage",
                "FINDINGS.md": "400 findings",
                "src/triage.py": "def triage(findings):\n    return []  # stub\n",
                "tests/test_triage.py": "from src.triage import triage\n\ndef test_orders():\n    assert triage([]) == []\n",
                "requirements.txt": "pytest\n",
            },
        },
        "role_alignment": {
            "source_user_email": "generated@taali.ai",
            "source_role_name": "Specialist - Vulnerability Management",
            "source_role_identifier": "specialist_vulnerability_management",
            "captured_at": "2026-01-01T00:00:00Z",
            "must_cover": ["triage under conflicting signals"],
            "must_not_cover": ["no actual exploitation"],
            "jd_to_signal_map": [
                {"job_requirement": "diagnose", "task_artifact": "transcript", "rubric_dimension": "problem_diagnosis"},
                {"job_requirement": "decide", "task_artifact": "transcript", "rubric_dimension": "design_decisions_articulated"},
                {"job_requirement": "triage", "task_artifact": "src/triage.py", "rubric_dimension": "triage_correctness"},
                {"job_requirement": "remediate", "task_artifact": "src/triage.py", "rubric_dimension": "remediation_quality"},
            ],
        },
        "human_testing_checklist": {
            "candidate_clarity": True, "repo_boot_ok": True, "tests_collect_ok": True,
            "baseline_failures_meaningful": True, "rubric_matches_role": True, "timebox_realistic": True,
        },
    }


def _resp(text: str) -> MagicMock:
    block = MagicMock()
    block.text = text
    r = MagicMock()
    r.content = [block]
    return r


class TestExtractJson:
    def test_plain(self):
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_fenced(self):
        assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_prose_wrapped(self):
        assert _extract_json('Here you go:\n{"a": 1}\nDone.') == {"a": 1}

    def test_junk_returns_none(self):
        assert _extract_json("not json at all") is None


class TestGenerateTaskSpec:
    def test_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key"):
            generate_task_spec(role_name="x", role_slug="x", jd_text="x", api_key="", organization_id=1)

    @patch("app.services.task_spec_generator.Anthropic")
    @patch("app.services.task_spec_generator.MeteredAnthropicClient")
    @patch("app.services.task_spec_generator._reserve_generation_attempt")
    def test_valid_first_try_passes_through(
        self, reserve_attempt, mock_client_cls, _a
    ):
        reserve_attempt.return_value = CreditReservation(
            organization_id=2,
            feature="assessment",
            amount=1_440_000,
            external_ref="usage-reservation:task-spec:test",
            live=False,
        )
        client = mock_client_cls.return_value
        client.messages.create.return_value = _resp(json.dumps(_valid_spec()))
        res = generate_task_spec(
            role_name="Specialist - Vulnerability Management",
            role_slug="specialist_vulnerability_management",
            jd_text="triage vulns", api_key="sk-x", organization_id=2, role_id=7,
        )
        assert res.valid is True
        assert res.attempts == 1
        assert res.spec["task_id"] == "secops_vuln_triage"
        # Metering shape pinned.
        kw = client.messages.create.call_args.kwargs
        assert kw["metering"]["metadata"]["sub_feature"] == "task_spec_generation"
        assert kw["metering"]["role_id"] == 7
        assert kw["metering"]["entity_id"] == "role:7"
        assert kw["metering"]["trace_id"].startswith("task-spec-")
        assert kw["metering"]["metadata"]["trace_id"] == kw["metering"]["trace_id"]
        assert kw["metering"]["credit_reservation"]["organization_id"] == 2
        assert kw["metering"]["credit_reservation"]["feature"] == "assessment"
        assert kw["metering"]["credit_reservation"]["amount"] > 0

    @patch("app.services.task_spec_generator.Anthropic")
    @patch("app.services.task_spec_generator.MeteredAnthropicClient")
    def test_repair_loop_recovers(self, mock_client_cls, _a):
        client = mock_client_cls.return_value
        bad = _valid_spec()
        bad["evaluation_rubric"]["problem_diagnosis"]["weight"] = 0.9  # weights won't sum to 1.0
        good = _valid_spec()
        client.messages.create.side_effect = [_resp(json.dumps(bad)), _resp(json.dumps(good))]
        res = generate_task_spec(
            role_name="x", role_slug="x", jd_text="x", api_key="sk-x", organization_id=1,
        )
        assert res.valid is True
        assert res.attempts == 2

    @patch("app.services.task_spec_generator.Anthropic")
    @patch("app.services.task_spec_generator.MeteredAnthropicClient")
    def test_exhaustion_returns_best_with_errors(self, mock_client_cls, _a):
        client = mock_client_cls.return_value
        bad = _valid_spec()
        bad["evaluation_rubric"]["problem_diagnosis"]["weight"] = 0.9
        client.messages.create.return_value = _resp(json.dumps(bad))
        res = generate_task_spec(
            role_name="x", role_slug="x", jd_text="x", api_key="sk-x",
            organization_id=1, max_attempts=2,
        )
        assert res.valid is False
        assert res.attempts == 2
        assert res.spec is not None  # best invalid attempt returned
        assert any("1.0" in e for e in res.errors)

    @patch("app.services.task_spec_generator.Anthropic")
    @patch("app.services.task_spec_generator.MeteredAnthropicClient")
    def test_non_json_then_valid(self, mock_client_cls, _a):
        client = mock_client_cls.return_value
        client.messages.create.side_effect = [_resp("sorry, here are some thoughts..."), _resp(json.dumps(_valid_spec()))]
        res = generate_task_spec(
            role_name="x", role_slug="x", jd_text="x", api_key="sk-x", organization_id=1,
        )
        assert res.valid is True
        assert res.attempts == 2

    @patch("app.services.task_spec_generator.Anthropic")
    @patch("app.services.task_spec_generator.MeteredAnthropicClient")
    def test_call_failure_is_graceful(self, mock_client_cls, _a):
        client = mock_client_cls.return_value
        client.messages.create.side_effect = RuntimeError("network down")
        res = generate_task_spec(
            role_name="x", role_slug="x", jd_text="x", api_key="sk-x", organization_id=1,
        )
        assert res.valid is False
        assert any("network down" in e for e in res.errors)

    @patch("app.services.task_spec_generator.Anthropic")
    @patch("app.services.task_spec_generator.MeteredAnthropicClient")
    @patch("app.services.task_spec_generator._reserve_generation_attempt")
    def test_insufficient_reservation_blocks_before_provider(
        self, reserve_attempt, mock_client_cls, _a
    ):
        reserve_attempt.side_effect = InsufficientCreditsError(
            organization_id=2,
            required=1_000_000,
            available=999_999,
        )

        res = generate_task_spec(
            role_name="x",
            role_slug="x",
            jd_text="x",
            api_key="sk-x",
            organization_id=2,
            role_id=7,
        )

        assert res.valid is False
        assert res.attempts == 0
        assert "insufficient usage credits" in res.errors[0]
        assert not mock_client_cls.return_value.messages.create.called

    @patch("app.services.task_spec_generator.Anthropic")
    @patch("app.services.task_spec_generator.MeteredAnthropicClient")
    @patch("app.services.task_spec_generator._reserve_generation_attempt")
    def test_role_cap_blocks_before_provider(
        self, reserve_attempt, mock_client_cls, _a
    ):
        reserve_attempt.side_effect = InsufficientRoleBudgetError(
            role_id=7,
            required=1_440_000,
            available=1_000_000,
        )

        res = generate_task_spec(
            role_name="x",
            role_slug="x",
            jd_text="x",
            api_key="sk-x",
            organization_id=2,
            role_id=7,
        )

        assert res.valid is False
        assert res.attempts == 0
        assert "insufficient role monthly budget" in res.errors[0]
        assert not mock_client_cls.return_value.messages.create.called

    @patch("app.services.task_spec_generator.Anthropic")
    @patch("app.services.task_spec_generator.MeteredAnthropicClient")
    @patch("app.services.task_spec_generator._release_generation_attempt")
    @patch("app.services.task_spec_generator._reserve_generation_attempt")
    def test_provider_failure_releases_hard_reservation(
        self, reserve_attempt, release_attempt, mock_client_cls, _a
    ):
        held = CreditReservation(
            organization_id=2,
            feature="assessment",
            amount=1_000_000,
            external_ref="usage-reservation:test",
            live=True,
        )
        reserve_attempt.return_value = held
        mock_client_cls.return_value.messages.create.side_effect = RuntimeError(
            "network down"
        )

        res = generate_task_spec(
            role_name="x",
            role_slug="x",
            jd_text="x",
            api_key="sk-x",
            organization_id=2,
            role_id=7,
        )

        assert res.valid is False
        release_attempt.assert_called_once()
        assert release_attempt.call_args.args[0] == held
