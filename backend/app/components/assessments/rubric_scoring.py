"""Rubric-driven scoring engine for candidate assessments.

Before this module, ``submission_runtime`` scored assessments by a mix of
prompt-behaviour heuristics (length, time-to-first-prompt, self-correction
rate) and a generic ``claude.analyze_code_quality()`` call on the final
code. The ``task.evaluation_rubric`` JSON in each task spec was stored,
shown to recruiters, but never consulted by the scorer. So a candidate
who delegated heavily to the model could still get a moderate score
because the rubric criteria — the thing the assessment claims to measure
— were dead code.

This module wires the rubric in: each ``evaluation_rubric.dimensions[*]``
entry gets graded against its declared criteria by a Claude call,
weighted, and aggregated into a final score. Per-dimension reasoning +
evidence citations are returned so the recruiter UI can show *why* a
candidate scored what they did.

Public surface
--------------
- :class:`ScoringArtifacts` — typed bundle of what the grader can see
  (final repo files, prompt transcript, design doc, test summary).
- :class:`DimensionGrade` — one rubric dimension's result.
- :class:`RubricResult` — aggregate across all dimensions, weighted.
- :class:`RubricScorer` — orchestrates the per-dimension Claude calls.

Wiring into ``submission_runtime`` lands in a follow-up PR; this PR is
the module + unit tests so the grading shape can be reviewed in
isolation. Metering follows the platform's invariant (every Anthropic
call writes a ``UsageEvent`` via ``MeteredAnthropicClient``;
``sub_feature=rubric_scoring``, dimension id in ``metadata``).

Model choice
------------
Defaults to Sonnet 4.5 (``claude-sonnet-4-5-20250929``) because grading
happens once per submission, not per chat turn — latency tolerance is
higher, and Sonnet's reasoning + JSON adherence are materially better
than Haiku on this kind of structured judgment work. ``model=`` kwarg
overrides for ops escape hatch.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from anthropic import Anthropic

from ...platform.config import settings
from ...services.metered_anthropic_client import MeteredAnthropicClient
from .interrogation import (
    RESOLVED_STATUSES,
    derive_interrogation_state,
)

logger = logging.getLogger("taali.assessments.rubric_scoring")

_DEFAULT_RUBRIC_SCORING_MODEL = "claude-sonnet-4-5-20250929"
_MAX_TOKENS_PER_DIMENSION = 1500

# Caller can supply richer artifacts but these defaults keep prompts
# bounded so we don't fan out to 30k-token grader calls.
_MAX_REPO_FILE_CHARS = 6000
_MAX_REPO_FILES = 20
_MAX_DESIGN_DOC_CHARS = 8000
_MAX_PROMPT_TRANSCRIPT_TURNS = 20
_MAX_PROMPT_TURN_CHARS = 2000
# Process-trace bounds (only applied when ``include_process_trace`` is set).
_MAX_TOOL_CALLS_PER_TURN = 8
_MAX_TOOL_TARGET_CHARS = 120
_MAX_TOOL_RESULT_EXCERPT_CHARS = 600
_MAX_GIT_DIFF_CHARS = 6000
_MAX_GIT_COMMITS_CHARS = 1500


@dataclass(frozen=True)
class ScoringArtifacts:
    """Everything a grader can see about a submission.

    Build one of these in ``submission_runtime`` and pass it through;
    individual graders pull the slices they need (e.g. a "code quality"
    dimension reads ``repo_files``, a "design documentation" dimension
    reads ``design_doc``).

    Attributes:
        repo_files: ``{relative_path: file_contents}`` — final state.
        design_doc: contents of ``DESIGN.md`` (or the per-task
            equivalent) if the candidate wrote one; empty string
            otherwise. Empty != graded zero; the dimension's grader
            decides how to score a missing doc.
        prompt_transcript: list of ``{message, response}`` pairs from
            ``ai_prompts`` in submission order. Most recent first or
            chronological — caller's choice; pass through as-is.
        test_results_summary: short text like
            ``"7 of 9 passed, failures: test_gate_blocks_warn".``
        task_scenario: candidate-facing task brief (so the grader has
            context for what was asked).
        candidate_role: e.g. ``"data_engineer"`` — informs grader on
            seniority/role expectations.
    """

    repo_files: Dict[str, str] = field(default_factory=dict)
    design_doc: str = ""
    prompt_transcript: List[Dict[str, Any]] = field(default_factory=list)
    test_results_summary: str = ""
    task_scenario: str = ""
    candidate_role: str = ""
    # Schema-driven interrogation dimension support. ``decision_points``
    # is the task's structured decision list (extra_data.decision_points
    # at the task spec layer); the ``interrogation_outcome`` grader reads
    # it together with the per-turn ``interrogation_state`` snapshots
    # written onto each ai_prompts record to score the dimension
    # deterministically — no Anthropic call for this dim.
    decision_points: List[Dict[str, Any]] = field(default_factory=list)
    # PR-2 (process-visible grading): when True, prompt_transcript_excerpt()
    # interleaves each turn's agent tool calls + results, and
    # git_evidence_excerpt() surfaces the committed diff — so the grader can
    # read HOW the candidate drove the agent (verification, iteration), not
    # just the message/response text. Gated by ASSESSMENT_GRADER_PROCESS_TRACE
    # so the rollout is reversible + shadow-validatable before it moves live
    # scores. git_evidence is assessment.git_evidence (head_sha, diff_main,
    # commits, status_porcelain, ...).
    include_process_trace: bool = False
    git_evidence: Dict[str, Any] = field(default_factory=dict)

    def repo_files_excerpt(self) -> str:
        """Concatenated repo files for prompt embedding (bounded)."""
        if not self.repo_files:
            return "(no repo files)"
        parts: List[str] = []
        for i, (path, content) in enumerate(sorted(self.repo_files.items())):
            if i >= _MAX_REPO_FILES:
                parts.append(f"... ({len(self.repo_files) - i} more files omitted)")
                break
            body = str(content or "")
            if len(body) > _MAX_REPO_FILE_CHARS:
                body = body[:_MAX_REPO_FILE_CHARS] + "\n... (truncated)"
            parts.append(f"--- {path} ---\n{body}")
        return "\n\n".join(parts)

    def design_doc_excerpt(self) -> str:
        if not self.design_doc:
            return "(no DESIGN.md submitted)"
        if len(self.design_doc) > _MAX_DESIGN_DOC_CHARS:
            return self.design_doc[:_MAX_DESIGN_DOC_CHARS] + "\n... (truncated)"
        return self.design_doc

    def prompt_transcript_excerpt(self) -> str:
        if not self.prompt_transcript:
            return "(no prompts in transcript)"
        turns = self.prompt_transcript[-_MAX_PROMPT_TRANSCRIPT_TURNS:]
        lines: List[str] = []
        for i, turn in enumerate(turns, 1):
            user = str(turn.get("message", "") or "")[:_MAX_PROMPT_TURN_CHARS]
            asst = str(turn.get("response", "") or "")[:_MAX_PROMPT_TURN_CHARS]
            block = [f"### Turn {i}", f"[Candidate]: {user}"]
            if self.include_process_trace:
                tool_lines = self._render_tool_calls(turn.get("tool_calls_made") or [])
                if tool_lines:
                    block.append("[Agent actions]:")
                    block.extend(tool_lines)
            block.append(f"[Claude]: {asst}")
            lines.append("\n".join(block))
        return "\n\n".join(lines)

    @staticmethod
    def _render_tool_calls(tool_calls: List[Dict[str, Any]]) -> List[str]:
        """One bounded line per tool call: ``- Bash(pytest -q) → 2 failed``.

        Shows what the agent did (tool + target) and what it OBSERVED (the
        result excerpt + an error flag) so the grader can judge whether the
        candidate verified the agent's work. Only rendered under
        ``include_process_trace``.
        """
        out: List[str] = []
        for call in tool_calls[:_MAX_TOOL_CALLS_PER_TURN]:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name", "")).split("__")[-1] or "tool"
            inp = call.get("input")
            target = ""
            if isinstance(inp, dict):
                target = str(inp.get("path") or inp.get("command") or inp.get("file_path") or "")
            target = target.replace("\n", " ")[:_MAX_TOOL_TARGET_CHARS]
            line = f"  - {name}({target})" if target else f"  - {name}"
            if "result" in call:
                flag = " [error]" if call.get("is_error") else ""
                result = str(call.get("result") or "").replace("\n", " ")[:_MAX_TOOL_RESULT_EXCERPT_CHARS]
                line += f"{flag} → {result}"
            out.append(line)
        extra = len(tool_calls) - _MAX_TOOL_CALLS_PER_TURN
        if extra > 0:
            out.append(f"  - … ({extra} more tool call(s))")
        return out

    def git_evidence_excerpt(self) -> str:
        """Committed diff + commit log for the grader (bounded). Empty unless
        ``include_process_trace`` is on and git evidence was captured."""
        if not self.include_process_trace or not self.git_evidence:
            return ""
        ge = self.git_evidence if isinstance(self.git_evidence, dict) else {}
        parts: List[str] = []
        commits = str(ge.get("commits") or "").strip()
        if commits:
            parts.append("Commits (git log --oneline):\n" + commits[:_MAX_GIT_COMMITS_CHARS])
        diff = str(ge.get("diff_main") or ge.get("diff") or "").strip()
        if diff:
            body = diff[:_MAX_GIT_DIFF_CHARS]
            if len(diff) > _MAX_GIT_DIFF_CHARS:
                body += "\n... (diff truncated)"
            parts.append("Diff vs base:\n" + body)
        return "\n\n".join(parts)


@dataclass(frozen=True)
class DimensionGrade:
    """One rubric dimension's graded result."""

    dimension_id: str
    score: float  # 0-10
    rating: str  # one of "excellent" | "good" | "poor" — matches rubric tiers
    reasoning: str  # 1-3 sentences from the grader
    evidence_citations: List[str] = field(default_factory=list)  # specific file:line or transcript-turn refs
    weight: float = 0.0  # carried through from rubric for aggregation
    error: Optional[str] = None  # set when the grader call failed; score=0 in that case


