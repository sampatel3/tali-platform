"""Candidate feedback and interview debrief generation helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.assessment import Assessment, AssessmentStatus


_DIMENSION_ALIASES = {
    "task_completion": "task_completion",
    "prompt_clarity": "prompt_clarity",
    "context_provision": "context_provision",
    "independence_efficiency": "independence_efficiency",
    "response_utilization": "response_utilization",
    "debugging_design": "debugging_design",
    "written_communication": "written_communication",
    "role_fit": "role_fit",
    # Legacy aliases seen in persisted payloads.
    "independence": "independence_efficiency",
    "utilization": "response_utilization",
    "communication": "written_communication",
    "approach": "debugging_design",
    "cv_match": "role_fit",
}

_DIMENSION_ORDER = [
    "task_completion",
    "prompt_clarity",
    "context_provision",
    "independence_efficiency",
    "response_utilization",
    "debugging_design",
    "written_communication",
    "role_fit",
]

_DIMENSION_META: dict[str, dict[str, str]] = {
    "task_completion": {
        "label": "Task Completion",
        "improvement_focus": "Execution consistency",
        "improvement_advice": "Break work into milestones and validate each with focused tests before moving on.",
        "interview_prompt": "Walk me through how you decide what to ship first when time is constrained.",
        "listen_for": "Clear prioritization, explicit tradeoffs, and evidence-driven execution decisions.",
    },
    "prompt_clarity": {
        "label": "Prompt Clarity",
        "improvement_focus": "Instruction precision",
        "improvement_advice": "State expected output format and constraints up front before requesting code.",
        "interview_prompt": "Show me how you rewrite a vague request into a clear technical instruction.",
        "listen_for": "Specific constraints, context framing, and clear acceptance criteria.",
    },
    "context_provision": {
        "label": "Context Provision",
        "improvement_focus": "System context coverage",
        "improvement_advice": "Add one sentence on where a change sits in the broader system before asking for implementation help.",
        "interview_prompt": "Describe a time you onboarded someone into a complex area. What context did you share first?",
        "listen_for": "Audience awareness and rationale for including the right context at the right time.",
    },
    "independence_efficiency": {
        "label": "Independence & Efficiency",
        "improvement_focus": "Escalation timing",
        "improvement_advice": "Time-box solo debugging and escalate earlier when hypotheses are not converging.",
        "interview_prompt": "Tell me about a time you asked for help later than you should have.",
        "listen_for": "Awareness of escalation triggers and efficient iteration loops.",
    },
    "response_utilization": {
        "label": "Response Utilization",
        "improvement_focus": "AI output synthesis",
        "improvement_advice": "Treat assistant output as draft material and explicitly adapt it to your codebase context.",
        "interview_prompt": "How do you decide whether to adopt, modify, or reject AI-generated suggestions?",
        "listen_for": "Critical evaluation, adaptation, and validation habits.",
    },
    "debugging_design": {
        "label": "Debugging & Design",
        "improvement_focus": "Hypothesis-driven debugging",
        "improvement_advice": "Write the hypothesis before each experiment so debugging steps stay structured.",
        "interview_prompt": "Walk me through a recent complex bug and the experiments you ran to isolate it.",
        "listen_for": "Structured hypotheses, measurable checks, and design tradeoff awareness.",
    },
    "written_communication": {
        "label": "Written Communication",
        "improvement_focus": "Technical communication depth",
        "improvement_advice": "Expand short task-focused messages with intent, tradeoffs, and expected impact.",
        "interview_prompt": "Describe the last technical document or RFC you wrote and how you calibrated detail level.",
        "listen_for": "Audience calibration, clarity, and completeness in technical writing.",
    },
    "role_fit": {
        "label": "Role Fit (CV ↔ Job)",
        "improvement_focus": "Role alignment narrative",
        "improvement_advice": "Connect concrete prior outcomes to role-specific expectations with measurable impact.",
        "interview_prompt": "What part of your prior experience best maps to this role, and where is your biggest growth area?",
        "listen_for": "Concrete examples, honest gap assessment, and evidence of ramp plans.",
    },
}

_CONTEXT_HINT_WORDS = {
    "context",
    "architecture",
    "service",
    "module",
    "caller",
    "interface",
    "constraint",
    "because",
    "system",
    "integration",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _status_value(assessment: Assessment) -> str:
    raw = getattr(assessment.status, "value", assessment.status)
    return str(raw or "").lower()


def _is_completed(assessment: Assessment) -> bool:
    return _status_value(assessment) in {
        AssessmentStatus.COMPLETED.value,
        AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT.value,
    }


def _score_100(assessment: Assessment) -> float | None:
    final_score = getattr(assessment, "final_score", None)
    if isinstance(final_score, (int, float)):
        return float(final_score)
    score = getattr(assessment, "score", None)
    if isinstance(score, (int, float)):
        return float(score) * 10.0
    return None


def _score_10(assessment: Assessment) -> float | None:
    score = getattr(assessment, "score", None)
    if isinstance(score, (int, float)):
        return float(score)
    score_100 = _score_100(assessment)
    return None if score_100 is None else float(score_100 / 10.0)


def _extract_category_scores(assessment: Assessment) -> dict[str, float]:
    breakdown = assessment.score_breakdown if isinstance(assessment.score_breakdown, dict) else {}
    analytics = assessment.prompt_analytics if isinstance(assessment.prompt_analytics, dict) else {}

    raw_scores = (
        (breakdown.get("category_scores") if isinstance(breakdown.get("category_scores"), dict) else None)
        or (analytics.get("category_scores") if isinstance(analytics.get("category_scores"), dict) else None)
        or (
            analytics.get("detailed_scores", {}).get("category_scores")
            if isinstance(analytics.get("detailed_scores"), dict)
            and isinstance(analytics.get("detailed_scores", {}).get("category_scores"), dict)
            else None
        )
        or {}
    )

    out: dict[str, float] = {}
    for key, value in raw_scores.items():
        canonical = _DIMENSION_ALIASES.get(str(key))
        if not canonical or not isinstance(value, (int, float)):
            continue
        out[canonical] = round(float(value), 2)
    return out


def _percentile_rank(values: list[float], target: float) -> float:
    if not values:
        return 0.0
    count = sum(1 for value in values if value <= target)
    return round((count / len(values)) * 100.0, 1)


def _benchmark_payload(db: Session, assessment: Assessment, scores: dict[str, float]) -> dict[str, Any]:
    completed = (
        db.query(Assessment)
        .filter(
            Assessment.organization_id == assessment.organization_id,
            Assessment.task_id == assessment.task_id,
            Assessment.status.in_(
                [AssessmentStatus.COMPLETED, AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT]
            ),
        )
        .all()
    )
    sample_size = len(completed)
    payload: dict[str, Any] = {
        "available": sample_size >= 20,
        "sample_size": sample_size,
        "message": (
            "Benchmark coming soon"
            if sample_size < 20
            else f"Compared against {sample_size} completed assessments on this task"
        ),
    }
    if sample_size < 20:
        return payload

    overall_distribution = [
        score
        for score in (_score_100(item) for item in completed)
        if isinstance(score, (int, float))
    ]
    candidate_overall = _score_100(assessment)
    if candidate_overall is not None and overall_distribution:
        payload["overall_percentile"] = _percentile_rank(overall_distribution, candidate_overall)

    dimension_distributions: dict[str, list[float]] = {key: [] for key in _DIMENSION_ORDER}
    for item in completed:
        item_scores = _extract_category_scores(item)
        for key, value in item_scores.items():
            dimension_distributions.setdefault(key, []).append(float(value))

    dimension_percentiles: dict[str, float] = {}
    for key, value in scores.items():
        distribution = dimension_distributions.get(key) or []
        if distribution:
            dimension_percentiles[key] = _percentile_rank(distribution, value)
    payload["dimension_percentiles"] = dimension_percentiles
    return payload


def _top_or_bottom_label(percentile: float | None) -> str | None:
    if percentile is None:
        return None
    if percentile >= 50:
        top = max(1, int(round(100 - percentile)))
        return f"Top {top}%"
    bottom = max(1, int(round(100 - percentile)))
    return f"Bottom {bottom}%"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _has_context_signal(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(token in lowered for token in _CONTEXT_HINT_WORDS)


def _prompt_stats(prompts: list[dict[str, Any]]) -> dict[str, int]:
    total = len(prompts)
    with_context = 0
    without_context = 0
    short_prompts = 0
    for prompt in prompts:
        message = str(prompt.get("message") or "")
        words = [item for item in message.split() if item]
        if len(words) <= 8:
            short_prompts += 1
        if _has_context_signal(message):
            with_context += 1
        else:
            without_context += 1
    return {
        "total": total,
        "with_context": with_context,
        "without_context": without_context,
        "short_prompts": short_prompts,
    }


def _minute_marker(assessment: Assessment, prompt_timestamp: str | None, index: int) -> int:
    started_at = assessment.started_at
    prompt_dt = _parse_iso(prompt_timestamp)
    if started_at and prompt_dt:
        started = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
        delta_minutes = int(max(0, (prompt_dt - started).total_seconds()) // 60)
        return delta_minutes
    return max(1, (index + 1) * 2)


def _strongest_moment(assessment: Assessment, prompts: list[dict[str, Any]], scores: dict[str, float]) -> dict[str, Any]:
    if not prompts:
        focus_dimension = max(scores.items(), key=lambda item: item[1])[0] if scores else "prompt_clarity"
        label = _DIMENSION_META.get(focus_dimension, {}).get("label", "Prompt Clarity")
        return {
            "minute": 0,
            "prompt": "Prompt transcript unavailable for this assessment.",
            "reason": f"Strongest measurable signal came from {label}.",
            "dimension": focus_dimension,
            "score_hint": round(scores.get(focus_dimension, 0.0), 1) if focus_dimension in scores else None,
        }

    ranked: list[tuple[float, int, dict[str, Any]]] = []
    for idx, prompt in enumerate(prompts):
        message = str(prompt.get("message") or "")
        if not message.strip():
            continue
        words = [item for item in message.split() if item]
        quality = 0.0
        if len(words) >= 10:
            quality += 2.0
        if _has_context_signal(message):
            quality += 2.5
        if ":" in message or "->" in message:
            quality += 1.0
        if "do not" in message.lower() or "must" in message.lower():
            quality += 1.0
        quality += min(2.0, len(words) / 25.0)
        ranked.append((quality, idx, prompt))

    if not ranked:
        prompt = prompts[0]
        return {
            "minute": _minute_marker(assessment, prompt.get("timestamp"), 0),
            "prompt": str(prompt.get("message") or "")[:280],
            "reason": "This was the clearest available interaction in the transcript.",
            "dimension": "prompt_clarity",
            "score_hint": round(scores.get("prompt_clarity", 0.0), 1) if "prompt_clarity" in scores else None,
        }

    ranked.sort(key=lambda item: item[0], reverse=True)
    _, idx, best_prompt = ranked[0]
    best_text = str(best_prompt.get("message") or "").strip()
    minute = _minute_marker(assessment, best_prompt.get("timestamp"), idx)
    return {
        "minute": minute,
        "prompt": best_text[:520],
        "reason": (
            "This prompt explicitly includes context, constraints, and expected output format, "
            "which strongly improves assistant reliability."
        ),
        "dimension": "prompt_clarity",
        "score_hint": round(scores.get("prompt_clarity", 0.0), 1) if "prompt_clarity" in scores else None,
    }


def _style_archetype(scores: dict[str, float]) -> dict[str, str]:
    clarity = scores.get("prompt_clarity", 0.0)
    context = scores.get("context_provision", 0.0)
    independence = scores.get("independence_efficiency", 0.0)
    utilization = scores.get("response_utilization", 0.0)
    debugging = scores.get("debugging_design", 0.0)

    if clarity >= 7.0 and independence >= 7.0 and debugging >= 6.5:
        return {
            "key": "methodical_prompter",
            "label": "The Methodical Prompter",
            "description": "You structure requests carefully, execute in focused loops, and validate outputs with discipline.",
        }
    if context >= 7.0 and utilization >= 7.0:
        return {
            "key": "contextual_collaborator",
            "label": "The Contextual Collaborator",
            "description": "You give strong system framing and actively adapt AI output to practical implementation needs.",
        }
    if independence >= 8.0 and context < 6.0:
        return {
            "key": "autonomous_executor",
            "label": "The Autonomous Executor",
            "description": "You drive independently and move quickly, with upside from adding broader context earlier.",
        }
    return {
        "key": "iterative_explorer",
        "label": "The Iterative Explorer",
        "description": "You iterate frequently and learn quickly; consistency improves further when prompts are more structured.",
    }


def _strengths(scores: dict[str, float]) -> list[dict[str, Any]]:
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    out: list[dict[str, Any]] = []
    for key, score in ranked[:3]:
        meta = _DIMENSION_META.get(key, {})
        out.append(
            {
                "dimension_id": key,
                "dimension": meta.get("label", key),
                "score": round(score, 1),
                "validation_prompt": meta.get("interview_prompt", "Ask for a concrete example from recent work."),
            }
        )
    return out


def _improvements(scores: dict[str, float], stats: dict[str, int]) -> list[dict[str, Any]]:
    ranked = sorted(scores.items(), key=lambda item: item[1])
    out: list[dict[str, Any]] = []
    for key, score in ranked[:3]:
        meta = _DIMENSION_META.get(key, {})
        evidence = ""
        if key == "context_provision" and stats.get("total", 0) > 0:
            evidence = (
                f"{stats.get('without_context', 0)} of {stats.get('total', 0)} prompts did not include enough system context."
            )
        elif key == "prompt_clarity" and stats.get("total", 0) > 0:
            evidence = (
                f"{stats.get('short_prompts', 0)} prompts were short and likely under-specified for complex requests."
            )
        out.append(
            {
                "dimension_id": key,
                "dimension": meta.get("label", key),
                "score": round(score, 1),
                "focus": meta.get("improvement_focus", "Collaboration quality"),
                "evidence": evidence,
                "practice_advice": meta.get(
                    "improvement_advice",
                    "Add explicit context and expected outcomes before asking for implementation help.",
                ),
            }
        )
    return out


def _safe_overall_score(assessment: Assessment, scores: dict[str, float]) -> float:
    overall = _score_10(assessment)
    if isinstance(overall, (int, float)):
        return round(float(overall), 1)
    if scores:
        return round(sum(scores.values()) / max(1, len(scores)), 1)
    return 0.0


def build_candidate_feedback_payload(
    db: Session,
    assessment: Assessment,
    *,
    organization_name: str,
) -> dict[str, Any]:
    prompts = assessment.ai_prompts if isinstance(assessment.ai_prompts, list) else []
    scores = _extract_category_scores(assessment)
    stats = _prompt_stats(prompts)
    benchmark = _benchmark_payload(db, assessment, scores)
    strongest = _strongest_moment(assessment, prompts, scores)
    overall_score = _safe_overall_score(assessment, scores)

    dimensions: list[dict[str, Any]] = []
    dimension_percentiles = benchmark.get("dimension_percentiles", {}) if isinstance(benchmark, dict) else {}
    for key in _DIMENSION_ORDER:
        score = scores.get(key)
        if score is None:
            continue
        percentile = dimension_percentiles.get(key)
        dimensions.append(
            {
                "id": key,
                "label": _DIMENSION_META.get(key, {}).get("label", key),
                "score": round(score, 1),
                "percentile": percentile,
                "percentile_label": _top_or_bottom_label(percentile),
            }
        )

    overall_percentile = benchmark.get("overall_percentile") if isinstance(benchmark, dict) else None
    return {
        "version": 1,
        "generated_at": _utcnow().isoformat(),
        "organization_name": organization_name,
        "task_name": assessment.task.name if assessment.task else "Assessment",
        "role_name": assessment.role.name if getattr(assessment, "role", None) else None,
        "overall_score": overall_score,
        "overall_percentile": overall_percentile,
        "overall_percentile_label": _top_or_bottom_label(overall_percentile),
        "benchmark": benchmark,
        "dimensions": dimensions,
        "strongest_moment": strongest,
        "improvements": _improvements(scores, stats),
        "strengths": _strengths(scores),
        "style": _style_archetype(scores),
        "prompt_stats": stats,
    }


def build_feedback_text_report(payload: dict[str, Any]) -> str:
    lines = [
        "TAALI Candidate Feedback Report",
        "===============================",
        f"Company: {payload.get('organization_name') or 'Unknown'}",
        f"Task: {payload.get('task_name') or 'Assessment'}",
        f"Role: {payload.get('role_name') or 'N/A'}",
        "",
        f"Overall Score: {payload.get('overall_score', 'N/A')}/10",
    ]
    if payload.get("overall_percentile_label"):
        lines.append(f"Benchmark: {payload.get('overall_percentile_label')}")
    else:
        lines.append("Benchmark: Benchmark coming soon")

    lines.extend(["", "Dimension Breakdown", "-------------------"])
    for dimension in payload.get("dimensions", []):
        lines.append(
            f"- {dimension.get('label')}: {dimension.get('score')}/10"
            + (
                f" ({dimension.get('percentile_label')})"
                if dimension.get("percentile_label")
                else ""
            )
        )

    strongest = payload.get("strongest_moment") or {}
    lines.extend(
        [
            "",
            "Strongest Moment",
            "---------------",
            f"Minute {strongest.get('minute', 0)}",
            str(strongest.get("prompt") or ""),
            str(strongest.get("reason") or ""),
        ]
    )

    lines.extend(["", "Improvement Opportunities", "-------------------------"])
    for item in payload.get("improvements", []):
        lines.append(f"- {item.get('dimension')} ({item.get('score')}/10)")
        if item.get("evidence"):
            lines.append(f"  Evidence: {item.get('evidence')}")
        lines.append(f"  Practice: {item.get('practice_advice')}")

    style = payload.get("style") or {}
    lines.extend(
        [
            "",
            "AI Collaboration Style",
            "----------------------",
            f"{style.get('label', 'Unknown style')}",
            str(style.get("description") or ""),
        ]
    )
    return "\n".join(lines).strip()


def build_plain_text_pdf(body_text: str) -> bytes:
    escaped = (
        str(body_text or "")
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )
    stream = f"BT /F1 11 Tf 50 780 Td ({escaped.replace(chr(10), ') Tj T* (')}) Tj ET"
    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
    )
    content = stream.encode("latin-1", errors="ignore")
    pdf += (
        f"5 0 obj << /Length {len(content)} >> stream\n".encode("ascii")
        + content
        + b"\nendstream endobj\n"
    )
    xref_pos = len(pdf)
    xref = (
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000241 00000 n \n"
        b"0000000311 00000 n \n"
    )
    trailer = f"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode("ascii")
    return pdf + xref + trailer


def _role_context_text(assessment: Assessment) -> str:
    role_name = assessment.role.name if getattr(assessment, "role", None) else None
    task_name = assessment.task.name if assessment.task else "assessment task"
    if role_name:
        return f"{role_name} role for {task_name}"
    return task_name


def _debrief_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Interview Guide - {payload.get('candidate_name', 'Candidate')}",
        "",
        payload.get("summary", ""),
        "",
        "## Probing Questions",
    ]
    for item in payload.get("probing_questions", []):
        lines.extend(
            [
                f"### {item.get('dimension')} ({item.get('score')}/10)",
                item.get("pattern", ""),
                f"- Question: {item.get('question')}",
                f"- What to listen for: {item.get('what_to_listen_for')}",
                "",
            ]
        )

    lines.append("## Strengths To Validate")
    for item in payload.get("strengths_to_validate", []):
        lines.append(f"- {item.get('text')}")

    lines.extend(["", "## Red Flags To Follow Up"])
    for item in payload.get("red_flags", []):
        lines.append(f"- {item.get('text')}")
        if item.get("follow_up_question"):
            lines.append(f"  - Follow-up: {item.get('follow_up_question')}")

    return "\n".join(lines).strip()


def build_interview_debrief_payload(assessment: Assessment) -> dict[str, Any]:
    prompts = assessment.ai_prompts if isinstance(assessment.ai_prompts, list) else []
    transcript_available = bool(prompts)
    scores = _extract_category_scores(assessment)
    role_context = _role_context_text(assessment)
    stats = _prompt_stats(prompts)
    task = assessment.task
    rubric = task.evaluation_rubric if task and isinstance(task.evaluation_rubric, dict) else {}
    rubric_items = [(key, value) for key, value in rubric.items() if isinstance(value, dict)]
    extra_data = task.extra_data if task and isinstance(task.extra_data, dict) else {}
    interviewer_signals = extra_data.get("interviewer_signals") if isinstance(extra_data.get("interviewer_signals"), dict) else {}
    strong_positive_signals = interviewer_signals.get("strong_positive") if isinstance(interviewer_signals.get("strong_positive"), list) else []
    rubric_red_flag_signals = interviewer_signals.get("red_flags") if isinstance(interviewer_signals.get("red_flags"), list) else []

    ranked = sorted(scores.items(), key=lambda item: item[1])
    weakest = ranked[:5] if ranked else [(key, 5.0) for key in _DIMENSION_ORDER[:3]]
    probing_questions: list[dict[str, Any]] = []
    for idx, (key, score) in enumerate(weakest[: max(3, min(5, len(weakest)))]):
        meta = _DIMENSION_META.get(key, {})
        rubric_key = None
        rubric_details = None
        if rubric_items:
            rubric_key, rubric_details = rubric_items[idx % len(rubric_items)]
        rubric_criteria = rubric_details.get("criteria") if isinstance(rubric_details, dict) else {}
        pattern = (
            f"Observed score trend in {meta.get('label', key)} was {round(score, 1)}/10."
            if transcript_available
            else f"Transcript unavailable; generated from {meta.get('label', key)} score profile."
        )
        if key == "context_provision" and stats.get("total", 0):
            pattern = (
                f"{stats.get('without_context', 0)} of {stats.get('total', 0)} prompts had limited system context."
            )
        if isinstance(rubric_criteria, dict) and rubric_criteria.get("poor"):
            pattern = f"{pattern} Rubric concern: {rubric_criteria.get('poor')}"

        question = meta.get("interview_prompt", "Share a concrete example from a recent project.")
        question = f"{question} ({role_context})"
        probing_questions.append(
            {
                "dimension_id": key,
                "dimension": meta.get("label", key),
                "score": round(score, 1),
                "pattern": pattern,
                "question": question,
                "what_to_listen_for": meta.get(
                    "listen_for",
                    "Specific examples, clear reasoning, and awareness of tradeoffs.",
                ),
                "rubric_dimension": rubric_key,
                "rubric_weight": round(float(rubric_details.get("weight", 0.0) or 0.0), 3) if isinstance(rubric_details, dict) else None,
            }
        )

    strengths: list[dict[str, Any]] = []
    for key, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:3]:
        label = _DIMENSION_META.get(key, {}).get("label", key)
        strengths.append(
            {
                "dimension_id": key,
                "score": round(score, 1),
                "text": f"{label} was strong ({round(score, 1)}/10); validate with a concrete example from recent work.",
            }
        )
    for signal in strong_positive_signals[:2]:
        strengths.append(
            {
                "dimension_id": "task_rubric_signal",
                "score": None,
                "text": f"Task signal: {signal}",
            }
        )

    red_flags: list[dict[str, Any]] = []
    for key, score in ranked:
        if score > 5.0 or len(red_flags) >= 2:
            continue
        label = _DIMENSION_META.get(key, {}).get("label", key)
        follow_up = _DIMENSION_META.get(key, {}).get("interview_prompt", "Ask for a concrete example.")
        red_flags.append(
            {
                "dimension_id": key,
                "score": round(score, 1),
                "text": f"{label} may require deeper validation ({round(score, 1)}/10).",
                "follow_up_question": follow_up,
            }
        )
    for signal in rubric_red_flag_signals:
        if len(red_flags) >= 4:
            break
        red_flags.append(
            {
                "dimension_id": "task_rubric_signal",
                "score": None,
                "text": f"Task red flag: {signal}",
                "follow_up_question": "Ask the candidate for specific evidence from this assessment session.",
            }
        )

    candidate_name = (
        (assessment.candidate.full_name if assessment.candidate else None)
        or (assessment.candidate.email if assessment.candidate else None)
        or "Candidate"
    )
    summary = (
        f"Generated from TAALI assessment behavior for {candidate_name}. "
        f"Focus this interview on the highest-risk collaboration patterns first, then validate task-specific rubric signals."
    )
    payload = {
        "version": 1,
        "generated_at": _utcnow().isoformat(),
        "candidate_name": candidate_name,
        "task_name": assessment.task.name if assessment.task else "Assessment",
        "role_name": assessment.role.name if getattr(assessment, "role", None) else None,
        "summary": summary,
        "transcript_available": transcript_available,
        "source": "heuristic_v1",
        "estimated_read_time_min": 3,
        "probing_questions": probing_questions,
        "strengths_to_validate": strengths,
        "red_flags": red_flags,
    }
    payload["markdown"] = _debrief_markdown(payload)
    return payload
