"""Glue layer between ``interview_support_service`` and the LLM-driven
tech-stage prompt.

Keeps the helpers (transcript selection, evidence-anchor formatting) +
the call-site wiring out of ``interview_support_service`` so that file
stays under the 500-LOC architecture gate. The deterministic fallbacks
remain inline in ``interview_support_service`` so the LLM is purely
additive.
"""

from __future__ import annotations

from typing import Any


_EVIDENCE_PREFIX_BY_SOURCE = {
    "cv": "CV § ",
    "transcript": "Screen § ",
    "recruiter": "Recruiter § ",
    "requirement": "Requirement § ",
}


def format_evidence_anchor(raw_q: dict[str, Any]) -> str | None:
    """Apply the per-source prefix (CV §, Screen §, etc.) to the LLM's
    raw evidence anchor so interviewers can trace each question back to
    its source.
    """
    anchor = str(raw_q.get("evidence_anchor") or "").strip()
    if not anchor:
        return None
    source = str(raw_q.get("evidence_source") or "").strip().lower()
    prefix = _EVIDENCE_PREFIX_BY_SOURCE.get(source, "")
    return f"{prefix}{anchor}" if prefix else anchor


def deterministic_tech_questions(
    missing_skills: list[str],
    screening_summary_text: str | None,
) -> list[dict[str, Any]]:
    """Fallback tech-stage questions used when the LLM call returns
    nothing. Pure string templating — no I/O. Returns the raw dicts the
    caller will wrap with ``_question`` for storage sanitisation.
    """
    out: list[dict[str, Any]] = []
    for skill in missing_skills[:3]:
        out.append(
            {
                "question": f"Walk through the most technically complex work you've done related to {skill.lower()}.",
                "why_this_matters": "This is a likely technical gap relative to the job requirements.",
                "evidence_anchor": skill,
                "positive_signals": [
                    "Deep implementation detail",
                    "Tradeoff reasoning",
                    "Debugging examples",
                ],
                "red_flags": [
                    "Only conceptual familiarity",
                    "No concrete decisions or outcomes",
                ],
                "follow_up_probe": "Ask about architecture choices, failure modes, and what they would optimize next.",
            }
        )
    summary = (screening_summary_text or "").strip()
    if summary:
        out.append(
            {
                "question": "Build on the first interview: what technical example best validates the strongest screening claim?",
                "why_this_matters": "Keeps the technical interview grounded in evidence already surfaced during screening.",
                "evidence_anchor": summary,
                "positive_signals": [
                    "Consistency with prior interview",
                    "Specific architecture detail",
                    "Honest tradeoffs",
                ],
                "red_flags": ["Inconsistent narrative", "Surface-level technical depth"],
                "follow_up_probe": "Ask which part they personally designed, debugged, and measured.",
            }
        )
    return out


# ``maybe_generate_tech_questions`` (per-candidate LLM tech-question
# generator) was removed 2026-05-22. Tech screening questions are now
# generated once per role and cached on the role
# (``role_tech_questions_service``), regenerated only on job-spec /
# criteria changes. The per-candidate path fired ~302 Anthropic calls/day
# for marginal benefit. Its exclusive helpers
# (``latest_screening_transcript_text``, ``_ordered_interviews_local``)
# were removed with it.
