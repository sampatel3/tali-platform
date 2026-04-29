from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from .document_service import sanitize_json_for_storage, sanitize_text_for_storage
from .interview_tech_questions import (
    deterministic_tech_questions,
    format_evidence_anchor,
    maybe_generate_tech_questions,
)
from .pre_screening_service import pre_screen_snapshot

INTERVIEW_STAGES = ("screening", "tech_stage_2")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clean_text(value: Any, *, max_len: int = 4000) -> str | None:
    text = sanitize_text_for_storage(str(value or "").strip())
    if not text:
        return None
    return text[:max_len]


def _clean_email(value: Any) -> str | None:
    text = _clean_text(value, max_len=255)
    if not text:
        return None
    return text.lower()


def _string_list(value: Any, *, max_items: int = 8, max_len: int = 220) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in value:
        item = _clean_text(raw, max_len=max_len)
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= max_items:
            break
    return out


def _question(
    *,
    question: str,
    why_this_matters: str | None = None,
    evidence_anchor: str | None = None,
    positive_signals: list[str] | None = None,
    red_flags: list[str] | None = None,
    follow_up_probe: str | None = None,
) -> dict[str, Any]:
    return sanitize_json_for_storage(
        {
            "question": _clean_text(question, max_len=600) or "Question",
            "why_this_matters": _clean_text(why_this_matters, max_len=500),
            "evidence_anchor": _clean_text(evidence_anchor, max_len=500),
            "positive_signals": _string_list(positive_signals or [], max_items=5),
            "red_flags": _string_list(red_flags or [], max_items=5),
            "follow_up_probe": _clean_text(follow_up_probe, max_len=500),
        }
    )


def _requirement_gaps(details: dict[str, Any]) -> list[dict[str, Any]]:
    requirements = details.get("requirements_assessment")
    if not isinstance(requirements, list):
        return []
    gaps: list[dict[str, Any]] = []
    for item in requirements:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower()
        requirement = _clean_text(item.get("requirement"), max_len=220)
        if not requirement or status in {"", "met"}:
            continue
        gaps.append(
            {
                "requirement": requirement,
                "status": status,
                "evidence": _clean_text(item.get("evidence") or item.get("impact"), max_len=400),
            }
        )
    return gaps


