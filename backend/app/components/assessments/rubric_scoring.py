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
    # Process-visible grading (now the only path): prompt_transcript_excerpt()
    # interleaves each turn's agent tool calls + results, and
    # git_evidence_excerpt() surfaces the committed diff — so the grader reads
    # HOW the candidate drove the agent (verification, iteration), not just the
    # message/response text. Defaults True (live). Retained as a knob ONLY so
    # scripts/shadow_rescore_assessments.py can force it off for a before/after
    # comparison; production never sets it. git_evidence is
    # assessment.git_evidence (head_sha, diff_main, commits, ...).
    include_process_trace: bool = True
    git_evidence: Dict[str, Any] = field(default_factory=dict)
    # Planted traps (wrong-but-plausible paths) for the DISCERNMENT lens to
    # check the candidate caught. Each: {id, planted, tell, where?}. Empty =
    # the task declared none. See interrogation.validate_traps (PR-9).
    traps: List[Dict[str, Any]] = field(default_factory=list)

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

    def traps_excerpt(self) -> str:
        """Planted traps for the grader to check the candidate caught. Empty
        unless the task declared traps."""
        if not self.traps:
            return ""
        lines: List[str] = []
        for trap in self.traps:
            if not isinstance(trap, dict):
                continue
            planted = str(trap.get("planted") or "").strip()
            if not planted:
                continue
            where = str(trap.get("where") or "").strip()
            tell = str(trap.get("tell") or "").strip()
            entry = f"- TRAP: {planted}" + (f" (where: {where})" if where else "")
            if tell:
                entry += f"\n  CAUGHT IF: {tell}"
            lines.append(entry)
        return "\n".join(lines)


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

# DISCERNMENT lens — did the candidate critically EVALUATE the agent's output
# rather than take it at face value? The "appropriate reliance" signal: catching
# a wrong/incomplete suggestion, rejecting a bad approach, noticing a green test
# run still hid a gap. Only has teeth because the process trace (PR-2) lets the
# grader see the agent's tool actions + results.
_DISCERNMENT_LENS_PROMPT = (
    _GRADER_PREAMBLE
    + "\n\nLENS: DISCERNMENT. You are grading whether the candidate critically "
    "EVALUATED the agent's work rather than accepting it at face value. Look in "
    "the transcript + the agent's tool actions/results for: catching a wrong, "
    "incomplete, or plausible-but-incorrect suggestion; rejecting or correcting "
    "a bad approach the agent proposed; noticing that a passing test run still "
    "left a real gap; pushing the agent to justify or re-check something "
    "dubious. A candidate who accepted whatever the agent produced without "
    "scrutiny scores POOR here even if the result happened to be correct — this "
    "lens measures judgment ABOUT the AI's output (trust good output, override "
    "bad), not the output itself."
    + _GRADER_OUTPUT
)

# DILIGENCE lens — did the candidate take RESPONSIBILITY for what shipped:
# verify before claiming done (run the tests, re-read the diff), own residual
# risk, no premature "all done". The "accountable for AI-produced work" signal.
_DILIGENCE_LENS_PROMPT = (
    _GRADER_PREAMBLE
    + "\n\nLENS: DILIGENCE. You are grading whether the candidate took "
    "RESPONSIBILITY for the work. Did they VERIFY before claiming success — run "
    "the tests, re-read the changed files, confirm the fix actually holds — "
    "rather than trusting the agent's word? Did they flag what remains risky or "
    "unfinished instead of declaring premature completion? The evidence is in "
    "the agent's tool actions (test runs, re-reads after edits) and the "
    "transcript. A candidate who declared it done with no verification, or "
    "shipped on the agent's say-so, scores POOR here even if it happened to work."
    + _GRADER_OUTPUT
)

