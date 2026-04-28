"""Convert Tali domain objects (Candidate, ApplicationInterview, etc.)
into Graphiti ``add_episode`` calls.

Episode design rules:
- One episode per logical "thing said about a candidate" — not one per
  field. Graphiti charges an LLM call per episode, so coarse is cheaper.
- ``reference_time`` anchors the temporal validity of facts in the
  episode. Use the most accurate timestamp we have (interview date,
  experience start_date, candidate creation date).
- ``source_description`` tags provenance so we can debug why a fact
  was extracted. Recruiters never see this; engineers do.
- Every episode is namespaced via ``group_id = org:{organization_id}``.
- Episode body always begins with a "Subject candidate" line so the LLM
  binds extracted facts to the right person — Graphiti merges entities
  across episodes by name + group_id, so this is load-bearing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.candidate_application_event import CandidateApplicationEvent
from ..models.application_interview import ApplicationInterview
from . import client as graph_client

logger = logging.getLogger("taali.candidate_graph.episodes")


@dataclass
class Episode:
    """In-memory representation of a Graphiti episode before dispatch."""

    name: str
    body: str
    source_description: str
    reference_time: datetime
    group_id: str


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_datetime(value: Any, fallback: datetime | None = None) -> datetime:
    """Best-effort conversion of mixed datetime/str inputs to aware UTC."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str) and value.strip():
        for fmt in ("%Y-%m-%d", "%Y-%m"):
            try:
                return datetime.strptime(value.strip()[: len(fmt) + 2], fmt).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue
    return fallback or _now_utc()


def _candidate_subject_header(candidate: Candidate) -> str:
    """First line of every candidate-scoped episode.

    Graphiti binds extracted entities by name; using a stable identifier
    plus the candidate's full name ensures cross-episode merging.
    """
    name = (candidate.full_name or "").strip() or f"Candidate {candidate.id}"
    return f"Subject candidate: {name} (taali_id={candidate.id})"


# ---------------------------------------------------------------------------
# Builders — one function per episode kind. Each returns 0+ Episode objects.
# ---------------------------------------------------------------------------


def build_candidate_profile_episodes(
    candidate: Candidate,
    *,
    max_episodes: int,
) -> list[Episode]:
    """Profile, education, and skills as 1-3 compact episodes per candidate.

    Experience entries get one episode each (so date validity propagates
    naturally) up to ``max_episodes - 2`` (reserving slots for the
    summary + skills+education episode).
    """
    org = int(candidate.organization_id or 0)
    if org <= 0:
        return []
    group_id = graph_client.group_id_for_org(org)
    header = _candidate_subject_header(candidate)
    out: list[Episode] = []

    # Episode 1: identity + summary + current location
    summary_lines = [header]
    if candidate.headline:
        summary_lines.append(f"Headline: {candidate.headline}")
    if candidate.position:
        summary_lines.append(f"Current position: {candidate.position}")
    if candidate.location_country:
        loc = candidate.location_country
        if candidate.location_city:
            loc = f"{candidate.location_city}, {loc}"
        summary_lines.append(f"Located in: {loc}")
    if candidate.summary:
        summary_lines.append("")
        summary_lines.append("Summary:")
        summary_lines.append(candidate.summary[:1500])
    out.append(
        Episode(
            name=f"candidate-{candidate.id}-profile",
            body="\n".join(summary_lines),
            source_description="candidate.profile",
            reference_time=_coerce_datetime(candidate.created_at, fallback=_now_utc()),
            group_id=group_id,
        )
    )

    # Episode 2: skills + education in one shot (cheap; both are short).
    skills = _collect_skills(candidate)
    educations = list(_iter_educations(candidate))
    if skills or educations:
        body_lines = [header, ""]
        if skills:
            body_lines.append("Skills: " + ", ".join(skills[:80]))
        for edu in educations:
            line = "Studied"
            if edu.get("degree"):
                line += f" {edu['degree']}"
            if edu.get("field"):
                line += f" in {edu['field']}"
            line += f" at {edu['institution']}"
            if edu.get("start") or edu.get("end"):
                line += f" ({edu.get('start', '')}–{edu.get('end', '')})"
            body_lines.append(line)
        out.append(
            Episode(
                name=f"candidate-{candidate.id}-skills-education",
                body="\n".join(body_lines),
                source_description="candidate.skills_education",
                reference_time=_coerce_datetime(candidate.created_at, fallback=_now_utc()),
                group_id=group_id,
            )
        )

    # Episodes 3..N: one per experience entry, oldest first so Graphiti's
    # temporal merging sees forward time motion.
    experiences = sorted(
        _iter_experiences(candidate),
        key=lambda e: e.get("start_date", "0000"),
    )
    remaining = max_episodes - len(out)
    for entry in experiences[: max(0, remaining)]:
        body = _experience_episode_body(header, entry)
        ref_time = _coerce_datetime(entry.get("start_date"), fallback=_now_utc())
        out.append(
            Episode(
                name=f"candidate-{candidate.id}-exp-{_safe_slug(entry['company'])}-{entry.get('start_date','')}",
                body=body,
                source_description="candidate.experience",
                reference_time=ref_time,
                group_id=group_id,
            )
        )

    return out