@dataclass(frozen=True)
class RubricResult:
    """Aggregated rubric grading across all dimensions."""

    dimensions: List[DimensionGrade]
    weighted_score_100: float  # 0-100, weight-aggregated across dimensions
    model_used: str
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    failed_dimension_ids: List[str] = field(default_factory=list)

    @property
    def fully_graded(self) -> bool:
        return not self.failed_dimension_ids


# ---- Prompt construction ----------------------------------------------------

_GRADER_PREAMBLE = (
    "You are grading ONE rubric dimension of a technical assessment where the "
    "candidate works WITH an AI coding agent. You will receive: (1) the "
    "criterion's excellent / good / poor descriptors, (2) the submission "
    "artifacts (the final repo the candidate shipped, the chat transcript "
    "with the agent — which, where available, interleaves the agent's tool "
    "calls + results and the candidate's git diff so you can see how the work "
    "was actually done), (3) the task scenario."
)
_GRADER_OUTPUT = (
    "\n\nChoose ONE rating tier ('excellent', 'good', or 'poor'), assign a "
    "0-10 score, write a 1-3 sentence reasoning, and list specific evidence "
    "citations (e.g. 'dq/gate.py line 23', 'transcript turn 4'). Score 9-10 "
    "for excellent, 5-8 for good, 0-4 for poor. Be specific and "
    "evidence-based."
    "\n\n"
    "Respond ONLY with valid JSON, no markdown formatting, matching:\n"
    '{"score": <0-10>, "rating": "<excellent|good|poor>", '
    '"reasoning": "<1-3 sentences>", '
    '"evidence_citations": ["<citation>", ...]}'
)