# PRACTICE lens — did the candidate operate their AI environment the way a
# strong AI-native practitioner does: gave the agent genuinely useful context
# (a lean AGENTS.md/CLAUDE.md, source material, examples), planned/specified
# before building, authored reusable leverage (a skill/template/checklist),
# kept context clean, and verified — rather than treating the agent as a vague
# one-shot oracle. This lens grades the QUALITY of that craft (was it
# load-bearing?), NOT mere presence: a deterministic ``practice_outcome`` grader
# scores presence/structure and is capped at the "good" band; "excellent" lives
# here, and only when the practice demonstrably improved the work. Cargo-culting
# (an empty/box-ticked plan, a bloated AGENTS.md the agent would ignore) scores
# POOR even though the artifact exists. Maps to Anthropic's AI-Fluency
# Description/Delegation/Diligence depending on the dimension's ``fluency`` tag.
_PRACTICE_LENS_PROMPT = (
    _GRADER_PREAMBLE
    + "\n\nLENS: PRACTICE. You are grading the candidate's AI-NATIVE CRAFT — "
    "HOW they set up and operated the agent, visible in the transcript, their "
    "tool actions/results, the git diff, and the files they created (e.g. "
    "AGENTS.md/CLAUDE.md, PLAN.md, a reusable skill/template/checklist). Reward "
    "practice that was GENUINE and LOAD-BEARING: context that demonstrably "
    "improved the agent's direction, a plan that actually shaped the build, "
    "verification that caught or prevented a problem, a reusable asset that was "
    "coherent and used. Penalise CARGO-CULTING: a box-ticked plan the candidate "
    "ignored, a bloated or irrelevant context file (agents ignore over-long "
    "memory files), a 'reusable' asset that is noise. Mere presence of an "
    "artifact is at most GOOD; EXCELLENT requires the practice to have visibly "
    "improved the work. If the candidate gave vague one-liners with no setup and "
    "no verification, that is POOR even if the result happened to work."
    + _GRADER_OUTPUT
)


def _system_prompt_for_lens(lens: Optional[str]) -> str:
    key = (lens or "").strip().lower()
    if key == "deliverable":
        return _DELIVERABLE_LENS_PROMPT
    if key == "discernment":
        return _DISCERNMENT_LENS_PROMPT
    if key == "diligence":
        return _DILIGENCE_LENS_PROMPT
    if key == "practice":
        return _PRACTICE_LENS_PROMPT
    return _DECISION_LENS_PROMPT


# ---- 4-D fluency rollup (Anthropic AI Fluency framework) --------------------
#
# Delegation · Description · Discernment · Diligence (Anthropic's "4 Ds") + a
# Deliverable/outcome axis. Each graded rubric dimension rolls up to exactly one
# axis. A dimension may declare its axis explicitly via a ``fluency`` field;
# otherwise we derive it from its grader/lens so existing tasks roll up sensibly
# with zero spec churn. This view is purely DERIVED from the same dimension
# grades — it does NOT change the authoritative weighted score.
FLUENCY_AXES = ("delegation", "description", "discernment", "diligence", "deliverable")


def fluency_axis_for_dimension(spec: Dict[str, Any]) -> str:
    """Map one rubric-dimension spec to its 4-D fluency axis."""
    if not isinstance(spec, dict):
        return "delegation"
    explicit = str(spec.get("fluency") or "").strip().lower()
    if explicit in FLUENCY_AXES:
        return explicit
    if str(spec.get("grader") or "").strip().lower() == "interrogation_outcome":
        return "delegation"  # decision-ownership
    lens = str(spec.get("lens") or "").strip().lower()
    if lens == "deliverable":
        return "deliverable"
    if lens in ("discernment", "diligence", "description", "delegation"):
        return lens
    return "delegation"  # decision lens (or unset back-compat default)


def summarize_fluency_4d(
    evaluation_rubric: Optional[Dict[str, Any]],
    dimensions: List["DimensionGrade"],
) -> Dict[str, Optional[float]]:
    """Roll graded dimensions up to the 4-D fluency axes (0-100 per axis).

    Weighted mean of the dimension scores assigned to each axis, scaled to
    0-100. Axes with no contributing dimension are ``None`` (no signal). A
    dimension whose grader errored is skipped so a transient failure doesn't
    drag an axis to zero. Purely derived/additive — never the authoritative
    score.
    """
    rubric = evaluation_rubric if isinstance(evaluation_rubric, dict) else {}
    acc: Dict[str, List[tuple]] = {axis: [] for axis in FLUENCY_AXES}
    for dim in dimensions or []:
        if getattr(dim, "error", None):
            continue
        axis = fluency_axis_for_dimension(rubric.get(dim.dimension_id, {}))
        weight = float(getattr(dim, "weight", 0.0) or 0.0) or 1.0
        acc[axis].append((float(getattr(dim, "score", 0.0) or 0.0), weight))
    out: Dict[str, Optional[float]] = {}
    for axis, pairs in acc.items():
        if not pairs:
            out[axis] = None
            continue
        wsum = sum(w for _, w in pairs)
        score10 = (sum(s * w for s, w in pairs) / wsum) if wsum else 0.0
        out[axis] = round(score10 * 10.0, 1)
    return out


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
    traps_text = artifacts.traps_excerpt()
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
    if traps_text:
        sections.append(
            "Planted traps for this task — wrong-but-plausible paths the agent "
            "might lead the candidate down. For a DISCERNMENT dimension, judge "
            "whether the candidate CAUGHT and rejected these (strong signal) vs. "
            "accepted them uncritically (poor):\n"
            f"{traps_text}"
        )
    sections.append(
        "Final repository state (candidate's submitted code):\n"
        f"{artifacts.repo_files_excerpt()}"
    )
    sections.append("Grade this dimension now. Respond with the JSON shape only.")
    return "\n\n".join(sections)


