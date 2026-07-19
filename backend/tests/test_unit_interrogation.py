"""Unit tests for the schema-driven interrogation engine.

Covers the four pieces that need to behave deterministically across
arbitrary task specs (so the autogen pipeline target holds):

- ``validate_decision_points``: schema gatekeeping at load time.
- ``render_opener``: pure template; golden output equivalence with
  hand-authored task_opener strings the four pilot tasks shipped with.
- ``derive_interrogation_state`` + ``merge_state``: carry-forward
  semantics on the per-turn ``interrogation_state`` snapshots.
- ``build_interrogation_directive``: state-aware system-prompt block;
  empty when all resolved.

The classifier and the rubric-side grader are exercised in separate
tests below; the classifier is mocked because it makes a real Anthropic
call in production.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from app.components.assessments.interrogation import (
    all_resolved,
    build_interrogation_directive,
    classify_response,
    derive_interrogation_state,
    merge_state,
    render_opener,
    validate_decision_points,
    validate_traps,
)


def _two_dp_block() -> List[Dict[str, Any]]:
    """A minimal valid decision_points block used across the tests."""
    return [
        {
            "id": "shape",
            "headline": "The shape.",
            "tension": "Three consumers want different shapes.",
            "options": [
                {"label": "A", "summary": "first"},
                {"label": "B", "summary": "second"},
                {"label": "C", "summary": "third"},
            ],
            "ask": "Pick one.",
            "valid_commit": "names a specific consumer and the cost the others pay",
            "valid_reframes": ["proposes a superset shape and names which consumer pays"],
            "anti_patterns": ["lists all options", "asks Claude to decide"],
        },
        {
            "id": "severity",
            "headline": "Severity.",
            "tension": "Spec says WARN, partner says ERROR.",
            "options": [
                {"label": "WARN", "summary": "spec-canonical"},
                {"label": "ERROR", "summary": "blocks promotion"},
            ],
            "ask": "Which and would you check with finance?",
            "valid_commit": "names WARN or ERROR with one-sentence reason AND states whether they'd consult finance",
            "valid_reframes": ["names it as a requirements-gathering issue and proposes consulting finance first"],
            "anti_patterns": ["picks a label without rationale"],
        },
    ]


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestValidateDecisionPoints:
    def test_none_is_valid(self):
        assert validate_decision_points(None) == []

    def test_empty_list_is_invalid(self):
        errors = validate_decision_points([])
        assert any("non-empty" in e for e in errors)

    def test_non_list_is_invalid(self):
        errors = validate_decision_points({"id": "x"})
        assert any("must be a list" in e for e in errors)

    def test_valid_block_passes(self):
        assert validate_decision_points(_two_dp_block()) == []

    def test_missing_required_field_is_caught(self):
        bad = _two_dp_block()
        bad[0].pop("ask")
        errors = validate_decision_points(bad)
        assert any(".ask" in e for e in errors)

    def test_options_requires_two_entries(self):
        bad = _two_dp_block()
        bad[0]["options"] = [{"label": "A", "summary": "only one"}]
        errors = validate_decision_points(bad)
        assert any("≥2 entries" in e for e in errors)

    def test_option_missing_summary_is_caught(self):
        bad = _two_dp_block()
        bad[0]["options"][1].pop("summary")
        errors = validate_decision_points(bad)
        assert any(".summary" in e for e in errors)

    def test_duplicate_id_is_caught(self):
        bad = _two_dp_block()
        bad[1]["id"] = bad[0]["id"]
        errors = validate_decision_points(bad)
        assert any("duplicates" in e for e in errors)

    def test_reframes_must_be_list_of_strings(self):
        bad = _two_dp_block()
        bad[0]["valid_reframes"] = ["valid", 42]
        errors = validate_decision_points(bad)
        assert any("valid_reframes" in e for e in errors)


# ---------------------------------------------------------------------------
# Opener renderer
# ---------------------------------------------------------------------------


class TestRenderOpener:
    def test_empty_list_returns_empty_string(self):
        assert render_opener([]) == ""

    def test_renders_n_decisions_singular_vs_plural(self):
        one = render_opener(_two_dp_block()[:1])
        assert "1 decision that need" in one
        two = render_opener(_two_dp_block())
        assert "2 decisions that need" in two

    def test_each_decision_shows_headline_tension_options_ask(self):
        text = render_opener(_two_dp_block())
        assert "**1. The shape.**" in text
        assert "Three consumers want different shapes." in text
        assert "- **A**: first" in text
        assert "- **B**: second" in text
        assert "Pick one." in text
        assert "**2. Severity.**" in text
        assert "Spec says WARN, partner says ERROR." in text
        assert "Which and would you check with finance?" in text

    def test_includes_closing_pushback_statement(self):
        text = render_opener(_two_dp_block())
        assert "whatever you think" in text  # the polite-pushback contract
        assert "these need" in text

    def test_opens_with_orientation_greeting_before_decisions(self):
        text = render_opener(_two_dp_block())
        # The greeting must come first and offer the zero-stakes first move.
        assert text.startswith("Hi — I'm Claude")
        assert "ask me to run the tests" in text
        assert text.index("I'm Claude") < text.index("Before we start")


# ---------------------------------------------------------------------------
# State derivation + merge + resolved-check
# ---------------------------------------------------------------------------


class TestDeriveInterrogationState:
    def test_empty_ai_prompts_yields_unaddressed(self):
        state = derive_interrogation_state(_two_dp_block(), [])
        assert state == {"shape": "unaddressed", "severity": "unaddressed"}

    def test_walks_record_metadata(self):
        ai_prompts = [
            {"message": "", "response": "opener", "opener": True,
             "interrogation_state": {"shape": {"status": "unaddressed"}, "severity": {"status": "unaddressed"}}},
            {"message": "{passed, checks}", "response": "...",
             "interrogation_state": {"shape": {"status": "vague"}, "severity": {"status": "commit"}}},
            {"message": "I pick airflow, slack pays", "response": "...",
             "interrogation_state": {"shape": {"status": "commit"}, "severity": {"status": "commit"}}},
        ]
        state = derive_interrogation_state(_two_dp_block(), ai_prompts)
        assert state == {"shape": "commit", "severity": "commit"}

    def test_does_not_downgrade(self):
        # Once severity reaches commit, a subsequent unaddressed turn
        # must NOT roll it back to unaddressed.
        ai_prompts = [
            {"interrogation_state": {"severity": {"status": "commit"}}},
            {"interrogation_state": {"severity": {"status": "unaddressed"}}},
        ]
        state = derive_interrogation_state(_two_dp_block(), ai_prompts)
        assert state["severity"] == "commit"

    def test_reframe_treated_as_resolved(self):
        ai_prompts = [
            {"interrogation_state": {"shape": {"status": "reframe"}, "severity": {"status": "reframe"}}},
        ]
        state = derive_interrogation_state(_two_dp_block(), ai_prompts)
        assert all_resolved(state) is True

    def test_string_status_payload_accepted(self):
        # Older records might persist statuses as bare strings instead
        # of {status, rationale} dicts; the walker must tolerate both.
        ai_prompts = [{"interrogation_state": {"shape": "commit"}}]
        state = derive_interrogation_state(_two_dp_block(), ai_prompts)
        assert state["shape"] == "commit"


class TestMergeState:
    def test_carry_forward_upgrades_status(self):
        prior = {"shape": "vague", "severity": "unaddressed"}
        new = {"shape": {"status": "commit", "rationale": "named airflow"}}
        merged, persist = merge_state(prior, new)
        assert merged["shape"] == "commit"
        assert merged["severity"] == "unaddressed"  # untouched this turn
        assert persist["shape"]["status"] == "commit"
        # The dp not classified this turn still appears in persist so the
        # transcript replay can read it.
        assert "severity" in persist
        assert persist["severity"]["status"] == "unaddressed"

    def test_carry_forward_does_not_downgrade(self):
        prior = {"shape": "commit"}
        new = {"shape": {"status": "vague", "rationale": "candidate hedged"}}
        merged, persist = merge_state(prior, new)
        assert merged["shape"] == "commit"
        # persist records the carry-forward, raw_status tracks the
        # classifier's actual judgment for audit.
        assert persist["shape"]["status"] == "commit"
        assert persist["shape"]["raw_status"] == "vague"

    def test_unknown_status_falls_back_to_unaddressed(self):
        prior = {"shape": "unaddressed"}
        new = {"shape": {"status": "magnificent"}}
        merged, _ = merge_state(prior, new)
        assert merged["shape"] == "unaddressed"


class TestAllResolved:
    def test_empty_state_is_resolved(self):
        # No decisions = nothing to interrogate.
        assert all_resolved({}) is True

    def test_mixed_statuses(self):
        assert all_resolved({"a": "commit", "b": "vague"}) is False
        assert all_resolved({"a": "commit", "b": "reframe"}) is True
        assert all_resolved({"a": "dodge", "b": "commit"}) is False


# ---------------------------------------------------------------------------
# Directive builder
# ---------------------------------------------------------------------------


class TestBuildInterrogationDirective:
    def test_empty_when_all_resolved(self):
        directive = build_interrogation_directive(
            _two_dp_block(), {"shape": "commit", "severity": "reframe"},
        )
        assert directive == ""

    def test_empty_when_no_decision_points(self):
        assert build_interrogation_directive(None, {}) == ""
        assert build_interrogation_directive([], {}) == ""

    def test_lists_open_decisions_with_status(self):
        directive = build_interrogation_directive(
            _two_dp_block(),
            {"shape": "vague", "severity": "commit"},
        )
        assert "decision=shape" in directive
        assert "status=vague" in directive
        assert "decision=severity" in directive
        assert "status=commit" in directive
        # Rule block is always included when directive is non-empty.
        assert "INTERROGATION RULES" in directive

    def test_quotes_ask_text_for_open_decisions(self):
        directive = build_interrogation_directive(
            _two_dp_block(),
            {"shape": "vague", "severity": "unaddressed"},
        )
        # Both asks should be available so Claude can quote them verbatim
        # per the rules.
        assert "Pick one." in directive
        assert "Which and would you check with finance?" in directive


# ---------------------------------------------------------------------------
# Classifier — mock Anthropic
# ---------------------------------------------------------------------------


class TestClassifyResponse:
    """The classifier is the one piece that makes a real Anthropic call.

    Tests pin behaviour on (a) the by_dp shape, (b) ignoring unknown dp
    ids, (c) graceful failure on JSON errors / network errors / missing
    api_key, and (d) metering metadata.
    """

    def _stub_response(self, content_text: str) -> MagicMock:
        block = MagicMock()
        block.text = content_text
        response = MagicMock()
        response.content = [block]
        return response

    def test_returns_empty_when_no_decision_points(self):
        outcome = classify_response(
            decision_points=[],
            candidate_message="anything",
            prior_state={},
            api_key="sk-x",
            organization_id=1,
        )
        assert outcome.by_dp == {}
        assert outcome.error is None

    def test_returns_empty_with_error_when_api_key_missing(self):
        outcome = classify_response(
            decision_points=_two_dp_block(),
            candidate_message="anything",
            prior_state={},
            api_key="",
            organization_id=1,
        )
        assert outcome.error == "interrogation_classifier_unconfigured"
        assert outcome.by_dp == {}

    @patch("app.components.assessments.interrogation._reserve_classifier_call")
    @patch("app.components.assessments.interrogation.get_metered_interrogation_client")
    def test_parses_well_formed_response(
        self, mock_client_factory, mock_reserve
    ):
        mock_reserve.return_value.as_metering_payload.return_value = {
            "external_ref": "usage-hold:test",
        }
        client = mock_client_factory.return_value
        client.messages.create.return_value = self._stub_response(
            '{"by_dp": {"shape": {"status": "commit", "rationale": "named airflow"}, '
            '"severity": {"status": "reframe", "rationale": "ask finance first"}}}'
        )
        outcome = classify_response(
            decision_points=_two_dp_block(),
            candidate_message="airflow optimised; ask finance first",
            prior_state={"shape": "unaddressed", "severity": "unaddressed"},
            api_key="sk-x",
            organization_id=42,
            assessment_id=99,
            role_id=17,
            trace_id="assessment:99:chat:req-1:classifier",
        )
        assert outcome.by_dp == {
            "shape": {"status": "commit", "rationale": "named airflow"},
            "severity": {"status": "reframe", "rationale": "ask finance first"},
        }
        assert outcome.error is None
        # Metering shape pinned: MeteredAnthropicClient only persists
        # metering["metadata"] onto the UsageEvent row's metadata
        # column. ``sub_feature`` MUST live inside that nested dict —
        # at the top level it gets silently dropped (the bug fixed
        # 2026-06-01).
        call_kwargs = client.messages.create.call_args.kwargs
        assert call_kwargs["metering"]["organization_id"] == 42
        assert call_kwargs["metering"]["role_id"] == 17
        assert call_kwargs["metering"]["trace_id"] == "assessment:99:chat:req-1:classifier"
        assert call_kwargs["metering"]["entity_id"] == "assessment:99"
        assert call_kwargs["metering"]["metadata"]["sub_feature"] == "interrogation_classifier"
        assert call_kwargs["metering"]["metadata"]["trace_id"] == "assessment:99:chat:req-1:classifier"
        assert call_kwargs["metering"]["credit_reservation"] == {
            "external_ref": "usage-hold:test",
        }
        reserve_kwargs = mock_reserve.call_args.kwargs
        assert reserve_kwargs == {
            "organization_id": 42,
            "assessment_id": 99,
            "role_id": 17,
            "trace_id": "assessment:99:chat:req-1:classifier",
            "model": "claude-haiku-4-5-20251001",
            "provider_request": {
                key: value
                for key, value in call_kwargs.items()
                if key != "metering"
            },
        }

    @patch("app.components.assessments.interrogation._reserve_classifier_call")
    @patch("app.components.assessments.interrogation.get_metered_interrogation_client")
    def test_role_budget_admission_failure_skips_provider(
        self, mock_client_factory, mock_reserve
    ):
        mock_reserve.side_effect = RuntimeError("role cap exhausted")

        outcome = classify_response(
            decision_points=_two_dp_block(),
            candidate_message="pick airflow",
            prior_state={},
            api_key="sk-x",
            organization_id=42,
            assessment_id=99,
            role_id=17,
            trace_id="assessment:99:chat:req-cap:classifier",
        )

        assert outcome.by_dp == {}
        assert outcome.error == "interrogation_classifier_budget_blocked"
        assert "role cap exhausted" not in outcome.error
        mock_client_factory.return_value.messages.create.assert_not_called()

    @patch("app.components.assessments.interrogation.get_metered_interrogation_client")
    def test_drops_unknown_dp_ids(self, mock_client_factory):
        client = mock_client_factory.return_value
        client.messages.create.return_value = self._stub_response(
            '{"by_dp": {"shape": {"status": "commit"}, "made_up_id": {"status": "commit"}}}'
        )
        outcome = classify_response(
            decision_points=_two_dp_block(),
            candidate_message="x",
            prior_state={},
            api_key="sk-x",
            organization_id=1,
        )
        assert "shape" in outcome.by_dp
        assert "made_up_id" not in outcome.by_dp

    @patch("app.components.assessments.interrogation.get_metered_interrogation_client")
    def test_drops_unknown_statuses(self, mock_client_factory):
        client = mock_client_factory.return_value
        client.messages.create.return_value = self._stub_response(
            '{"by_dp": {"shape": {"status": "magnificent"}}}'
        )
        outcome = classify_response(
            decision_points=_two_dp_block(),
            candidate_message="x",
            prior_state={},
            api_key="sk-x",
            organization_id=1,
        )
        assert outcome.by_dp == {}

    @patch("app.components.assessments.interrogation.get_metered_interrogation_client")
    def test_tolerates_json_in_markdown_fences(self, mock_client_factory):
        client = mock_client_factory.return_value
        client.messages.create.return_value = self._stub_response(
            "```json\n{\"by_dp\": {\"shape\": {\"status\": \"commit\"}}}\n```"
        )
        outcome = classify_response(
            decision_points=_two_dp_block(),
            candidate_message="x",
            prior_state={},
            api_key="sk-x",
            organization_id=1,
        )
        assert outcome.by_dp == {"shape": {"status": "commit", "rationale": ""}}

    @patch("app.components.assessments.interrogation.get_metered_interrogation_client")
    def test_network_error_returns_stable_error_code(self, mock_client_factory, caplog):
        client = mock_client_factory.return_value
        provider_secret = "network response bearer-secret-must-not-escape"
        client.messages.create.side_effect = RuntimeError(provider_secret)
        outcome = classify_response(
            decision_points=_two_dp_block(),
            candidate_message="x",
            prior_state={},
            api_key="sk-x",
            organization_id=1,
        )
        assert outcome.by_dp == {}
        assert outcome.error == "interrogation_classifier_failed"
        assert provider_secret not in outcome.error
        assert provider_secret not in caplog.text


# ---------------------------------------------------------------------------
# Pilot-task golden checks
# ---------------------------------------------------------------------------


class TestPilotTaskGoldens:
    """Smoke-test that all four pilot tasks now ship decision_points and
    can be rendered. Failure here means the conversion script regressed."""

    @pytest.mark.parametrize("task_id", [
        "data_eng_data_quality_contract_framework",
        "ai_eng_genai_production_readiness",
        "data_eng_aws_glue_pipeline_recovery",
        "ai_eng_rag_eval_harness",
    ])
    def test_pilot_task_has_decisions_and_renders(self, task_id):
        import json
        from pathlib import Path
        spec_path = (
            Path(__file__).resolve().parents[1] / "tasks" / f"{task_id}.json"
        )
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        dps = spec.get("decision_points")
        assert isinstance(dps, list) and len(dps) >= 1
        # Loader-level schema must pass.
        assert validate_decision_points(dps) == []
        # Renders to a non-trivial opener.
        rendered = render_opener(dps)
        assert "Before we start" in rendered
        assert "whatever you think" in rendered
        # Rubric must declare interrogation_outcome grader.
        ddi = (spec.get("evaluation_rubric") or {}).get("design_decisions_articulated") or {}
        assert ddi.get("grader") == "interrogation_outcome"
        # And the legacy task_opener string must be gone.
        assert "task_opener" not in spec


# ---- validate_traps (PR-9: planted-trap discernment aid) -------------------


def test_validate_traps_none_and_valid():
    assert validate_traps(None) == []
    traps = [
        {"id": "t1", "planted": "agent suggests silencing the failing check", "tell": "candidate rejects it"},
        {"id": "t2", "planted": "agent papers over the contradiction", "tell": "candidate surfaces it", "where": "dq/severity.py"},
    ]
    assert validate_traps(traps) == []


def test_validate_traps_rejects_bad_shapes():
    assert validate_traps("nope") == ["traps must be a list"]
    assert validate_traps([]) == ["traps must be non-empty when present (drop the field instead)"]
    # missing required fields
    errs = validate_traps([{"id": "t1"}])
    assert any("planted" in e for e in errs)
    assert any("tell" in e for e in errs)
    # duplicate id
    dup = validate_traps([
        {"id": "t1", "planted": "x", "tell": "y"},
        {"id": "t1", "planted": "x2", "tell": "y2"},
    ])
    assert any("duplicates" in e for e in dup)
    # bad optional 'where'
    bad_where = validate_traps([{"id": "t1", "planted": "x", "tell": "y", "where": "   "}])
    assert any("where" in e for e in bad_where)