# DECISION lens — judgment as evidenced in the transcript. Lazy delegation
# is punished HERE (and only here). This is where "did the candidate steer +
# reason" lives.
_DECISION_LENS_PROMPT = (
    _GRADER_PREAMBLE
    + "\n\nLENS: DECISION. You are grading the candidate's JUDGMENT and "
    "STEERING as evidenced in the chat transcript. What matters is whether "
    "THE CANDIDATE made and owned the load-bearing calls, diagnosed the real "
    "problem, and directed the work — not what the agent produced. A "
    "candidate who delegated with 'fix it' / 'do all 3' and never engaged "
    "the decision scores POOR here regardless of how good the agent's output "
    "was; this lens measures the candidate's reasoning, not the model's. "
    "Reaching a working result without demonstrating design thinking is NOT "
    "the target — design thinking is."
    + _GRADER_OUTPUT
)

# DELIVERABLE lens — the shipped artifact on its own merits. Credit good
# output regardless of who typed it; directing an agent to a correct
# solution IS the skill. Nothing-shipped = poor.
_DELIVERABLE_LENS_PROMPT = (
    _GRADER_PREAMBLE
    + "\n\nLENS: DELIVERABLE. You are grading the SHIPPED ARTIFACT on its own "
    "merits. The candidate is EXPECTED to use the AI agent — directing an "
    "agent to a correct, well-structured solution IS the skill being "
    "measured. DO NOT penalise the candidate for using the agent to produce "
    "the artifact; judge what was actually shipped in the final repo. If "
    "nothing coherent was shipped (empty / stubbed / broken, tests failing), "
    "that is POOR — the candidate is accountable for shipping a working "
    "result."
    + _GRADER_OUTPUT
)