# ---- Deterministic practice-proficiency detection ---------------------------
#
# AI-Native Practice Proficiency (see docs/AI_NATIVE_PRACTICES_ASSESSMENT_
# INTEGRATION.md): does the candidate set up + operate their AI environment like
# a strong practitioner — maintain a lean context file, plan/spec first, author
# reusable leverage, verify? The ``practice_outcome`` grader scores PRESENCE +
# STRUCTURE deterministically from already-captured artifacts (final repo files,
# the process trace), with NO Anthropic call. By design it is capped at the
# "good" band: the LLM ``practice`` lens is what awards "excellent" for craft
# that was genuinely load-bearing. A bloated context file scores POOR here — it
# is the documented anti-pattern (agents ignore over-long memory files).
_PRACTICE_BAND_STRONG = 7.5   # present + well-formed (capped at "good" — excellent is the LLM lens's job)
_PRACTICE_BAND_PARTIAL = 5.5  # present but thin/unchanged ("presence alone caps at good")
_PRACTICE_BAND_BLOAT = 4.0    # present but bloated/irrelevant — anti-pattern → poor
_PRACTICE_BAND_WEAK = 1.5     # absent

_CONTEXT_FILE_NAMES = ("agents.md", "claude.md")
_MAX_LEAN_CONTEXT_LINES = 200       # Anthropic memory guidance: keep it lean
_MIN_SUBSTANTIVE_LINES = 3
_PLAN_FILE_HINTS = ("plan.md", "design.md", "approach.md", "spec.md", "plan.txt")
_ASSET_FILE_HINTS = ("skill.md", ".skill.md", "checklist", "template", ".template")
# Distinctive verification markers in tool commands (kept specific so "latest"
# etc. don't false-positive on a bare "test").
_VERIFICATION_MARKERS = (
    "pytest", "unittest", "vitest", "jest", "npm test", "npm run test",
    "go test", "make test", "tox", "./run_tests", "run_tests", "cargo test",
)


def _nonempty_lines(text: str) -> List[str]:
    return [ln for ln in str(text or "").splitlines() if ln.strip()]


def _detect_context_file(repo_files: Dict[str, str]):
    """Return ``(present, lean, substantive, path)`` for an AGENTS.md/CLAUDE.md
    in the final repo (case-insensitive on the basename)."""
    for path, content in (repo_files or {}).items():
        base = str(path).rsplit("/", 1)[-1].lower()
        if base in _CONTEXT_FILE_NAMES:
            n = len(_nonempty_lines(content))
            return True, (n <= _MAX_LEAN_CONTEXT_LINES), (n >= _MIN_SUBSTANTIVE_LINES), path
    return False, False, False, None


def _detect_plan(repo_files: Dict[str, str], design_doc: str) -> Optional[str]:
    """Return the path of a substantive plan/spec/design artifact, or None."""
    if design_doc and len(_nonempty_lines(design_doc)) >= _MIN_SUBSTANTIVE_LINES:
        return "DESIGN.md"
    for path, content in (repo_files or {}).items():
        base = str(path).rsplit("/", 1)[-1].lower()
        if any(base == hint or base.endswith(hint) for hint in _PLAN_FILE_HINTS):
            if len(_nonempty_lines(content)) >= _MIN_SUBSTANTIVE_LINES:
                return path
    return None


def _detect_reusable_asset(repo_files: Dict[str, str]) -> Optional[str]:
    """Return the path of a reusable asset (skill/template/checklist), or None."""
    for path in (repo_files or {}):
        low = str(path).lower()
        if any(hint in low for hint in _ASSET_FILE_HINTS):
            return path
    return None