def build_cv_text_episode(candidate: Candidate) -> Episode | None:
    """One episode for raw CV text — captures things cv_parsing missed.

    cv_text can be 5-15KB; we truncate to 12KB to stay well under the
    Graphiti per-episode token budget.
    """
    if not candidate.cv_text:
        return None
    org = int(candidate.organization_id or 0)
    if org <= 0:
        return None
    body = "\n".join(
        [
            _candidate_subject_header(candidate),
            "",
            "Full CV text follows:",
            "",
            candidate.cv_text[:12_000],
        ]
    )
    return Episode(
        name=f"candidate-{candidate.id}-cv",
        body=body,
        source_description="candidate.cv_text",
        reference_time=_coerce_datetime(candidate.cv_uploaded_at, fallback=_now_utc()),
        group_id=graph_client.group_id_for_org(org),
    )


def build_interview_episodes(interview: ApplicationInterview) -> list[Episode]:
    """One episode per interview — transcript first, then structured summary."""
    org = int(interview.organization_id or 0)
    if org <= 0:
        return []
    group_id = graph_client.group_id_for_org(org)
    candidate = interview.application.candidate if interview.application else None
    if candidate is None:
        return []
    header = _candidate_subject_header(candidate)
    ref_time = _coerce_datetime(interview.meeting_date, fallback=_now_utc())
    out: list[Episode] = []

    if interview.transcript_text:
        speakers = _format_speakers(interview.speakers)
        body_lines = [
            header,
            f"Interview stage: {interview.stage}",
            f"Source: {interview.source} ({interview.provider or 'unknown provider'})",
        ]
        if speakers:
            body_lines.append(f"Speakers: {speakers}")
        body_lines.append("")
        body_lines.append("Transcript:")
        body_lines.append(interview.transcript_text[:18_000])
        out.append(
            Episode(
                name=f"interview-{interview.id}-transcript",
                body="\n".join(body_lines),
                source_description=f"interview.transcript.{interview.stage}",
                reference_time=ref_time,
                group_id=group_id,
            )
        )

    if interview.summary and isinstance(interview.summary, dict):
        body_lines = [header, f"Interview stage: {interview.stage}", ""]
        for key, value in interview.summary.items():
            if isinstance(value, (list, tuple)) and value:
                body_lines.append(f"{key}:")
                for item in value:
                    body_lines.append(f"  - {item}")
            elif isinstance(value, str) and value.strip():
                body_lines.append(f"{key}: {value.strip()[:600]}")
        out.append(
            Episode(
                name=f"interview-{interview.id}-summary",
                body="\n".join(body_lines),
                source_description=f"interview.summary.{interview.stage}",
                reference_time=ref_time,
                group_id=group_id,
            )
        )
    return out


def build_event_episode(event: CandidateApplicationEvent) -> Episode | None:
    """Pipeline-stage transitions and recruiter notes as a single episode.

    Lower-priority source: most events are pure state transitions with
    no extractable facts. We ingest them anyway so queries like "rejected
    due to comp mismatch" can pick up note text.
    """
    if not event.application:
        return None
    candidate = event.application.candidate
    if candidate is None:
        return None
    org = int(event.application.organization_id or 0)
    if org <= 0:
        return None
    note = (getattr(event, "notes", None) or getattr(event, "comment", None) or "").strip()
    body_lines = [
        _candidate_subject_header(candidate),
        f"Pipeline event: {event.event_type}",
    ]
    if hasattr(event, "from_value") and event.from_value:
        body_lines.append(f"From: {event.from_value}")
    if hasattr(event, "to_value") and event.to_value:
        body_lines.append(f"To: {event.to_value}")
    if note:
        body_lines.append("")
        body_lines.append(f"Note: {note[:1500]}")
    if len(body_lines) <= 2 and not note:
        # Pure stage transition with no commentary — not worth an LLM call.
        return None
    return Episode(
        name=f"event-{event.id}",
        body="\n".join(body_lines),
        source_description=f"event.{event.event_type}",
        reference_time=_coerce_datetime(event.created_at, fallback=_now_utc()),
        group_id=graph_client.group_id_for_org(org),
    )


# ---------------------------------------------------------------------------
# Dispatch — turn Episode objects into Graphiti add_episode calls.
# ---------------------------------------------------------------------------


def dispatch(episodes: Iterable[Episode]) -> int:
    """Send episodes to Graphiti. Returns the number successfully sent.

    Graphiti's ``add_episode`` is async; we dispatch via the shared loop.
    Errors on individual episodes are logged but don't abort the batch —
    a partial graph is better than nothing.
    """
    sent = 0
    if not graph_client.is_configured():
        return 0
    try:
        from graphiti_core.nodes import EpisodeType  # type: ignore[import-not-found]
    except Exception as exc:
        logger.warning("graphiti_core not importable: %s", exc)
        return 0

    graphiti = graph_client.get_graphiti()
    for episode in episodes:
        try:
            graph_client.run_async(
                graphiti.add_episode(
                    name=episode.name,
                    episode_body=episode.body,
                    source=EpisodeType.text,
                    source_description=episode.source_description,
                    reference_time=episode.reference_time,
                    group_id=episode.group_id,
                ),
                timeout=120.0,
            )
            sent += 1
        except Exception as exc:
            logger.warning(
                "Graphiti add_episode failed name=%s reason=%s", episode.name, exc
            )
    return sent


