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
            lines.append(f"### Turn {i}\n[Candidate]: {user}\n[Claude]: {asst}")
        return "\n\n".join(lines)


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

_SYSTEM_PROMPT = (
    "You are grading a candidate's technical assessment against a specific "
    "rubric criterion. You will receive: (1) the criterion's excellent / "
    "good / poor descriptors, (2) the relevant submission artifacts (final "
    "code, design doc, chat transcript), (3) the task scenario the "
    "candidate was given. "
    "\n\n"
    "Your job: choose ONE rating tier ('excellent', 'good', or 'poor'), "
    "assign a 0-10 score, write a 1-3 sentence reasoning, and list specific "
    "evidence citations (e.g. 'dq/gate.py line 23', 'transcript turn 4'). "
    "Score 9-10 for excellent, 5-8 for good, 0-4 for poor. "
    "\n\n"
    "Be specific and evidence-based. A candidate who delegated the entire "
    "task to the model with 'fix it' prompts should not score 'excellent' "
    "on any design or judgment dimension regardless of code outcome — the "
    "rubric measures the candidate's reasoning, not the model's. "
    "\n\n"
    "Respond ONLY with valid JSON, no markdown formatting, matching:\n"
    '{"score": <0-10>, "rating": "<excellent|good|poor>", '
    '"reasoning": "<1-3 sentences>", '
    '"evidence_citations": ["<citation>", ...]}'
)


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
    return (
        f"Rubric dimension: **{dimension_id}**\n\n"
        "Tier descriptors:\n"
        f"- EXCELLENT (9-10): {excellent}\n"
        f"- GOOD (5-8): {good}\n"
        f"- POOR (0-4): {poor}\n\n"
        f"Candidate role: {role}\n\n"
        "Task scenario the candidate was given:\n"
        f"{scenario}\n\n"
        "Test runner outcome:\n"
        f"{artifacts.test_results_summary or '(not provided)'}\n\n"
        "Candidate's DESIGN.md (if any):\n"
        f"{artifacts.design_doc_excerpt()}\n\n"
        "Candidate's chat transcript with Claude:\n"
        f"{artifacts.prompt_transcript_excerpt()}\n\n"
        "Final repository state (candidate's submitted code):\n"
        f"{artifacts.repo_files_excerpt()}\n\n"
        "Grade this dimension now. Respond with the JSON shape only."
    )


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
        meta: Dict[str, Any] = {
            "feature": "assessment",
            "sub_feature": "rubric_scoring",
            "organization_id": self._organization_id,
            "dimension": dimension_id,
        }
        if self._assessment_id is not None:
            meta["entity_id"] = f"assessment:{self._assessment_id}"
            meta["assessment_id"] = str(self._assessment_id)
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
        - all decisions in {commit, reframe}            → excellent (score=9.5)
        - ≥50% in {commit, reframe}, no dodges           → good   (score=6.5)
        - otherwise (all vague/unaddressed, no dodges)   → poor   (score=3.0)

        Why fixed scores: the rubric's job is signal differentiation,
        not a continuous score. Two consultants both committing
        substantively to every decision should both land at "excellent"
        — variance there is noise. The thresholds live in one place;
        change them centrally if calibration data demands it.

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

        if n_dodge > 0:
            score = 2.0
            rating = "poor"
        elif n_resolved == n_total:
            score = 9.5
            rating = "excellent"
        elif n_resolved * 2 >= n_total:
            score = 6.5
            rating = "good"
        else:
            score = 3.0
            rating = "poor"

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
    ) -> DimensionGrade:
        """Send one rubric dimension to Claude and return its graded result.

        Never raises. On failure returns a ``DimensionGrade`` with
        ``score=0`` and ``error`` set so the aggregator can flag the gap
        for human review.
        """
        user_prompt = _build_user_prompt(dimension_id, criteria, artifacts)
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=_MAX_TOKENS_PER_DIMENSION,
                system=_SYSTEM_PROMPT,
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