def _assessment_signal(application: CandidateApplication) -> dict[str, Any]:
    completed = []
    for assessment in application.assessments or []:
        if bool(getattr(assessment, "is_voided", False)):
            continue
        status = getattr(getattr(assessment, "status", None), "value", getattr(assessment, "status", None))
        if str(status or "").lower() not in {"completed", "completed_due_to_timeout"}:
            continue
        completed.append(assessment)
    if not completed:
        return {}
    latest = sorted(
        completed,
        key=lambda item: (
            getattr(item, "completed_at", None) or datetime.min.replace(tzinfo=timezone.utc),
            getattr(item, "created_at", None) or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )[0]
    return sanitize_json_for_storage(
        {
            "assessment_id": latest.id,
            "assessment_score": getattr(latest, "assessment_score", None)
            if getattr(latest, "assessment_score", None) is not None
            else getattr(latest, "final_score", None),
            "task_name": getattr(getattr(latest, "task", None), "name", None),
            "completed_at": getattr(latest, "completed_at", None),
        }
    )


def _ordered_interviews(items: list[Any]) -> list[Any]:
    return sorted(
        items,
        key=lambda item: (
            getattr(item, "meeting_date", None) or getattr(item, "linked_at", None) or datetime.min.replace(tzinfo=timezone.utc),
            getattr(item, "created_at", None) or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )


def _fireflies_org_context(application: CandidateApplication, organization: Any | None = None) -> dict[str, Any]:
    org = organization or getattr(application, "organization", None)
    owner_email = _clean_email(getattr(org, "fireflies_owner_email", None) if org else None)
    invite_email = _clean_email(getattr(org, "fireflies_invite_email", None) if org else None)
    has_api_key = bool((getattr(org, "fireflies_api_key_encrypted", None) or "").strip()) if org else False
    configured = bool(has_api_key and owner_email)
    capture_expected = str(getattr(application, "source", "") or "").strip().lower() == "workable"
    return sanitize_json_for_storage(
        {
            "configured": configured,
            "capture_expected": capture_expected,
            "invite_email": invite_email,
        }
    )


def _fireflies_metadata_for_interviews(
    interviews: list[Any],
    fireflies_context: dict[str, Any],
) -> dict[str, Any]:
    linked = _ordered_interviews(
        [
            item for item in interviews
            if str(getattr(item, "provider", "") or "").strip().lower() == "fireflies"
        ]
    )
    latest = linked[0] if linked else None
    latest_payload = getattr(latest, "provider_payload", None) if latest else None
    latest_payload = latest_payload if isinstance(latest_payload, dict) else {}
    taali_match = latest_payload.get("taali_match") if isinstance(latest_payload.get("taali_match"), dict) else {}
    invite_email = _clean_email(taali_match.get("fireflies_invite_email")) or _clean_email(fireflies_context.get("invite_email"))

    status = "linked"
    if latest is None:
        if not fireflies_context.get("configured"):
            status = "not_configured"
        elif fireflies_context.get("capture_expected"):
            status = "awaiting_transcript"
        else:
            status = "not_expected"

    latest_summary = None
    if latest is not None:
        latest_summary = _clean_text(getattr(latest, "summary", None), max_len=600)
        if not latest_summary:
            latest_summary = _clean_text(getattr(latest, "transcript_text", None), max_len=600)

    return sanitize_json_for_storage(
        {
            "status": status,
            "configured": bool(fireflies_context.get("configured")),
            "capture_expected": bool(fireflies_context.get("capture_expected")),
            "invite_email": invite_email,
            "linked_interviews_count": len(linked),
            "latest_source": getattr(latest, "source", None) if latest is not None else None,
            "latest_stage": getattr(latest, "stage", None) if latest is not None else None,
            "latest_meeting_date": getattr(latest, "meeting_date", None) if latest is not None else None,
            "latest_provider_url": getattr(latest, "provider_url", None) if latest is not None else None,
            "latest_provider_meeting_id": getattr(latest, "provider_meeting_id", None) if latest is not None else None,
            "latest_summary": latest_summary,
            "latest_linked_at": getattr(latest, "linked_at", None) if latest is not None else None,
        }
    )


def build_role_interview_pack_templates(role: Role) -> dict[str, dict[str, Any]]:
    role_focus = role.interview_focus if isinstance(role.interview_focus, dict) else {}
    focus_questions = role_focus.get("questions") if isinstance(role_focus.get("questions"), list) else []
    screening_questions: list[dict[str, Any]] = []
    tech_questions: list[dict[str, Any]] = []

    for item in focus_questions[:4]:
        if not isinstance(item, dict):
            continue
        prompt = _clean_text(item.get("question"), max_len=400)
        if not prompt:
            continue
        screening_questions.append(
            _question(
                question=prompt,
                why_this_matters="Validates the candidate's role-fit claims before advancing.",
                evidence_anchor=role_focus.get("role_summary") or role.name,
                positive_signals=_string_list(item.get("what_to_listen_for"), max_items=4),
                red_flags=_string_list(item.get("concerning_signals"), max_items=4),
                follow_up_probe="Ask for a recent example with measurable outcomes and ownership boundaries.",
            )
        )

    additional_topics = []
    for raw in str(role.additional_requirements or "").splitlines():
        topic = _clean_text(raw.lstrip("-* "), max_len=180)
        if topic:
            additional_topics.append(topic)
        if len(additional_topics) >= 4:
            break
    for topic in additional_topics:
        tech_questions.append(
            _question(
                question=f"Walk through a recent system or project where you demonstrated {topic.lower()}.",
                why_this_matters="Confirms deeper technical depth against recruiter-defined requirements.",
                evidence_anchor=topic,
                positive_signals=[
                    "Specific architecture or implementation decisions",
                    "Tradeoff awareness",
                    "Clear ownership and measurable impact",
                ],
                red_flags=[
                    "Vague examples without direct ownership",
                    "Tool-name dropping without implementation detail",
                ],
                follow_up_probe="Ask what broke, how they debugged it, and what they would change in hindsight.",
            )
        )

    if not tech_questions:
        for item in screening_questions[:3]:
            tech_questions.append(
                _question(
                    question=item["question"],
                    why_this_matters="Carries role-fit validation into a deeper technical conversation.",
                    evidence_anchor=item.get("evidence_anchor"),
                    positive_signals=item.get("positive_signals"),
                    red_flags=item.get("red_flags"),
                    follow_up_probe="Shift from narrative to implementation detail, edge cases, and debugging choices.",
                )
            )

    return {
        "screening": {
            "stage": "screening",
            "summary": _clean_text(role_focus.get("role_summary"), max_len=400)
            or f"Initial screening pack for {role.name}.",
            "source": "role_template",
            "generated_at": _utcnow().isoformat(),
            "questions": screening_questions,
        },
        "tech_stage_2": {
            "stage": "tech_stage_2",
            "summary": f"Second-stage technical pack for {role.name}.",
            "source": "role_template",
            "generated_at": _utcnow().isoformat(),
            "questions": tech_questions,
        },
    }


def summarize_application_interviews(
    application: CandidateApplication,
    *,
    organization: Any | None = None,
) -> dict[str, dict[str, Any]]:
    fireflies_context = _fireflies_org_context(application, organization=organization)
    stage_map: dict[str, list[Any]] = {stage: [] for stage in INTERVIEW_STAGES}
    for interview in application.interviews or []:
        stage = str(getattr(interview, "stage", "") or "").strip().lower()
        if stage not in stage_map:
            continue
        stage_map[stage].append(interview)

    summaries: dict[str, dict[str, Any]] = {}
    for stage, interviews in stage_map.items():
        ordered = _ordered_interviews(interviews)
        latest = ordered[0] if ordered else None
        combined_highlights: list[str] = []
        for item in ordered[:2]:
            summary_text = _clean_text(getattr(item, "summary", None), max_len=240)
            if summary_text:
                combined_highlights.append(summary_text)
                continue
            transcript = _clean_text(getattr(item, "transcript_text", None), max_len=240)
            if transcript:
                combined_highlights.append(transcript)
        summaries[stage] = sanitize_json_for_storage(
            {
                "stage": stage,
                "interviews_count": len(ordered),
                "latest_meeting_date": getattr(latest, "meeting_date", None) if latest else None,
                "latest_provider_url": getattr(latest, "provider_url", None) if latest else None,
                "summary": " ".join(combined_highlights).strip() or None,
                "speakers": getattr(latest, "speakers", None) if latest and isinstance(getattr(latest, "speakers", None), list) else [],
                "fireflies": _fireflies_metadata_for_interviews(ordered, fireflies_context),
            }
        )
    return summaries


def build_application_interview_support(
    application: CandidateApplication,
    *,
    organization: Any | None = None,
) -> dict[str, Any]:
    role = application.role
    details = application.cv_match_details if isinstance(application.cv_match_details, dict) else {}
    templates = build_role_interview_pack_templates(role) if role else {
        "screening": {"stage": "screening", "summary": "Screening pack", "source": "generated", "generated_at": _utcnow().isoformat(), "questions": []},
        "tech_stage_2": {"stage": "tech_stage_2", "summary": "Technical interview pack", "source": "generated", "generated_at": _utcnow().isoformat(), "questions": []},
    }
    if role:
        if isinstance(role.screening_pack_template, dict):
            templates["screening"] = sanitize_json_for_storage(role.screening_pack_template)
        if isinstance(role.tech_interview_pack_template, dict):
            templates["tech_stage_2"] = sanitize_json_for_storage(role.tech_interview_pack_template)

    missing_skills = _string_list(details.get("missing_skills"), max_items=5)
    matching_skills = _string_list(details.get("matching_skills"), max_items=5)
    concerns = _string_list(details.get("concerns"), max_items=4)
    requirement_gaps = _requirement_gaps(details)
    interview_summaries = summarize_application_interviews(application, organization=organization)
    assessment_signal = _assessment_signal(application)
    pre_screen = pre_screen_snapshot(application)
    fireflies_context = _fireflies_org_context(application, organization=organization)
    fireflies_summary = _fireflies_metadata_for_interviews(list(application.interviews or []), fireflies_context)

    screening_questions = list((templates.get("screening") or {}).get("questions") or [])
    for gap in requirement_gaps[:3]:
        screening_questions.append(
            _question(
                question=f"Tell me about a recent example where you demonstrated {gap['requirement'].lower()}.",
                why_this_matters="This requirement showed a gap or only partial evidence in the pre-screen review.",
                evidence_anchor=gap.get("evidence") or gap["requirement"],
                positive_signals=[
                    "Recent hands-on example",
                    "Clear ownership",
                    "Evidence that maps directly to the role requirement",
                ],
                red_flags=[
                    "Only high-level familiarity",
                    "No direct ownership or measurable outcome",
                ],
                follow_up_probe="Ask what they personally owned, what constraints existed, and what outcomes improved.",
            )
        )
    for concern in concerns[:2]:
        screening_questions.append(
            _question(
                question=f"Help me understand this area in more depth: {concern}",
                why_this_matters="Pre-screening surfaced this as a risk that should be validated early.",
                evidence_anchor=concern,
                positive_signals=["Direct answer", "Specific examples", "Learning loop or mitigation plan"],
                red_flags=["Deflection", "Abstract answers", "No concrete examples"],
                follow_up_probe="Ask for the most recent example and exactly what the candidate changed or owned.",
            )
        )

    tech_questions = list((templates.get("tech_stage_2") or {}).get("questions") or [])
    screening_summary = interview_summaries.get("screening") or {}

    # LLM-driven tech-question generator runs first (CV evidence +
    # transcript anchored). Falls back to the deterministic templates
    # below when the call fails or returns nothing.
    llm_tech_questions = maybe_generate_tech_questions(
        application,
        role,
        details,
        pre_screen.get("pre_screen_evidence") if isinstance(pre_screen, dict) else None,
    )
    use_llm = bool(llm_tech_questions)
    raw_pack = llm_tech_questions if use_llm else deterministic_tech_questions(
        missing_skills, screening_summary.get("summary"),
    )
    for raw_q in raw_pack:
        tech_questions.append(
            _question(
                question=raw_q.get("question") or "",
                why_this_matters=raw_q.get("why_this_matters"),
                evidence_anchor=format_evidence_anchor(raw_q) if use_llm else raw_q.get("evidence_anchor"),
                positive_signals=raw_q.get("positive_signals"),
                red_flags=raw_q.get("red_flags"),
                follow_up_probe=raw_q.get("follow_up_probe"),
            )
        )

    screening_pack = sanitize_json_for_storage(
        {
            "stage": "screening",
            "summary": (
                (templates.get("screening") or {}).get("summary")
                or f"Screening pack for {role.name if role else 'this role'}."
            ),
            "source": "application_generated",
            "generated_at": _utcnow().isoformat(),
            "questions": _dedupe_questions(screening_questions),
        }
    )
    tech_pack = sanitize_json_for_storage(
        {
            "stage": "tech_stage_2",
            "summary": (
                (templates.get("tech_stage_2") or {}).get("summary")
                or f"Technical interview pack for {role.name if role else 'this role'}."
            ),
            "source": "application_generated",
            "generated_at": _utcnow().isoformat(),
            "questions": _dedupe_questions(tech_questions),
            # Stamp the CV scoring version so maybe_generate_tech_questions
            # can skip regeneration when the CV hasn't changed.
            "cv_match_prompt_version": details.get("prompt_version") or details.get("scoring_version") or "",
        }
    )
    interview_evidence_summary = sanitize_json_for_storage(
        {
            "screening_summary": screening_summary.get("summary"),
            "tech_summary": (interview_summaries.get("tech_stage_2") or {}).get("summary"),
            "matching_skills": matching_skills,
            "missing_skills": missing_skills,
            "concerns": concerns,
            "pre_screen_score": pre_screen.get("pre_screen_score"),
            "assessment_signal": assessment_signal,
            "fireflies": fireflies_summary,
        }
    )
    return {
        "screening_pack": screening_pack,
        "tech_interview_pack": tech_pack,
        "screening_interview_summary": screening_summary,
        "tech_interview_summary": interview_summaries.get("tech_stage_2") or {},
        "interview_evidence_summary": interview_evidence_summary,
    }


def refresh_application_interview_support(
    application: CandidateApplication,
    *,
    organization: Any | None = None,
) -> dict[str, Any]:
    support = build_application_interview_support(application, organization=organization)
    application.screening_pack = support["screening_pack"]
    application.tech_interview_pack = support["tech_interview_pack"]
    application.screening_interview_summary = support["screening_interview_summary"]
    application.tech_interview_summary = support["tech_interview_summary"]
    application.interview_evidence_summary = support["interview_evidence_summary"]
    return support


def _dedupe_questions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        question = _clean_text(item.get("question"), max_len=600)
        if not question:
            continue
        key = question.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            _question(
                question=question,
                why_this_matters=item.get("why_this_matters"),
                evidence_anchor=item.get("evidence_anchor"),
                positive_signals=item.get("positive_signals"),
                red_flags=item.get("red_flags"),
                follow_up_probe=item.get("follow_up_probe"),
            )
        )
        if len(out) >= 8:
            break
    return out
