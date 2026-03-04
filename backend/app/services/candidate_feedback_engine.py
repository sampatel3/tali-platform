"""Candidate feedback and interview debrief generation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import textwrap
from typing import Any
import unicodedata

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models.assessment import Assessment, AssessmentStatus
from .taali_scoring import compute_role_fit_score, compute_taali_score


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

_PDF_PAGE_WIDTH = 612
_PDF_PAGE_HEIGHT = 792
_PDF_LEFT_MARGIN = 54
_PDF_TOP_MARGIN = 750
_PDF_BOTTOM_MARGIN = 54
_PDF_BODY_WRAP = 92
_A4_PAGE_WIDTH = 595
_A4_PAGE_HEIGHT = 842
_PDF_BRAND_PURPLE = "#9D00FF"
_PDF_BRAND_PURPLE_SOFT = "#F3E9FF"
_PDF_BORDER_SOFT = "#D8D5E8"
_PDF_TEXT = "#171B2D"
_PDF_MUTED = "#667085"
_PDF_CARD_BG = "#FFFFFF"


@dataclass(frozen=True)
class _PdfLine:
    text: str
    font: str = "F1"
    size: int = 11
    leading: int = 14


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


def _completed_assessment_query_filter():
    return and_(
        Assessment.completed_at.isnot(None),
        Assessment.is_voided.is_(False),
        or_(
            Assessment.status == AssessmentStatus.COMPLETED,
            Assessment.completed_due_to_timeout.is_(True),
        ),
    )


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


def _normalize_score_100(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if 0.0 <= numeric <= 10.0:
        numeric *= 10.0
    return round(max(0.0, min(100.0, numeric)), 1)


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
            _completed_assessment_query_filter(),
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


def _pdf_escape(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    safe = normalized.encode("latin-1", "ignore").decode("latin-1")
    return safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap_pdf_text(text: str, width: int) -> list[str]:
    raw = str(text or "").rstrip()
    if not raw:
        return [""]

    stripped = raw.lstrip()
    indent = raw[: len(raw) - len(stripped)]
    bullet_prefix = ""
    body = stripped
    subsequent_indent = indent
    if stripped.startswith("- "):
        bullet_prefix = f"{indent}- "
        body = stripped[2:]
        subsequent_indent = f"{indent}  "
    elif stripped.startswith("* "):
        bullet_prefix = f"{indent}* "
        body = stripped[2:]
        subsequent_indent = f"{indent}  "

    wrapped = textwrap.wrap(
        body,
        width=width,
        initial_indent=bullet_prefix or indent,
        subsequent_indent=subsequent_indent,
        break_long_words=False,
        break_on_hyphens=False,
    )
    return wrapped or [bullet_prefix or indent]


def _append_wrapped_pdf_lines(
    output: list[_PdfLine],
    text: str,
    *,
    font: str = "F1",
    size: int = 11,
    leading: int = 14,
    width: int = _PDF_BODY_WRAP,
) -> None:
    for raw_line in str(text or "").splitlines() or [""]:
        for wrapped in _wrap_pdf_text(raw_line, width):
            output.append(_PdfLine(text=wrapped, font=font, size=size, leading=leading))


def _paginate_pdf_lines(lines: list[_PdfLine]) -> list[list[tuple[float, _PdfLine]]]:
    return _paginate_pdf_lines_with_bounds(
        lines,
        top_baseline=_PDF_TOP_MARGIN,
        bottom_margin=_PDF_BOTTOM_MARGIN,
    )


def _paginate_pdf_lines_with_bounds(
    lines: list[_PdfLine],
    *,
    top_baseline: float,
    bottom_margin: float,
) -> list[list[tuple[float, _PdfLine]]]:
    pages: list[list[tuple[float, _PdfLine]]] = []
    current_page: list[tuple[float, _PdfLine]] = []
    y = top_baseline

    for line in lines:
        if y - line.leading < bottom_margin and current_page:
            pages.append(current_page)
            current_page = []
            y = top_baseline
        current_page.append((y, line))
        y -= line.leading

    if current_page or not pages:
        pages.append(current_page)
    return pages


def _build_pdf_from_page_streams(page_streams: list[bytes]) -> bytes:
    return _build_pdf_with_dimensions(page_streams, page_width=_PDF_PAGE_WIDTH, page_height=_PDF_PAGE_HEIGHT)


def _build_pdf_with_dimensions(
    page_streams: list[bytes],
    *,
    page_width: int,
    page_height: int,
) -> bytes:
    page_count = max(1, len(page_streams))
    font_regular_obj = 3
    font_bold_obj = 4
    next_obj_num = 5
    page_obj_nums: list[int] = []
    content_obj_nums: list[int] = []
    for _ in range(page_count):
        page_obj_nums.append(next_obj_num)
        content_obj_nums.append(next_obj_num + 1)
        next_obj_num += 2

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0] * next_obj_num

    def emit(obj_num: int, payload: bytes) -> None:
        offsets[obj_num] = len(pdf)
        pdf.extend(f"{obj_num} 0 obj\n".encode("ascii"))
        pdf.extend(payload)
        pdf.extend(b"\nendobj\n")

    emit(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{obj_num} 0 R" for obj_num in page_obj_nums)
    emit(2, f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode("ascii"))
    emit(font_regular_obj, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    emit(font_bold_obj, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

    for page_obj_num, content_obj_num, content in zip(page_obj_nums, content_obj_nums, page_streams, strict=False):
        emit(
            page_obj_num,
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
                f"/Resources << /Font << /F1 {font_regular_obj} 0 R /F2 {font_bold_obj} 0 R >> >> "
                f"/Contents {content_obj_num} 0 R >>"
            ).encode("ascii"),
        )
        emit(
            content_obj_num,
            f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"\nendstream",
        )

    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {next_obj_num}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for obj_num in range(1, next_obj_num):
        pdf.extend(f"{offsets[obj_num]:010d} 00000 n \n".encode("ascii"))
    pdf.extend(f"trailer << /Size {next_obj_num} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode("ascii"))
    return bytes(pdf)


def build_wrapped_text_pdf(
    body_text: str,
    *,
    title: str | None = None,
    subtitle: str | None = None,
) -> bytes:
    lines: list[_PdfLine] = []
    if title:
        _append_wrapped_pdf_lines(lines, title, font="F2", size=18, leading=24, width=52)
    if subtitle:
        _append_wrapped_pdf_lines(lines, subtitle, font="F1", size=11, leading=16, width=82)
    if title or subtitle:
        lines.append(_PdfLine(text="", leading=12))
    _append_wrapped_pdf_lines(lines, body_text, font="F1", size=11, leading=14, width=_PDF_BODY_WRAP)

    page_layouts = _paginate_pdf_lines(lines)
    page_streams: list[bytes] = []
    for page_index, page in enumerate(page_layouts, start=1):
        ops: list[str] = []
        for y, line in page:
            if not line.text:
                continue
            ops.append(
                f"BT /{line.font} {line.size} Tf {_PDF_LEFT_MARGIN} {y:.1f} Td ({_pdf_escape(line.text)}) Tj ET"
            )
        footer = f"Page {page_index} of {len(page_layouts)}"
        ops.append(f"BT /F1 9 Tf {_PDF_LEFT_MARGIN} 28 Td ({_pdf_escape(footer)}) Tj ET")
        page_streams.append("\n".join(ops).encode("latin-1", "ignore"))

    return _build_pdf_from_page_streams(page_streams)


def build_plain_text_pdf(body_text: str) -> bytes:
    return build_wrapped_text_pdf(body_text)


def _rgb_components(hex_color: str, fallback: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    raw = str(hex_color or "").strip().lstrip("#")
    if len(raw) != 6:
        return fallback
    try:
        return tuple(round(int(raw[index : index + 2], 16) / 255.0, 4) for index in (0, 2, 4))
    except ValueError:
        return fallback


def _pdf_color_ops(hex_color: str, *, fill: bool = True) -> str:
    r, g, b = _rgb_components(hex_color)
    operator = "rg" if fill else "RG"
    return f"{r} {g} {b} {operator}"


def _pdf_rect_top(
    x: float,
    top: float,
    width: float,
    height: float,
    *,
    fill_color: str | None = None,
    stroke_color: str | None = None,
    line_width: float = 1.0,
) -> str:
    y = _A4_PAGE_HEIGHT - top - height
    ops: list[str] = []
    if fill_color:
        ops.append(_pdf_color_ops(fill_color, fill=True))
    if stroke_color:
        ops.append(_pdf_color_ops(stroke_color, fill=False))
        ops.append(f"{line_width} w")
    paint = "B" if fill_color and stroke_color else "f" if fill_color else "S"
    ops.append(f"{x:.1f} {y:.1f} {width:.1f} {height:.1f} re {paint}")
    return "\n".join(ops)


def _estimate_wrap_width(width: float, font_size: int) -> int:
    return max(12, int(width / max(font_size * 0.54, 1.0)))


def _truncate_pdf_line(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    trimmed = text[: max_chars - 1].rstrip(" ,.;:-")
    return f"{trimmed}…"


def _wrapped_pdf_lines_for_width(
    text: str,
    *,
    width: float,
    font_size: int,
    max_lines: int | None = None,
) -> list[str]:
    lines: list[str] = []
    wrap_width = _estimate_wrap_width(width, font_size)
    for raw_line in str(text or "").splitlines() or [""]:
        lines.extend(_wrap_pdf_text(raw_line, wrap_width))
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _truncate_pdf_line(lines[-1], max(8, wrap_width - 2))
    return lines or [""]


def _pdf_text_block_ops(
    text: str,
    *,
    x: float,
    top: float,
    width: float,
    font: str = "F1",
    size: int = 11,
    leading: int = 14,
    color: str = _PDF_TEXT,
    max_lines: int | None = None,
) -> tuple[list[str], float]:
    ops: list[str] = []
    lines = _wrapped_pdf_lines_for_width(text, width=width, font_size=size, max_lines=max_lines)
    for index, line in enumerate(lines):
        baseline_y = _A4_PAGE_HEIGHT - top - (index * leading) - size
        ops.append(
            " ".join(
                [
                    "BT",
                    f"/{font}",
                    f"{size}",
                    "Tf",
                    _pdf_color_ops(color, fill=True),
                    "1 0 0 1",
                    f"{x:.1f}",
                    f"{baseline_y:.1f}",
                    "Tm",
                    f"({_pdf_escape(line)})",
                    "Tj ET",
                ]
            )
        )
    return ops, top + (len(lines) * leading)


def _pdf_metric_card_ops(
    *,
    x: float,
    top: float,
    width: float,
    height: float,
    label: str,
    value: float | None,
) -> list[str]:
    ops = [
        _pdf_rect_top(x, top, width, height, fill_color=_PDF_CARD_BG, stroke_color=_PDF_BORDER_SOFT),
    ]
    label_ops, _ = _pdf_text_block_ops(
        label.upper(),
        x=x + 14,
        top=top + 14,
        width=width - 28,
        font="F2",
        size=9,
        leading=11,
        color=_PDF_MUTED,
        max_lines=1,
    )
    value_ops, _ = _pdf_text_block_ops(
        "—" if value is None else f"{value:.1f}",
        x=x + 14,
        top=top + 34,
        width=width - 28,
        font="F2",
        size=22,
        leading=24,
        color=_PDF_TEXT,
        max_lines=1,
    )
    ops.extend(label_ops)
    ops.extend(value_ops)
    return ops


def _unique_text_items(items: list[str], max_items: int = 4) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        text = str(raw or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= max_items:
            break
    return out


def _client_report_probe_items(payload: dict[str, Any]) -> list[str]:
    interview_focus = payload.get("interview_focus") if isinstance(payload.get("interview_focus"), list) else []
    requirement_gap = payload.get("first_requirement_gap") if isinstance(payload.get("first_requirement_gap"), dict) else {}
    items = []
    if requirement_gap.get("requirement"):
        evidence = str(requirement_gap.get("evidence") or requirement_gap.get("impact") or "").strip()
        items.append(
            f"{requirement_gap.get('requirement')}: {evidence or 'Validate whether the candidate can close this gap quickly.'}"
        )
    for item in interview_focus[:3]:
        dimension = str(item.get("dimension") or item.get("focus") or "Interview focus").strip()
        summary = str(item.get("evidence") or item.get("practice_advice") or item.get("focus") or "").strip()
        if dimension and summary:
            items.append(f"{dimension}: {summary}")
    return _unique_text_items(items, 3)


def _client_report_gap_items(payload: dict[str, Any]) -> list[str]:
    concerns = payload.get("concerns") if isinstance(payload.get("concerns"), list) else []
    missing_skills = payload.get("missing_skills") if isinstance(payload.get("missing_skills"), list) else []
    requirements = payload.get("requirements_assessment") if isinstance(payload.get("requirements_assessment"), list) else []
    requirement_gaps = [
        str(item.get("requirement") or "").strip()
        for item in requirements
        if str(item.get("status") or "").lower() not in {"", "met"}
    ]
    items = [*(f"Skill gap: {item}" for item in missing_skills), *concerns, *requirement_gaps]
    return _unique_text_items(items, 4)


def _client_report_assessment_key_points(payload: dict[str, Any]) -> list[str]:
    strengths = payload.get("strengths") if isinstance(payload.get("strengths"), list) else []
    dimension_scores = payload.get("dimension_scores") if isinstance(payload.get("dimension_scores"), list) else []
    prompt_count = payload.get("prompt_count")
    tests_passed = payload.get("tests_passed")
    tests_total = payload.get("tests_total")
    benchmark_label = str(payload.get("benchmark_label") or "").strip()
    integrity_note = str(payload.get("integrity_note") or "").strip()

    items: list[str] = []
    top_strengths = [str(item.get("dimension") or "").strip() for item in strengths[:3] if str(item.get("dimension") or "").strip()]
    if top_strengths:
        items.append(f"Strongest signals: {', '.join(top_strengths)}")

    top_dimensions = [
        f"{str(item.get('label') or '').strip()} {item.get('score')}/10"
        for item in dimension_scores[:4]
        if str(item.get("label") or "").strip() and item.get("score") is not None
    ]
    if top_dimensions:
        items.append(f"Highest rubric scores: {', '.join(top_dimensions)}")

    if isinstance(tests_total, (int, float)) and tests_total:
        items.append(f"Test completion evidence: {int(tests_passed or 0)}/{int(tests_total)} tests passed")
    if isinstance(prompt_count, (int, float)) and prompt_count:
        items.append(f"AI interactions captured: {int(prompt_count)}")
    if benchmark_label:
        items.append(f"Benchmark position: {benchmark_label}")
    if integrity_note and not integrity_note.lower().startswith("no integrity modifiers"):
        items.append(integrity_note)

    return _unique_text_items(items, 5)


def _client_report_cv_page_streams(payload: dict[str, Any]) -> list[bytes]:
    candidate_name = str(payload.get("candidate_name") or "Candidate")
    role_name = str(payload.get("role_name") or "Role")
    cv_filename = str(payload.get("cv_filename") or "").strip()
    cv_text = str(payload.get("cv_text") or "").strip() or "Candidate CV text is not available for this assessment."

    lines: list[_PdfLine] = []
    _append_wrapped_pdf_lines(lines, cv_text, font="F1", size=10, leading=14, width=82)
    page_layouts = _paginate_pdf_lines_with_bounds(
        lines,
        top_baseline=616,
        bottom_margin=64,
    )

    page_streams: list[bytes] = []
    total_pages = max(1, len(page_layouts))
    for page_index, page in enumerate(page_layouts, start=1):
        ops: list[str] = []
        ops.append(_pdf_rect_top(0, 0, _A4_PAGE_WIDTH, _A4_PAGE_HEIGHT, fill_color="#FFFFFF"))
        ops.append(_pdf_rect_top(0, 0, _A4_PAGE_WIDTH, 72, fill_color=_PDF_BRAND_PURPLE))

        header_left, _ = _pdf_text_block_ops(
            "TAALI",
            x=40,
            top=18,
            width=120,
            font="F2",
            size=22,
            leading=24,
            color="#FFFFFF",
            max_lines=1,
        )
        header_title, _ = _pdf_text_block_ops(
            "Candidate CV" if page_index == 1 else "Candidate CV (continued)",
            x=40,
            top=44,
            width=220,
            font="F1",
            size=11,
            leading=13,
            color="#FFFFFF",
            max_lines=1,
        )
        header_right, _ = _pdf_text_block_ops(
            f"Page {page_index + 1}",
            x=470,
            top=30,
            width=85,
            font="F2",
            size=10,
            leading=12,
            color="#FFFFFF",
            max_lines=1,
        )
        ops.extend(header_left)
        ops.extend(header_title)
        ops.extend(header_right)

        name_ops, _ = _pdf_text_block_ops(
            candidate_name,
            x=40,
            top=96,
            width=320,
            font="F2",
            size=20,
            leading=24,
            color=_PDF_TEXT,
            max_lines=1,
        )
        meta_ops, _ = _pdf_text_block_ops(
            " | ".join(
                item for item in [
                    role_name and f"Role: {role_name}",
                    cv_filename and f"CV: {cv_filename}",
                ]
                if item
            ),
            x=40,
            top=124,
            width=420,
            font="F1",
            size=10,
            leading=12,
            color=_PDF_MUTED,
            max_lines=1,
        )
        ops.extend(name_ops)
        ops.extend(meta_ops)
        ops.append(_pdf_rect_top(40, 152, 515, 1, fill_color=_PDF_BORDER_SOFT))

        for y, line in page:
            if not line.text:
                continue
            ops.append(
                " ".join(
                    [
                        "BT",
                        f"/{line.font}",
                        f"{line.size}",
                        "Tf",
                        _pdf_color_ops(_PDF_TEXT, fill=True),
                        "1 0 0 1",
                        "40",
                        f"{y:.1f}",
                        "Tm",
                        f"({_pdf_escape(line.text)})",
                        "Tj ET",
                    ]
                )
            )

        footer_ops, _ = _pdf_text_block_ops(
            "TAALI | Candidate CV appendix",
            x=40,
            top=806,
            width=240,
            font="F2",
            size=9,
            leading=11,
            color=_PDF_MUTED,
            max_lines=1,
        )
        ops.extend(footer_ops)
        page_streams.append("\n".join(ops).encode("latin-1", "ignore"))

    return page_streams


def build_client_assessment_summary_pdf(payload: dict[str, Any]) -> bytes:
    scores = payload.get("scores") if isinstance(payload.get("scores"), dict) else {}
    candidate_name = str(payload.get("candidate_name") or "Candidate")
    role_name = str(payload.get("role_name") or "Role")
    task_name = str(payload.get("task_name") or "Assessment")
    organization_name = str(payload.get("organization_name") or "Employer")
    recommendation = str(payload.get("recommendation") or "Pending")
    summary_text = str(payload.get("executive_summary") or "Assessment summary unavailable.").strip()
    role_fit_summary = str(payload.get("role_fit_summary") or "Role-fit evidence is available for employer review.").strip()
    matching_skills = _unique_text_items(payload.get("matching_skills") or [], 4)
    gap_items = _client_report_gap_items(payload)
    assessment_key_points = _client_report_assessment_key_points(payload)
    completed_at = str(payload.get("completed_at") or "").strip()
    generated_at = str(payload.get("generated_at") or "").strip()
    duration_label = str(payload.get("duration_label") or "").strip()
    benchmark_label = str(payload.get("benchmark_label") or "").strip()

    ops: list[str] = []
    ops.append(_pdf_rect_top(0, 0, _A4_PAGE_WIDTH, _A4_PAGE_HEIGHT, fill_color="#FFFFFF"))
    ops.append(_pdf_rect_top(0, 0, _A4_PAGE_WIDTH, 88, fill_color=_PDF_BRAND_PURPLE))

    header_left, _ = _pdf_text_block_ops(
        "TAALI",
        x=40,
        top=22,
        width=120,
        font="F2",
        size=24,
        leading=26,
        color="#FFFFFF",
        max_lines=1,
    )
    header_title, _ = _pdf_text_block_ops(
        "Client Assessment Summary",
        x=40,
        top=50,
        width=260,
        font="F1",
        size=11,
        leading=13,
        color="#FFFFFF",
        max_lines=1,
    )
    header_right, _ = _pdf_text_block_ops(
        f"Prepared for {organization_name}",
        x=330,
        top=28,
        width=225,
        font="F2",
        size=11,
        leading=13,
        color="#FFFFFF",
        max_lines=1,
    )
    header_meta, _ = _pdf_text_block_ops(
        "Employer-facing summary for shortlist review",
        x=330,
        top=47,
        width=225,
        font="F1",
        size=10,
        leading=12,
        color="#FFFFFF",
        max_lines=1,
    )
    ops.extend(header_left)
    ops.extend(header_title)
    ops.extend(header_right)
    ops.extend(header_meta)

    name_ops, _ = _pdf_text_block_ops(
        candidate_name,
        x=40,
        top=116,
        width=340,
        font="F2",
        size=24,
        leading=28,
        color=_PDF_TEXT,
        max_lines=1,
    )
    meta_text = " | ".join(
        item for item in [
            role_name and f"Role: {role_name}",
            task_name and f"Assessment: {task_name}",
            duration_label and f"Duration: {duration_label}",
        ]
        if item
    )
    secondary_meta = " | ".join(
        item for item in [
            completed_at and f"Completed: {completed_at[:10]}",
            benchmark_label and f"Benchmark: {benchmark_label}",
        ]
        if item
    )
    meta_ops, _ = _pdf_text_block_ops(
        meta_text,
        x=40,
        top=148,
        width=360,
        font="F1",
        size=10,
        leading=12,
        color=_PDF_MUTED,
        max_lines=1,
    )
    meta_secondary_ops, _ = _pdf_text_block_ops(
        secondary_meta,
        x=40,
        top=164,
        width=360,
        font="F1",
        size=10,
        leading=12,
        color=_PDF_MUTED,
        max_lines=1,
    )
    ops.extend(name_ops)
    ops.extend(meta_ops)
    ops.extend(meta_secondary_ops)

    summary_card_top = 196
    summary_card_height = 208
    summary_card_width = 324
    score_card_x = 382
    score_card_width = 173
    score_card_height = 208

    ops.append(_pdf_rect_top(40, summary_card_top, summary_card_width, summary_card_height, fill_color=_PDF_CARD_BG, stroke_color=_PDF_BORDER_SOFT))
    summary_label_ops, _ = _pdf_text_block_ops(
        "Assessment summary",
        x=56,
        top=summary_card_top + 16,
        width=180,
        font="F2",
        size=9,
        leading=11,
        color=_PDF_MUTED,
        max_lines=1,
    )
    summary_reco_ops, _ = _pdf_text_block_ops(
        recommendation.upper(),
        x=250,
        top=summary_card_top + 16,
        width=94,
        font="F2",
        size=10,
        leading=12,
        color=_PDF_BRAND_PURPLE,
        max_lines=1,
    )
    summary_body_ops, _ = _pdf_text_block_ops(
        summary_text,
        x=56,
        top=summary_card_top + 40,
        width=summary_card_width - 32,
        font="F1",
        size=11,
        leading=16,
        color=_PDF_TEXT,
        max_lines=9,
    )
    ops.extend(summary_label_ops)
    ops.extend(summary_reco_ops)
    ops.extend(summary_body_ops)

    ops.append(_pdf_rect_top(score_card_x, summary_card_top, score_card_width, score_card_height, fill_color=_PDF_BRAND_PURPLE_SOFT, stroke_color=_PDF_BORDER_SOFT))
    score_label_ops, _ = _pdf_text_block_ops(
        "TAALI score",
        x=score_card_x + 16,
        top=summary_card_top + 16,
        width=score_card_width - 32,
        font="F2",
        size=9,
        leading=11,
        color=_PDF_MUTED,
        max_lines=1,
    )
    score_value_ops, _ = _pdf_text_block_ops(
        "—" if scores.get("taali_score") is None else f"{float(scores['taali_score']):.1f}",
        x=score_card_x + 16,
        top=summary_card_top + 40,
        width=score_card_width - 32,
        font="F2",
        size=34,
        leading=36,
        color=_PDF_TEXT,
        max_lines=1,
    )
    score_rec_ops, _ = _pdf_text_block_ops(
        recommendation,
        x=score_card_x + 16,
        top=summary_card_top + 88,
        width=score_card_width - 32,
        font="F1",
        size=11,
        leading=13,
        color=_PDF_TEXT,
        max_lines=1,
    )
    ops.extend(score_label_ops)
    ops.extend(score_value_ops)
    ops.extend(score_rec_ops)
    ops.extend(
        _pdf_metric_card_ops(
            x=score_card_x + 16,
            top=summary_card_top + 140,
            width=68,
            height=48,
            label="Role fit",
            value=scores.get("role_fit_score"),
        )
    )
    ops.extend(
        _pdf_metric_card_ops(
            x=score_card_x + 89,
            top=summary_card_top + 140,
            width=68,
            height=48,
            label="Assessment",
            value=scores.get("assessment_score"),
        )
    )

    lower_top = 432
    lower_height = 320
    lower_width = 249
    gap = 18

    ops.append(_pdf_rect_top(40, lower_top, lower_width, lower_height, fill_color=_PDF_CARD_BG, stroke_color=_PDF_BORDER_SOFT))
    role_fit_label_ops, _ = _pdf_text_block_ops(
        "Role fit summary",
        x=56,
        top=lower_top + 16,
        width=lower_width - 32,
        font="F2",
        size=9,
        leading=11,
        color=_PDF_MUTED,
        max_lines=1,
    )
    role_fit_body_ops, role_fit_body_bottom = _pdf_text_block_ops(
        role_fit_summary,
        x=56,
        top=lower_top + 40,
        width=lower_width - 32,
        font="F1",
        size=11,
        leading=15,
        color=_PDF_TEXT,
        max_lines=6,
    )
    role_fit_skills_label, _ = _pdf_text_block_ops(
        "Matching skills",
        x=56,
        top=role_fit_body_bottom + 12,
        width=lower_width - 32,
        font="F2",
        size=9,
        leading=11,
        color=_PDF_MUTED,
        max_lines=1,
    )
    role_fit_skills_body, role_fit_skills_bottom = _pdf_text_block_ops(
        ", ".join(matching_skills) if matching_skills else "No matching skills were extracted for the client-facing summary.",
        x=56,
        top=role_fit_body_bottom + 30,
        width=lower_width - 32,
        font="F1",
        size=11,
        leading=15,
        color=_PDF_TEXT,
        max_lines=4,
    )
    role_fit_gaps_label, _ = _pdf_text_block_ops(
        "Main gaps",
        x=56,
        top=role_fit_skills_bottom + 14,
        width=lower_width - 32,
        font="F2",
        size=9,
        leading=11,
        color=_PDF_MUTED,
        max_lines=1,
    )
    role_fit_gaps_body, _ = _pdf_text_block_ops(
        "\n".join(f"- {item}" for item in gap_items) if gap_items else "No material role-fit gaps were surfaced in the exported summary.",
        x=56,
        top=role_fit_skills_bottom + 32,
        width=lower_width - 32,
        font="F1",
        size=10,
        leading=14,
        color=_PDF_TEXT,
        max_lines=6,
    )
    ops.extend(role_fit_label_ops)
    ops.extend(role_fit_body_ops)
    ops.extend(role_fit_skills_label)
    ops.extend(role_fit_skills_body)
    ops.extend(role_fit_gaps_label)
    ops.extend(role_fit_gaps_body)

    summary_points_x = 40 + lower_width + gap
    ops.append(_pdf_rect_top(summary_points_x, lower_top, lower_width, lower_height, fill_color=_PDF_CARD_BG, stroke_color=_PDF_BORDER_SOFT))
    summary_points_label_ops, _ = _pdf_text_block_ops(
        "Assessment key points",
        x=summary_points_x + 16,
        top=lower_top + 16,
        width=lower_width - 32,
        font="F2",
        size=9,
        leading=11,
        color=_PDF_MUTED,
        max_lines=1,
    )
    summary_points_body_ops, _ = _pdf_text_block_ops(
        "\n".join(f"- {item}" for item in assessment_key_points)
        if assessment_key_points
        else "- Summary key points were not available for this assessment export.",
        x=summary_points_x + 16,
        top=lower_top + 40,
        width=lower_width - 32,
        font="F1",
        size=10,
        leading=14,
        color=_PDF_TEXT,
        max_lines=12,
    )
    ops.extend(summary_points_label_ops)
    ops.extend(summary_points_body_ops)

    footer_top = 776
    ops.append(_pdf_rect_top(40, footer_top, 515, 1, fill_color=_PDF_BORDER_SOFT))
    footer_text = f"Generated {generated_at[:19].replace('T', ' ')}" if generated_at else "Generated by TAALI"
    footer_left_ops, _ = _pdf_text_block_ops(
        footer_text,
        x=40,
        top=footer_top + 8,
        width=260,
        font="F1",
        size=9,
        leading=11,
        color=_PDF_MUTED,
        max_lines=1,
    )
    footer_right_ops, _ = _pdf_text_block_ops(
        "TAALI | AI-native engineering assessment",
        x=320,
        top=footer_top + 8,
        width=235,
        font="F2",
        size=9,
        leading=11,
        color=_PDF_MUTED,
        max_lines=1,
    )
    ops.extend(footer_left_ops)
    ops.extend(footer_right_ops)

    return _build_pdf_with_dimensions(
        [
            "\n".join(ops).encode("latin-1", "ignore"),
            *_client_report_cv_page_streams(payload),
        ],
        page_width=_A4_PAGE_WIDTH,
        page_height=_A4_PAGE_HEIGHT,
    )


def _assessment_score_components_100(assessment: Assessment) -> dict[str, float | None]:
    breakdown = assessment.score_breakdown if isinstance(assessment.score_breakdown, dict) else {}
    score_components = breakdown.get("score_components") if isinstance(breakdown.get("score_components"), dict) else {}
    role_fit_components = (
        score_components.get("role_fit_components")
        if isinstance(score_components.get("role_fit_components"), dict)
        else {}
    )
    details = assessment.cv_job_match_details if isinstance(assessment.cv_job_match_details, dict) else {}

    cv_fit_score = _normalize_score_100(
        score_components.get("cv_fit_score")
        if score_components.get("cv_fit_score") is not None
        else role_fit_components.get("cv_fit_score")
        if role_fit_components.get("cv_fit_score") is not None
        else assessment.cv_job_match_score
    )
    requirements_fit_score = _normalize_score_100(
        score_components.get("requirements_fit_score")
        if score_components.get("requirements_fit_score") is not None
        else role_fit_components.get("requirements_fit_score")
        if role_fit_components.get("requirements_fit_score") is not None
        else details.get("requirements_match_score_100")
    )
    role_fit_score = _normalize_score_100(
        score_components.get("role_fit_score")
        if score_components.get("role_fit_score") is not None
        else details.get("role_fit_score_100")
        if details.get("role_fit_score_100") is not None
        else compute_role_fit_score(cv_fit_score, requirements_fit_score)
    )
    assessment_score = _normalize_score_100(
        score_components.get("assessment_score")
        if score_components.get("assessment_score") is not None
        else getattr(assessment, "assessment_score", None)
        if getattr(assessment, "assessment_score", None) is not None
        else getattr(assessment, "final_score", None)
        if getattr(assessment, "final_score", None) is not None
        else getattr(assessment, "score", None)
    )
    taali_score = _normalize_score_100(
        score_components.get("taali_score")
        if score_components.get("taali_score") is not None
        else getattr(assessment, "taali_score", None)
        if getattr(assessment, "taali_score", None) is not None
        else compute_taali_score(assessment_score, role_fit_score)
    )

    return {
        "assessment_score": assessment_score,
        "taali_score": taali_score,
        "role_fit_score": role_fit_score,
        "cv_fit_score": cv_fit_score,
        "requirements_fit_score": requirements_fit_score,
    }


def _recommendation_label(score_100: float | None) -> str:
    if score_100 is None:
        return "Pending"
    if score_100 >= 80:
        return "Strong Hire"
    if score_100 >= 65:
        return "Hire"
    if score_100 >= 50:
        return "Consider"
    return "No Hire"


def _format_duration_label(total_seconds: Any) -> str | None:
    if not isinstance(total_seconds, (int, float)) or total_seconds <= 0:
        return None
    total_seconds = int(total_seconds)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if seconds:
        return f"{minutes}m {seconds}s"
    return f"{minutes}m"


def _client_report_integrity_note(assessment: Assessment, score_breakdown: dict[str, Any]) -> str:
    applied_caps = score_breakdown.get("applied_caps") if isinstance(score_breakdown.get("applied_caps"), list) else []
    fraud_flags = assessment.prompt_fraud_flags if isinstance(assessment.prompt_fraud_flags, list) else []
    if "severe_unprofessional_language" in applied_caps:
        return "Score was capped because severe unprofessional language was detected in the assessment transcript."
    if "fraud" in applied_caps or fraud_flags:
        return "Integrity modifiers were applied and this assessment should be reviewed alongside the flagged prompt evidence."
    return "No integrity modifiers were applied to the exported score."


def build_client_assessment_report_payload(
    db: Session,
    assessment: Assessment,
    *,
    organization_name: str,
) -> dict[str, Any]:
    prompts = assessment.ai_prompts if isinstance(assessment.ai_prompts, list) else []
    scores = _extract_category_scores(assessment)
    stats = _prompt_stats(prompts)
    benchmark = _benchmark_payload(db, assessment, scores)
    strongest = _strengths(scores)
    improvements = _improvements(scores, stats)
    score_breakdown = assessment.score_breakdown if isinstance(assessment.score_breakdown, dict) else {}
    score_components = _assessment_score_components_100(assessment)
    details = assessment.cv_job_match_details if isinstance(assessment.cv_job_match_details, dict) else {}
    requirements = details.get("requirements_assessment") if isinstance(details.get("requirements_assessment"), list) else []
    first_requirement_gap = next(
        (
            item for item in requirements
            if str(item.get("status") or "").lower() not in {"", "met"}
        ),
        None,
    )

    benchmark_label = _top_or_bottom_label(benchmark.get("overall_percentile")) if isinstance(benchmark, dict) else None
    strengths_summary = ", ".join(item.get("dimension", "") for item in strongest[:2] if item.get("dimension"))
    executive_summary_parts = [
        f"Recommendation: {_recommendation_label(score_components['taali_score'])}.",
        str(details.get("summary") or "").strip(),
        (
            f"Strongest assessment signals were {strengths_summary}."
            if strengths_summary
            else ""
        ),
        (
            f"Primary interview focus should be {first_requirement_gap.get('requirement')}."
            if isinstance(first_requirement_gap, dict) and first_requirement_gap.get("requirement")
            else (
                f"Primary interview focus should be {improvements[0].get('dimension')}."
                if improvements
                else ""
            )
        ),
    ]

    return {
        "generated_at": _utcnow().isoformat(),
        "organization_name": organization_name,
        "candidate_name": (
            (assessment.candidate.full_name if getattr(assessment, "candidate", None) else None)
            or (assessment.candidate.email if getattr(assessment, "candidate", None) else None)
            or "Candidate"
        ),
        "task_name": assessment.task.name if assessment.task else "Assessment",
        "role_name": assessment.role.name if getattr(assessment, "role", None) else None,
        "completed_at": assessment.completed_at.isoformat() if assessment.completed_at else None,
        "duration_label": _format_duration_label(getattr(assessment, "total_duration_seconds", None)),
        "prompt_count": getattr(assessment, "total_prompts", None),
        "tests_passed": getattr(assessment, "tests_passed", None),
        "tests_total": getattr(assessment, "tests_total", None),
        "benchmark_label": benchmark_label,
        "recommendation": _recommendation_label(score_components["taali_score"]),
        "scores": score_components,
        "executive_summary": " ".join(part for part in executive_summary_parts if part).strip(),
        "dimension_scores": [
            {
                "label": _DIMENSION_META.get(key, {}).get("label", key),
                "score": round(value, 1),
            }
            for key, value in sorted(scores.items(), key=lambda item: item[1], reverse=True)
        ],
        "strengths": strongest,
        "interview_focus": improvements,
        "role_fit_summary": str(details.get("summary") or "").strip() or None,
        "matching_skills": [str(item).strip() for item in details.get("matching_skills", []) if str(item).strip()],
        "missing_skills": [str(item).strip() for item in details.get("missing_skills", []) if str(item).strip()],
        "experience_highlights": [str(item).strip() for item in details.get("experience_highlights", []) if str(item).strip()],
        "concerns": [str(item).strip() for item in details.get("concerns", []) if str(item).strip()],
        "requirements_coverage": details.get("requirements_coverage") if isinstance(details.get("requirements_coverage"), dict) else {},
        "requirements_assessment": requirements,
        "first_requirement_gap": first_requirement_gap,
        "integrity_note": _client_report_integrity_note(assessment, score_breakdown),
        "score_formula_version": score_breakdown.get("score_formula_version"),
        "cv_filename": (
            (assessment.application.cv_filename if getattr(assessment, "application", None) and assessment.application.cv_filename else None)
            or (assessment.candidate.cv_filename if getattr(assessment, "candidate", None) and assessment.candidate.cv_filename else None)
            or getattr(assessment, "cv_filename", None)
        ),
        "cv_text": (
            (assessment.application.cv_text if getattr(assessment, "application", None) and assessment.application.cv_text else None)
            or (assessment.candidate.cv_text if getattr(assessment, "candidate", None) and assessment.candidate.cv_text else None)
            or ""
        ),
    }


def build_client_assessment_report_text(payload: dict[str, Any]) -> str:
    scores = payload.get("scores") if isinstance(payload.get("scores"), dict) else {}
    strengths = payload.get("strengths") if isinstance(payload.get("strengths"), list) else []
    interview_focus = payload.get("interview_focus") if isinstance(payload.get("interview_focus"), list) else []
    dimension_scores = payload.get("dimension_scores") if isinstance(payload.get("dimension_scores"), list) else []
    matching_skills = payload.get("matching_skills") if isinstance(payload.get("matching_skills"), list) else []
    experience_highlights = payload.get("experience_highlights") if isinstance(payload.get("experience_highlights"), list) else []
    concerns = payload.get("concerns") if isinstance(payload.get("concerns"), list) else []
    requirements_coverage = payload.get("requirements_coverage") if isinstance(payload.get("requirements_coverage"), dict) else {}
    requirements_assessment = payload.get("requirements_assessment") if isinstance(payload.get("requirements_assessment"), list) else []

    def score_text(key: str) -> str:
        value = scores.get(key)
        return f"{value}/100" if value is not None else "N/A"

    lines = [
        "Prepared for employer / client review",
        f"Prepared for: {payload.get('organization_name') or 'Employer'}",
        f"Generated: {payload.get('generated_at') or 'N/A'}",
        f"Candidate: {payload.get('candidate_name') or 'Candidate'}",
        f"Role: {payload.get('role_name') or 'N/A'}",
        f"Assessment: {payload.get('task_name') or 'Assessment'}",
        "",
        "Executive Summary",
        "-----------------",
        str(payload.get("executive_summary") or "TAALI assessment evidence is available for review.").strip(),
        "",
        "Score Snapshot",
        "--------------",
        f"- Recommendation: {payload.get('recommendation') or 'Pending'}",
        f"- TAALI score: {score_text('taali_score')}",
        f"- Assessment score: {score_text('assessment_score')}",
        f"- Role fit: {score_text('role_fit_score')}",
    ]

    if scores.get("cv_fit_score") is not None:
        lines.append(f"- CV fit: {score_text('cv_fit_score')}")
    if scores.get("requirements_fit_score") is not None:
        lines.append(f"- Requirements fit: {score_text('requirements_fit_score')}")
    if payload.get("benchmark_label"):
        lines.append(f"- Benchmark position: {payload.get('benchmark_label')}")
    if payload.get("score_formula_version"):
        lines.append(f"- Score model: {payload.get('score_formula_version')}")

    lines.extend(["", "Assessment Evidence", "-------------------"])
    if payload.get("completed_at"):
        lines.append(f"- Completed at: {payload.get('completed_at')}")
    if payload.get("duration_label"):
        lines.append(f"- Duration: {payload.get('duration_label')}")
    if payload.get("tests_total") is not None:
        lines.append(f"- Tests passed: {payload.get('tests_passed') or 0}/{payload.get('tests_total')}")
    if payload.get("prompt_count") is not None:
        lines.append(f"- AI interactions captured: {payload.get('prompt_count')}")
    for item in dimension_scores[:4]:
        lines.append(f"- {item.get('label')}: {item.get('score')}/10")

    lines.extend(["", "Strengths To Validate", "---------------------"])
    if strengths:
        for item in strengths:
            lines.append(
                f"- {item.get('dimension')} ({item.get('score')}/10): {item.get('validation_prompt')}"
            )
    else:
        lines.append("- No dimension strengths were available in the stored assessment payload.")

    lines.extend(["", "Suggested Interview Focus", "-------------------------"])
    if interview_focus:
        for item in interview_focus:
            summary = str(item.get("evidence") or item.get("practice_advice") or "").strip()
            lines.append(f"- {item.get('dimension')} ({item.get('score')}/10): {summary}")
    else:
        lines.append("- No additional interview focus areas were generated for this assessment.")

    lines.extend(["", "Role-Fit Snapshot", "-----------------"])
    if payload.get("role_fit_summary"):
        lines.append(str(payload.get("role_fit_summary")))
    if requirements_coverage.get("total"):
        lines.append(
            "- Requirement coverage: "
            f"{requirements_coverage.get('met', 0)} met, "
            f"{requirements_coverage.get('partially_met', 0)} partial, "
            f"{requirements_coverage.get('missing', 0)} missing "
            f"out of {requirements_coverage.get('total')}."
        )
    if matching_skills:
        lines.append(f"- Matching skills: {', '.join(matching_skills[:6])}")
    for item in experience_highlights[:2]:
        lines.append(f"- Relevant experience: {item}")
    for item in requirements_assessment[:2]:
        requirement = str(item.get("requirement") or "").strip()
        status = str(item.get("status") or "").replace("_", " ").strip()
        evidence = str(item.get("evidence") or "").strip()
        if requirement:
            lines.append(f"- Requirement: {requirement} ({status or 'pending'})")
            if evidence:
                lines.append(f"  {evidence}")
    for item in concerns[:2]:
        lines.append(f"- Risk to probe: {item}")

    lines.extend(["", "Integrity And Caveats", "---------------------"])
    lines.append(str(payload.get("integrity_note") or "No additional caveats were attached to this assessment."))

    return "\n".join(lines).strip()


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