# ---------------------------------------------------------------------------
# Helpers — extract structured experience/skills from the candidate.
# ---------------------------------------------------------------------------


def _iter_experiences(candidate: Candidate) -> Iterable[dict]:
    """Yield ``{company, title, location, start_date, end_date, summary, industry}``.

    Reads BOTH the Workable shape (``experience_entries``) and the
    cv_parsing shape (``cv_sections.experience``), de-duped by
    (company_norm, start_date).
    """
    seen: set[tuple[str, str]] = set()
    for entry in candidate.experience_entries or []:
        if not isinstance(entry, dict):
            continue
        company = (entry.get("company") or "").strip()
        if not company:
            continue
        start = str(entry.get("start_date") or "").strip()
        key = (company.lower(), start)
        if key in seen:
            continue
        seen.add(key)
        yield {
            "company": company,
            "title": (entry.get("title") or "").strip(),
            "start_date": start,
            "end_date": str(entry.get("end_date") or "").strip(),
            "location": (entry.get("location") or "").strip(),
            "summary": (entry.get("summary") or "").strip(),
            "industry": (entry.get("industry") or "").strip(),
        }
    cv_sections = candidate.cv_sections or {}
    if isinstance(cv_sections, dict):
        for entry in cv_sections.get("experience") or []:
            if not isinstance(entry, dict):
                continue
            company = (entry.get("company") or "").strip()
            if not company:
                continue
            start = str(entry.get("start") or "").strip()
            key = (company.lower(), start)
            if key in seen:
                continue
            seen.add(key)
            bullets = entry.get("bullets") or []
            yield {
                "company": company,
                "title": (entry.get("title") or "").strip(),
                "start_date": start,
                "end_date": str(entry.get("end") or "").strip(),
                "location": (entry.get("location") or "").strip(),
                "summary": "\n".join(b for b in bullets if isinstance(b, str))[:2000],
                "industry": "",
            }


def _iter_educations(candidate: Candidate) -> Iterable[dict]:
    seen: set[str] = set()
    for entry in candidate.education_entries or []:
        if not isinstance(entry, dict):
            continue
        institution = (entry.get("school") or entry.get("institution") or "").strip()
        if not institution or institution.lower() in seen:
            continue
        seen.add(institution.lower())
        yield {
            "institution": institution,
            "degree": (entry.get("degree") or "").strip(),
            "field": (entry.get("field_of_study") or entry.get("field") or "").strip(),
            "start": str(entry.get("start_date") or "").strip(),
            "end": str(entry.get("end_date") or "").strip(),
        }
    cv_sections = candidate.cv_sections or {}
    if isinstance(cv_sections, dict):
        for entry in cv_sections.get("education") or []:
            if not isinstance(entry, dict):
                continue
            institution = (entry.get("institution") or entry.get("school") or "").strip()
            if not institution or institution.lower() in seen:
                continue
            seen.add(institution.lower())
            yield {
                "institution": institution,
                "degree": (entry.get("degree") or "").strip(),
                "field": (entry.get("field") or "").strip(),
                "start": str(entry.get("start") or "").strip(),
                "end": str(entry.get("end") or "").strip(),
            }


def _collect_skills(candidate: Candidate) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for source in (candidate.skills, (candidate.cv_sections or {}).get("skills") if isinstance(candidate.cv_sections, dict) else None):
        if not isinstance(source, list):
            continue
        for item in source:
            value = str(item or "").strip()
            if value and value.lower() not in seen:
                seen.add(value.lower())
                out.append(value)
    return out


def _experience_episode_body(header: str, entry: dict) -> str:
    lines = [header]
    role_line = f"Worked at {entry['company']}"
    if entry.get("title"):
        role_line += f" as {entry['title']}"
    if entry.get("location"):
        role_line += f" in {entry['location']}"
    if entry.get("start_date") or entry.get("end_date"):
        role_line += f" ({entry.get('start_date', '')}–{entry.get('end_date') or 'Present'})"
    if entry.get("industry"):
        role_line += f". Industry: {entry['industry']}."
    lines.append(role_line)
    if entry.get("summary"):
        lines.append("")
        lines.append(entry["summary"][:2000])
    return "\n".join(lines)


def _format_speakers(speakers: Any) -> str:
    if not isinstance(speakers, list):
        return ""
    names = []
    for s in speakers:
        if isinstance(s, dict):
            name = s.get("name") or s.get("speaker") or s.get("label")
            if name:
                names.append(str(name))
        elif isinstance(s, str):
            names.append(s)
    return ", ".join(names[:10])


def _safe_slug(value: str) -> str:
    cleaned = "".join(c if c.isalnum() else "-" for c in (value or "")).strip("-").lower()
    return cleaned[:60] or "x"