def _detect_verification(prompt_transcript: List[Dict[str, Any]]) -> bool:
    """True if the process trace shows the candidate ran tests/verification.

    Reads the per-turn ``tool_calls_made`` list (name + input) captured on each
    ai_prompts record. Only meaningful when the process trace was captured;
    tasks that adopt the ``verification`` probe run with the trace on.
    """
    for record in (prompt_transcript or []):
        if not isinstance(record, dict):
            continue
        calls = record.get("tool_calls_made") or record.get("tool_calls") or []
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name", ""))
            inp = call.get("input")
            cmd = ""
            if isinstance(inp, dict):
                cmd = str(inp.get("command") or inp.get("cmd") or "")
            blob = f"{name} {cmd}".lower()
            if any(marker in blob for marker in _VERIFICATION_MARKERS):
                return True
    return False


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

    def grade_dimension_via_practice_outcome(
        self,
        dimension_id: str,
        artifacts: ScoringArtifacts,
        *,
        weight: float = 0.0,
        probe: Optional[str] = None,
    ) -> DimensionGrade:
        """Deterministic grader for AI-native *practice proficiency* dims
        (``grader: "practice_outcome"``). No Anthropic call.

        Scores PRESENCE + STRUCTURE of practice artifacts from already-captured
        data (final repo files + the process trace). ``probe`` selects which
        practice to score:

        - ``context_setup``   — a lean, substantive AGENTS.md/CLAUDE.md
        - ``plan_first``      — a plan/spec/design artifact
        - ``reusable_asset``  — a skill/template/checklist
        - ``verification``    — tests/verification run during the session
        - (absent/unknown)    — composite mean of all four

        Capped at the "good" band by design: the LLM ``practice`` lens awards
        "excellent" for craft that was genuinely load-bearing. A bloated context
        file scores POOR (the documented anti-pattern). Mirrors the
        interrogation grader: pure-Python, reproducible, zero metering rows.
        """
        repo = artifacts.repo_files or {}
        present, lean, substantive, cpath = _detect_context_file(repo)
        if present and substantive and lean:
            ctx = (_PRACTICE_BAND_STRONG, f"maintains a lean, substantive context file ({cpath})", cpath)
        elif present and substantive and not lean:
            ctx = (_PRACTICE_BAND_BLOAT, f"context file {cpath} is bloated (>{_MAX_LEAN_CONTEXT_LINES} lines); agents tend to ignore over-long memory files", cpath)
        elif present:
            ctx = (_PRACTICE_BAND_PARTIAL, f"context file {cpath} present but thin/unchanged", cpath)
        else:
            ctx = (_PRACTICE_BAND_WEAK, "no AGENTS.md/CLAUDE.md context file maintained", None)

        plan_path = _detect_plan(repo, artifacts.design_doc)
        plan = (
            (_PRACTICE_BAND_STRONG, f"wrote a plan/spec artifact ({plan_path})", plan_path)
            if plan_path else (_PRACTICE_BAND_WEAK, "no plan/spec artifact captured", None)
        )
        asset_path = _detect_reusable_asset(repo)
        asset = (
            (_PRACTICE_BAND_STRONG, f"authored a reusable asset ({asset_path})", asset_path)
            if asset_path else (_PRACTICE_BAND_WEAK, "no reusable asset authored", None)
        )
        verified = _detect_verification(artifacts.prompt_transcript)
        verify = (
            (_PRACTICE_BAND_STRONG, "ran tests/verification during the session", None)
            if verified else (_PRACTICE_BAND_WEAK, "no test/verification run observed in the trace", None)
        )

        signals = {
            "context_setup": ctx,
            "plan_first": plan,
            "reusable_asset": asset,
            "verification": verify,
        }
        key = (probe or "").strip().lower()
        if key in signals:
            score, note, cite = signals[key]
            reasoning = note
            evidence = [c for c in [cite] if c]
        else:
            score = round(sum(s for s, _, _ in signals.values()) / len(signals), 1)
            reasoning = "; ".join(f"{k}: {n}" for k, (_, n, _) in signals.items())
            evidence = [c for _, _, c in signals.values() if c]
        rating = "excellent" if score >= 9 else ("good" if score >= 5 else "poor")
        return DimensionGrade(
            dimension_id=dimension_id, score=float(score), rating=rating,
            reasoning=reasoning[:1000], evidence_citations=evidence[:10], weight=weight,
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
            elif grader_kind == "practice_outcome":
                # Deterministic, no Anthropic call. Scores presence/structure of
                # AI-native practice artifacts (context file, plan, asset,
                # verification) from the final repo + process trace.
                grade = self.grade_dimension_via_practice_outcome(
                    dim_id, artifacts, weight=weight, probe=dim_spec.get("probe"),
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