# Back-compat default for any dimension that doesn't declare a lens (treated
# as decision-leaning, the historical behaviour).
_SYSTEM_PROMPT = _DECISION_LENS_PROMPT


def _system_prompt_for_lens(lens: Optional[str]) -> str:
    if (lens or "").strip().lower() == "deliverable":
        return _DELIVERABLE_LENS_PROMPT
    return _DECISION_LENS_PROMPT


def _build_user_prompt(
    dimension_id: str,
    criteria: Dict[str, str],
    artifacts: ScoringArtifacts,
) -> str:
    excellent = (criteria.get("excellent") or "").strip() or "(not specified)"
    good = (criteria.get("good") or "").strip() or "(not specified)"
    poor = (criteria.get("poor") or "").strip() or "(not specified)"
    role = artifacts.candidate_role or "(unspecified role)"
    scenario = (artifacts.task_scenario or "(no scenario)").strip()
    git_excerpt = artifacts.git_evidence_excerpt()
    sections = [
        f"Rubric dimension: **{dimension_id}**",
        (
            "Tier descriptors:\n"
            f"- EXCELLENT (9-10): {excellent}\n"
            f"- GOOD (5-8): {good}\n"
            f"- POOR (0-4): {poor}"
        ),
        f"Candidate role: {role}",
        f"Task scenario the candidate was given:\n{scenario}",
        f"Test runner outcome:\n{artifacts.test_results_summary or '(not provided)'}",
        f"Candidate's DESIGN.md (if any):\n{artifacts.design_doc_excerpt()}",
        (
            "Candidate's chat transcript with Claude (an [Agent actions] block, "
            "when present, lists the agent's tool calls + results so you can "
            "judge how the candidate steered and verified the work):\n"
            f"{artifacts.prompt_transcript_excerpt()}"
        ),
    ]
    if git_excerpt:
        sections.append(
            "Candidate's git history + diff (what they actually committed):\n"
            f"{git_excerpt}"
        )
    sections.append(
        "Final repository state (candidate's submitted code):\n"
        f"{artifacts.repo_files_excerpt()}"
    )
    sections.append("Grade this dimension now. Respond with the JSON shape only.")
    return "\n\n".join(sections)


# ---- Scorer -----------------------------------------------------------------


class RubricScorer:
    """Drives per-dimension grading against an ``evaluation_rubric``.

    One instance per submission. Constructor binds the metering org and
    the model choice. ``grade_rubric`` is the high-level entry point —
    it iterates ``rubric.items()``, calls ``grade_dimension`` for each,
    and aggregates with weights.

    Resilience: a single dimension grader failing (network blip, malformed
    JSON, etc.) does NOT fail the whole scoring run. The failed dimension
    is recorded in ``RubricResult.failed_dimension_ids`` with score=0 and
    the others still grade — the recruiter UI can then surface the gap
    for human re-grading rather than blocking the submit flow.
    """

    def __init__(
        self,
        api_key: str,
        organization_id: int,
        *,
        assessment_id: Optional[int] = None,
        model: Optional[str] = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._client = MeteredAnthropicClient(
            inner=Anthropic(api_key=api_key),
            organization_id=int(organization_id),
        )
        self._organization_id = int(organization_id)
        self._assessment_id = assessment_id
        self._model = (model or "").strip() or _DEFAULT_RUBRIC_SCORING_MODEL
        logger.info(
            "RubricScorer init org=%s assessment=%s model=%s",
            self._organization_id, self._assessment_id, self._model,
        )

    # ---- internal --------------------------------------------------------

    def _metering(self, dimension_id: str) -> Dict[str, Any]:
        # MeteredAnthropicClient only persists keys from ``metering[metadata]``
        # onto the UsageEvent row's metadata column. ``sub_feature`` and
        # ``dimension`` have to ride inside metadata or they disappear,
        # which left rubric_scoring rows un-attributable until 2026-06-01.
        grader_meta: Dict[str, Any] = {
            "sub_feature": "rubric_scoring",
            "dimension": dimension_id,
        }
        if self._assessment_id is not None:
            grader_meta["assessment_id"] = str(self._assessment_id)
        meta: Dict[str, Any] = {
            "feature": "assessment",
            "organization_id": self._organization_id,
            "metadata": grader_meta,
        }
        if self._assessment_id is not None:
            meta["entity_id"] = f"assessment:{self._assessment_id}"
        return meta

    def _parse_grader_response(self, raw: str, dimension_id: str) -> Dict[str, Any]:
        """Parse the grader's JSON response. Tolerant of stray markdown
        fences a misbehaving grader sometimes wraps the JSON in."""
        text = (raw or "").strip()
        # Strip ```json ... ``` fences if present
        if text.startswith("```"):
            stripped = text.split("\n", 1)[-1] if "\n" in text else text
            text = stripped.rsplit("```", 1)[0].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning(
                "RubricScorer grader returned non-JSON for dimension=%s: %s",
                dimension_id, exc,
            )
            raise

    # ---- public ----------------------------------------------------------

    def grade_dimension_via_interrogation_outcome(
        self,
        dimension_id: str,
        artifacts: ScoringArtifacts,
        *,
        weight: float = 0.0,
    ) -> DimensionGrade:
        """Deterministic grader for the ``design_decisions_articulated``
        dimension (or any dim with ``grader: "interrogation_outcome"``).

        Reads the per-turn classifier state from each ai_prompts record
        plus the task's ``decision_points`` list and derives the final
        per-dp outcome via the same carry-forward semantics the chat
        route used at runtime — so this re-scoring is byte-equivalent
        to "what did the runtime see at the last turn".

        Mapping is fixed and explicit:
        - any decision ended at ``dodge``               → poor   (score=2.0)
        - all decisions in {commit, reframe}            → excellent (9.5)
        - any dodge (decision handed back to the agent)  → poor   (1.5)
        - partial, no dodge: 2.0 + (resolved/total)·5
            e.g. 1-of-2 owned → 4.5 (poor); both vague → 2.0 (poor)

        Judgment-first: steering EVERY decision is the skill, so partial
        engagement scores low. The thresholds live in one place; change
        them centrally if calibration data demands it.

        No Anthropic call → zero metering rows for this dim. The classifier
        ran per-turn during the chat (already metered as
        ``sub_feature=interrogation_classifier``); re-deriving at submit
        time is pure-Python.
        """
        decision_points = list(artifacts.decision_points or [])
        if not decision_points:
            return DimensionGrade(
                dimension_id=dimension_id, score=0.0, rating="poor",
                reasoning=(
                    "No decision_points defined for this task; the "
                    "interrogation grader cannot evaluate."
                ),
                evidence_citations=[], weight=weight,
                error="missing_decision_points",
            )
        state = derive_interrogation_state(
            decision_points, artifacts.prompt_transcript or [],
        )
        per_dp_lines: List[str] = []
        n_resolved = 0
        n_dodge = 0
        for dp in decision_points:
            dp_id = dp.get("id")
            if not isinstance(dp_id, str) or not dp_id:
                continue
            status = state.get(dp_id, "unaddressed")
            headline = str(dp.get("headline") or dp_id).strip()
            per_dp_lines.append(f"{dp_id} ({headline}): {status}")
            if status in RESOLVED_STATUSES:
                n_resolved += 1
            if status == "dodge":
                n_dodge += 1
        n_total = len(per_dp_lines)
        if n_total == 0:
            return DimensionGrade(
                dimension_id=dimension_id, score=0.0, rating="poor",
                reasoning="decision_points present but none had a valid id.",
                evidence_citations=[], weight=weight,
                error="no_valid_decision_points",
            )

        # Judgment-first buckets (Sam, 2026-06-02): steering EVERY decision
        # is the skill, so partial engagement scores LOW. Reaching a working
        # result without owning the decisions is not the target — owning them
        # is. A single dodge (decision handed back to the agent) is the
        # canonical lazy-delegation signal and floors the dimension.
        #   all resolved (commit/reframe)        → 9.5 excellent
        #   any dodge                            → 1.5 poor
        #   partial (some resolved, no dodge)    → 2.0 + (resolved/total)·5
        #     e.g. 1-of-2 owned → 4.5 (poor); both vague → 2.0 (poor)
        if n_dodge > 0:
            score = 1.5
            rating = "poor"
        elif n_resolved == n_total:
            score = 9.5
            rating = "excellent"
        else:
            frac = n_resolved / n_total if n_total else 0.0
            score = round(2.0 + frac * 5.0, 1)
            rating = "good" if frac >= 0.75 else "poor"

        reasoning = (
            f"Per-decision outcomes — {'; '.join(per_dp_lines)}. "
            f"{n_resolved}/{n_total} resolved (commit/reframe); "
            f"{n_dodge} dodge(s)."
        )
        # Cite which transcript turn first promoted each dp to its
        # final status — cheap evidence trail for the recruiter UI.
        evidence: List[str] = []
        per_dp_turn_marks: Dict[str, int] = {}
        for idx, record in enumerate(artifacts.prompt_transcript or []):
            if not isinstance(record, dict):
                continue
            per_dp = record.get("interrogation_state") or {}
            if not isinstance(per_dp, dict):
                continue
            for dp_id, payload in per_dp.items():
                if dp_id in per_dp_turn_marks:
                    continue
                status_here = ""
                if isinstance(payload, dict):
                    status_here = str(payload.get("status") or "").strip().lower()
                elif isinstance(payload, str):
                    status_here = payload.strip().lower()
                if status_here and status_here == state.get(dp_id):
                    per_dp_turn_marks[dp_id] = idx
        for dp_id, turn_idx in per_dp_turn_marks.items():
            evidence.append(f"decision={dp_id} reached '{state[dp_id]}' at transcript turn {turn_idx + 1}")
        evidence = evidence[:10]

        return DimensionGrade(
            dimension_id=dimension_id, score=score, rating=rating,
            reasoning=reasoning[:1000], evidence_citations=evidence,
            weight=weight,
        )

    def grade_dimension(
        self,
        dimension_id: str,
        criteria: Dict[str, str],
        artifacts: ScoringArtifacts,
        *,
        weight: float = 0.0,
        lens: Optional[str] = None,
    ) -> DimensionGrade:
        """Send one rubric dimension to Claude and return its graded result.

        ``lens`` selects the grader frame: ``"decision"`` punishes lazy
        delegation (judgment from the transcript), ``"deliverable"`` credits
        the shipped artifact regardless of who typed it. Defaults to the
        decision frame for back-compat with un-lensed dimensions.

        Never raises. On failure returns a ``DimensionGrade`` with
        ``score=0`` and ``error`` set so the aggregator can flag the gap
        for human review.
        """
        user_prompt = _build_user_prompt(dimension_id, criteria, artifacts)
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=_MAX_TOKENS_PER_DIMENSION,
                # temperature=0 makes the authoritative assessment score
                # reproducible: two candidates with the same evidence get the
                # same grade (NORTH_STAR principle 4). Without it the Anthropic
                # default (1.0) makes identical submissions score differently.
                temperature=0,
                system=_system_prompt_for_lens(lens),
                messages=[{"role": "user", "content": user_prompt}],
                metering=self._metering(dimension_id),
            )
            raw_text = response.content[0].text if response.content else ""
            payload = self._parse_grader_response(raw_text, dimension_id)
        except Exception as exc:
            logger.exception(
                "RubricScorer dimension=%s grading failed", dimension_id,
            )
            return DimensionGrade(
                dimension_id=dimension_id, score=0.0, rating="poor",
                reasoning="Grader call failed; flagged for human review.",
                evidence_citations=[], weight=weight, error=str(exc),
            )

        # Validate + clamp grader output
        try:
            raw_score = float(payload.get("score", 0))
        except (TypeError, ValueError):
            raw_score = 0.0
        score = max(0.0, min(10.0, raw_score))
        rating = str(payload.get("rating", "")).lower().strip() or "poor"
        if rating not in {"excellent", "good", "poor"}:
            rating = "poor"
        reasoning = str(payload.get("reasoning", "")).strip()[:1000]
        cites_raw = payload.get("evidence_citations") or []
        evidence = [str(c).strip()[:200] for c in cites_raw if str(c).strip()][:10]
        return DimensionGrade(
            dimension_id=dimension_id, score=score, rating=rating,
            reasoning=reasoning, evidence_citations=evidence, weight=weight,
        )

    def grade_rubric(
        self, rubric: Dict[str, Any], artifacts: ScoringArtifacts,
    ) -> RubricResult:
        """Grade every dimension in ``rubric`` and return aggregated result.

        ``rubric`` is the ``task.evaluation_rubric`` dict — ``{dim_id:
        {weight, criteria}}`` (per the canonical task-spec shape). Weights
        usually sum to 1.0 across dimensions; we normalize defensively
        in case a redesign lands with weights summing to e.g. 0.95.
        """
        graded: List[DimensionGrade] = []
        failed_ids: List[str] = []
        total_weight = 0.0
        for dim_id, dim_spec in (rubric or {}).items():
            if not isinstance(dim_spec, dict):
                continue
            weight = float(dim_spec.get("weight") or 0.0)
            grader_kind = str(dim_spec.get("grader") or "").strip()
            if grader_kind == "interrogation_outcome":
                # Deterministic, no Anthropic call. Reads decision_points
                # + per-turn interrogation_state from artifacts.
                grade = self.grade_dimension_via_interrogation_outcome(
                    dim_id, artifacts, weight=weight,
                )
            else:
                criteria = dim_spec.get("criteria") or {}
                if not isinstance(criteria, dict):
                    criteria = {}
                grade = self.grade_dimension(
                    dim_id, criteria, artifacts, weight=weight,
                    lens=dim_spec.get("lens"),
                )
            graded.append(grade)
            total_weight += weight
            if grade.error is not None:
                failed_ids.append(dim_id)

        # Weighted aggregate to 0-100. If total_weight is 0 (no dimensions
        # had weight) treat each as equal-weight so the score isn't NaN.
        if not graded:
            weighted = 0.0
        elif total_weight <= 0:
            weighted = (sum(g.score for g in graded) / len(graded)) * 10.0
        else:
            weighted_sum = sum(g.score * g.weight for g in graded)
            weighted = (weighted_sum / total_weight) * 10.0

        return RubricResult(
            dimensions=graded,
            weighted_score_100=round(weighted, 2),
            model_used=self._model,
            failed_dimension_ids=failed_ids,
        )


__all__ = [
    "ScoringArtifacts",
    "DimensionGrade",
    "RubricResult",
    "RubricScorer",
]
