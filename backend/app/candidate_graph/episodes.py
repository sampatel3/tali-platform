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
from hashlib import sha256
from heapq import nsmallest
from itertools import chain, islice
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Iterable

from ..models.candidate import Candidate
from ..models.candidate_application_event import CandidateApplicationEvent
from ..models.application_interview import ApplicationInterview
from ..services.provider_error_evidence import safe_provider_error_code
from . import client as graph_client
from .ingest_manifest import (
    MAX_EPISODE_NAME_BYTES,
    MAX_EPISODE_PAYLOAD_BYTES,
    MAX_MANIFEST_EPISODES,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger("taali.candidate_graph.episodes")


class GraphProviderRuntimeError(RuntimeError):
    """Secret-safe failure at the outer Graphiti operation boundary."""

_EPISODE_TRUNCATION_MARKER = (
    "\n[Episode content truncated to fit the graph ingestion byte limit.]"
)
_MAX_STRUCTURED_SOURCE_ITEMS = 10_000
_PROVIDER_CONTROL_TRANSLATION = {
    codepoint: " "
    for codepoint in (*range(32), 127)
    if codepoint not in (9, 10, 13)
}
_IDENTIFIER_CONTROL_TRANSLATION = {
    codepoint: " " for codepoint in (*range(32), 127)
}


@dataclass
class Episode:
    """In-memory representation of a Graphiti episode before dispatch."""

    name: str
    body: str
    source_description: str
    reference_time: datetime
    group_id: str


def _episode_text_source():
    """Resolve Graphiti lazily so web/test imports do not require its runtime."""

    from graphiti_core.nodes import EpisodeType  # type: ignore[import-not-found]

    return EpisodeType.text


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _provider_text_byte_chunks(value: str) -> Iterable[bytes]:
    """Encode normalized provider text in bounded-memory chunks."""

    for offset in range(0, len(value), 4096):
        yield value[offset : offset + 4096].translate(
            _PROVIDER_CONTROL_TRANSLATION
        ).encode("utf-8", errors="replace")


def _identifier_byte_chunks(value: str) -> Iterable[bytes]:
    for offset in range(0, len(value), 4096):
        yield value[offset : offset + 4096].translate(
            _IDENTIFIER_CONTROL_TRANSLATION
        ).encode("utf-8", errors="replace")


def _bounded_episode_segments(segments: Iterable[str]) -> str:
    """Preserve ordered text within the provider byte budget without overbuilding."""

    payload = bytearray()
    marker = _EPISODE_TRUNCATION_MARKER.encode("utf-8")
    content_limit = MAX_EPISODE_PAYLOAD_BYTES - len(marker)
    for segment in segments:
        for encoded in _provider_text_byte_chunks(segment):
            if len(payload) + len(encoded) <= MAX_EPISODE_PAYLOAD_BYTES:
                payload.extend(encoded)
                continue
            if len(payload) < content_limit:
                payload.extend(encoded[: content_limit - len(payload)])
            del payload[content_limit:]
            return (
                payload.decode("utf-8", errors="ignore")
                + _EPISODE_TRUNCATION_MARKER
            )
    return payload.decode("utf-8")


def bounded_episode_body(value: str) -> str:
    """Return the exact normalized, UTF-8-bounded body sent to Graphiti."""

    return _bounded_episode_segments((value,))


def bounded_provider_identifier(value: str) -> str:
    """Normalize an SDK identifier and retain collision resistance if capped."""

    prefix = bytearray()
    digest = sha256()
    total_bytes = 0
    for encoded in _identifier_byte_chunks(value):
        digest.update(encoded)
        total_bytes += len(encoded)
        if len(prefix) < MAX_EPISODE_NAME_BYTES:
            prefix.extend(encoded[: MAX_EPISODE_NAME_BYTES - len(prefix)])
    if total_bytes <= MAX_EPISODE_NAME_BYTES:
        return prefix.decode("utf-8")
    suffix = f"~{digest.hexdigest()[:16]}".encode("ascii")
    del prefix[MAX_EPISODE_NAME_BYTES - len(suffix) :]
    return prefix.decode("utf-8", errors="ignore") + suffix.decode("ascii")


def _bounded_episode_lines(lines: Iterable[str]) -> str:
    def _segments() -> Iterable[str]:
        first = True
        for line in lines:
            if not first:
                yield "\n"
            yield line
            first = False

    return _bounded_episode_segments(_segments())


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


def _source_text(value: Any, *, max_chars: int) -> str:
    """Bound malformed ATS scalar fields before composing provider lines."""

    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()[:max_chars]
    if isinstance(value, (bool, int, float)):
        return str(value)[:max_chars]
    return ""


def _source_lines_text(values: Any, *, max_chars: int) -> str:
    """Join ordered source strings while stopping once the output is full."""

    if not isinstance(values, (list, tuple)):
        return ""
    parts: list[str] = []
    remaining = max_chars
    for value in islice(values, _MAX_STRUCTURED_SOURCE_ITEMS):
        if not isinstance(value, str):
            continue
        if parts:
            remaining -= 1
            if remaining <= 0:
                break
        part = value[:remaining]
        parts.append(part)
        remaining -= len(part)
        if remaining <= 0:
            break
    return "\n".join(parts)


def _candidate_subject_header(candidate: Candidate) -> str:
    """First line of every candidate-scoped episode.

    Graphiti binds extracted entities by name; using a stable identifier
    plus the candidate's full name ensures cross-episode merging.
    """
    name = _source_text(candidate.full_name, max_chars=1000) or f"Candidate {candidate.id}"
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
    max_episodes = max(0, min(int(max_episodes), MAX_MANIFEST_EPISODES))
    if org <= 0 or max_episodes == 0:
        return []
    group_id = graph_client.group_id_for_org(org)
    header = _candidate_subject_header(candidate)
    out: list[Episode] = []

    # Episode 1: identity + summary + current location
    summary_lines = [header]
    if candidate.headline:
        summary_lines.append(f"Headline: {_source_text(candidate.headline, max_chars=2000)}")
    if candidate.position:
        summary_lines.append(
            f"Current position: {_source_text(candidate.position, max_chars=2000)}"
        )
    if candidate.location_country:
        loc = _source_text(candidate.location_country, max_chars=1000)
        if candidate.location_city:
            loc = f"{_source_text(candidate.location_city, max_chars=1000)}, {loc}"
        summary_lines.append(f"Located in: {loc}")
    if candidate.summary:
        summary_lines.append("")
        summary_lines.append("Summary:")
        summary_lines.append(candidate.summary[:1500])
    out.append(
        Episode(
            name=f"candidate-{candidate.id}-profile",
            body=_bounded_episode_lines(summary_lines),
            source_description="candidate.profile",
            reference_time=_coerce_datetime(candidate.created_at, fallback=_now_utc()),
            group_id=group_id,
        )
    )
    if len(out) >= max_episodes:
        return out

    # Episode 2: skills + education in one shot (cheap; both are short).
    skills = _collect_skills(candidate, limit=80)
    education_iterator = iter(_iter_educations(candidate))
    first_education = next(education_iterator, None)
    if len(out) < max_episodes and (skills or first_education is not None):
        educations = (
            chain((first_education,), education_iterator)
            if first_education is not None
            else iter(())
        )

        def _skills_education_lines() -> Iterable[str]:
            yield header
            yield ""
            if skills:
                yield "Skills: " + ", ".join(skills)
            for edu in educations:
                line = "Studied"
                if edu.get("degree"):
                    line += f" {edu['degree']}"
                if edu.get("field"):
                    line += f" in {edu['field']}"
                line += f" at {edu['institution']}"
                if edu.get("start") or edu.get("end"):
                    line += f" ({edu.get('start', '')}–{edu.get('end', '')})"
                yield line

        out.append(
            Episode(
                name=f"candidate-{candidate.id}-skills-education",
                body=_bounded_episode_lines(_skills_education_lines()),
                source_description="candidate.skills_education",
                reference_time=_coerce_datetime(candidate.created_at, fallback=_now_utc()),
                group_id=group_id,
            )
        )

    # Episodes 3..N: one per experience entry, oldest first so Graphiti's
    # temporal merging sees forward time motion.
    remaining = max(0, max_episodes - len(out))
    experiences = nsmallest(
        remaining,
        _iter_experiences(candidate),
        key=lambda e: e.get("start_date", "0000"),
    )
    for entry in experiences:
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
        body=bounded_episode_body(body),
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

    stage = _source_text(interview.stage, max_chars=2000)
    if interview.transcript_text:
        speakers = _format_speakers(interview.speakers)
        body_lines = [
            header,
            f"Interview stage: {stage}",
            "Source: "
            f"{_source_text(interview.source, max_chars=2000)} "
            f"({_source_text(interview.provider, max_chars=2000) or 'unknown provider'})",
        ]
        if speakers:
            body_lines.append(f"Speakers: {speakers}")
        body_lines.append("")
        body_lines.append("Transcript:")
        body_lines.append(interview.transcript_text[:18_000])
        out.append(
            Episode(
                name=f"interview-{interview.id}-transcript",
                body=_bounded_episode_lines(body_lines),
                source_description=f"interview.transcript.{stage}",
                reference_time=ref_time,
                group_id=group_id,
            )
        )

    if interview.summary and isinstance(interview.summary, dict):

        def _summary_lines() -> Iterable[str]:
            yield header
            yield f"Interview stage: {stage}"
            yield ""
            for key, value in islice(interview.summary.items(), 100):
                if isinstance(value, (list, tuple)) and value:
                    yield f"{_source_text(key, max_chars=200)}:"
                    for item in value[:100]:
                        item_text = _source_text(item, max_chars=600)
                        if item_text:
                            yield f"  - {item_text}"
                elif isinstance(value, str) and value.strip():
                    yield (
                        f"{_source_text(key, max_chars=200)}: "
                        f"{value.strip()[:600]}"
                    )

        out.append(
            Episode(
                name=f"interview-{interview.id}-summary",
                body=_bounded_episode_lines(_summary_lines()),
                source_description=f"interview.summary.{stage}",
                reference_time=ref_time,
                group_id=group_id,
            )
        )
    return out


# System-bookkeeping event types that carry no qualitative facts beyond
# what's already on the candidate / application record. Skipping these
# keeps the per-org episode count (and Graphiti LLM bill) sane — they
# represent ~99% of candidate_application_events in production.
#   - pipeline_initialized: just "this row exists" (implied by the Person)
#   - cv_scored: the score itself lives on candidate_applications; the
#     reason field is templated ("CV scored: scored (46%)") with no facts
#     for the LLM to extract.
# ``agent_decision_queued`` is written by ``queue_decision.run`` so the
# AgentBar's last_activity tick has something to render mid-cycle. The
# rich decision episode is emitted separately by
# ``_emit_decision_episode_safe`` in the same path — letting this
# generic event also flow to Graphiti would double extraction + billing
# for every queued decision.
_NOISE_EVENT_TYPES = {
    "pipeline_initialized",
    "cv_scored",
    "agent_decision_queued",
    # Workable write-back mechanics — operational events about ATS sync
    # success/failure, not candidate facts. They carry a reason string so
    # they'd otherwise pass the note gate below and cost one Graphiti
    # extraction each (242 such episodes on 2026-06-07) for zero graph value.
    "workable_writeback_failed",
    "workable_writeback_skipped",
}


def build_event_episode(event: CandidateApplicationEvent) -> Episode | None:
    """Workable / pipeline events as a single episode.

    Captures the recruiter-meaningful signals: stage transitions
    (advanced, invited, hired), outcome changes (offered, hired,
    rejected), Workable disqualifications, and any free-text reason
    the recruiter or system wrote. Skips system-bookkeeping types
    (``pipeline_initialized``, ``cv_scored``) because they have no
    extractable facts.
    """
    if event.event_type in _NOISE_EVENT_TYPES:
        return None
    if not event.application:
        return None
    candidate = event.application.candidate
    if candidate is None:
        return None
    org = int(event.application.organization_id or 0)
    if org <= 0:
        return None

    note = (getattr(event, "reason", None) or "").strip()
    body_lines = [
        _candidate_subject_header(candidate),
        f"Pipeline event: {event.event_type}",
    ]
    from_stage = getattr(event, "from_stage", None) or None
    to_stage = getattr(event, "to_stage", None) or None
    if (from_stage or to_stage) and from_stage != to_stage:
        body_lines.append(
            f"Pipeline stage: {from_stage or '(none)'} → {to_stage or '(none)'}"
        )
    from_outcome = getattr(event, "from_outcome", None) or None
    to_outcome = getattr(event, "to_outcome", None) or None
    if (from_outcome or to_outcome) and from_outcome != to_outcome:
        body_lines.append(
            f"Application outcome: {from_outcome or '(none)'} → {to_outcome or '(none)'}"
        )
    if note:
        body_lines.append("")
        body_lines.append(f"Note: {note[:1500]}")

    # Skip if we ended up with just the header + event_type line and no
    # note — pure no-op (e.g. "applied → applied" with no commentary).
    if len(body_lines) <= 2 and not note:
        return None
    return Episode(
        name=f"event-{event.id}",
        body=_bounded_episode_lines(body_lines),
        source_description=f"event.{event.event_type}",
        reference_time=_coerce_datetime(event.created_at, fallback=_now_utc()),
        group_id=graph_client.group_id_for_org(org),
    )


# ---------------------------------------------------------------------------
# Dispatch — turn Episode objects into Graphiti add_episode calls.
# ---------------------------------------------------------------------------


def dispatch(
    episodes: Iterable[Episode],
    *,
    db: Session | None = None,
    bill_organization_id: int | None = None,
    bill_role_id: int | None = None,
    bill_user_id: int | None = None,
    bill_candidate_id: int | None = None,
    bill_trace_id: str | None = None,
    require_hard_admission: bool = False,
    require_role_admission: bool = False,
    raise_on_error: bool = False,
    provider_attempt_callback: Callable[[], bool] | None = None,
    operation_manifest_callback: Callable[[list[Episode]], bool] | None = None,
) -> int:
    """Send episodes to Graphiti. Returns the number successfully sent.

    Graphiti's ``add_episode`` is async; we dispatch via the shared loop.
    Errors on individual episodes are logged but don't abort the batch —
    a partial graph is better than nothing.

    When ``bill_organization_id`` is supplied, the wrapped Graphiti provider
    clients write one ``UsageEvent`` per actual Anthropic/Voyage call.
    Durable outbox callers also set ``require_hard_admission``: every provider
    call then reserves organization credits + role capacity before touching
    the SDK, settles to actual usage, and propagates any provider/metering
    error so the row remains retryable.
    """
    sent = 0
    episode_list = list(episodes)
    for episode in episode_list:
        if isinstance(episode.name, str):
            episode.name = bounded_provider_identifier(episode.name)
        if isinstance(episode.body, str):
            episode.body = bounded_episode_body(episode.body)
        if isinstance(episode.source_description, str):
            episode.source_description = bounded_provider_identifier(
                episode.source_description
            )
    if not graph_client.is_configured():
        if raise_on_error:
            raise RuntimeError("Graphiti runtime is not configured")
        return 0
    if operation_manifest_callback is not None and not bool(
        operation_manifest_callback(episode_list)
    ):
        raise RuntimeError("could not durably record graph operation manifest")
    if not episode_list:
        # An empty immutable manifest proves this operation intentionally made
        # no provider call. Do not load Graphiti or consume admission capacity.
        return 0
    import_error_code: str | None = None
    try:
        episode_text_source = _episode_text_source()
    except Exception as exc:
        import_error_code = safe_provider_error_code(
            exc, operation="graphiti_runtime_import"
        )
        logger.warning("graphiti_core not importable error_code=%s", import_error_code)
    if import_error_code is not None:
        if raise_on_error:
            raise GraphProviderRuntimeError(import_error_code)
        return 0

    client_error_code: str | None = None
    try:
        graphiti = graph_client.get_graphiti()
    except Exception as exc:
        client_error_code = safe_provider_error_code(
            exc, operation="graphiti_client_init"
        )
        logger.warning("Graphiti client init failed error_code=%s", client_error_code)
    if client_error_code is not None:
        if raise_on_error:
            raise GraphProviderRuntimeError(client_error_code)
        return 0
    # Import here so the module loads cleanly even when Graphiti / our
    # async wrapper aren't configured (test environments).
    from ..services.metered_async_anthropic_client import (
        GraphMeteringContext,
        graph_metering_ctx,
    )

    if require_hard_admission and bill_organization_id is None:
        raise ValueError("hard-admitted Graphiti dispatch requires organization")
    if require_role_admission and (
        bill_organization_id is None or bill_role_id is None
    ):
        raise ValueError(
            "hard-admitted Graphiti dispatch requires organization and role"
        )

    for episode in episode_list:
        # Populate the contextvar BEFORE invoking add_episode so the
        # metered async wrapper around Graphiti's LLM client picks up
        # org/role/candidate attribution for every claude_call_log row
        # it writes. Without this, the call_log row still lands (so
        # reconciliation against Anthropic billing closes) but it
        # carries no org_id and no usage_event is written — surfaced
        # as a metering-attribution gap in the dashboard.
        meter_ctx_token = None
        if bill_organization_id is not None:
            meter_ctx_token = graph_metering_ctx.set(
                GraphMeteringContext(
                    organization_id=int(bill_organization_id),
                    role_id=bill_role_id,
                    candidate_id=bill_candidate_id,
                    user_id=bill_user_id,
                    episode_name=episode.name,
                    trace_id=bill_trace_id or f"graphiti:{episode.name}",
                    require_hard_admission=bool(require_hard_admission),
                    require_role_admission=bool(require_role_admission),
                    provider_attempt_callback=provider_attempt_callback,
                )
            )
        episode_error_code: str | None = None
        try:
            graph_client.run_async(
                graphiti.add_episode(
                    name=episode.name,
                    episode_body=episode.body,
                    source=episode_text_source,
                    source_description=episode.source_description,
                    reference_time=episode.reference_time,
                    group_id=episode.group_id,
                ),
                timeout=120.0,
            )
            sent += 1
            # NOTE: the heuristic ``billing.record_episode_cost`` write
            # is gone — the metered async wrapper now writes a
            # claude_call_log row PER actual Anthropic call with real
            # tokens, and writes a usage_event when the contextvar is
            # populated. Heuristic estimates (len(body)//4 + 800)
            # massively under-counted Graphiti's actual prompt overhead
            # (typically 15-30k tokens/call) — see #237 / 2026-05-23
            # reconciliation. Real tokens via the wrapper supersede.
        except Exception as exc:
            episode_error_code = safe_provider_error_code(
                exc, operation="graphiti_add_episode"
            )
            logger.warning(
                "Graphiti add_episode failed name=%s error_code=%s",
                episode.name,
                episode_error_code,
            )
        finally:
            if meter_ctx_token is not None:
                graph_metering_ctx.reset(meter_ctx_token)
        if episode_error_code is not None and raise_on_error:
            raise GraphProviderRuntimeError(episode_error_code)
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
    for entry in islice(
        candidate.experience_entries or [],
        _MAX_STRUCTURED_SOURCE_ITEMS,
    ):
        if not isinstance(entry, dict):
            continue
        company = _source_text(entry.get("company"), max_chars=2000)
        if not company:
            continue
        start = _source_text(entry.get("start_date"), max_chars=100)
        key = (company.lower(), start)
        if key in seen:
            continue
        seen.add(key)
        yield {
            "company": company,
            "title": _source_text(entry.get("title"), max_chars=2000),
            "start_date": start,
            "end_date": _source_text(entry.get("end_date"), max_chars=100),
            "location": _source_text(entry.get("location"), max_chars=2000),
            "summary": _source_text(entry.get("summary"), max_chars=2000),
            "industry": _source_text(entry.get("industry"), max_chars=2000),
        }
    cv_sections = candidate.cv_sections or {}
    if isinstance(cv_sections, dict):
        for entry in islice(
            cv_sections.get("experience") or [],
            _MAX_STRUCTURED_SOURCE_ITEMS,
        ):
            if not isinstance(entry, dict):
                continue
            company = _source_text(entry.get("company"), max_chars=2000)
            if not company:
                continue
            start = _source_text(entry.get("start"), max_chars=100)
            key = (company.lower(), start)
            if key in seen:
                continue
            seen.add(key)
            bullets = entry.get("bullets") or []
            yield {
                "company": company,
                "title": _source_text(entry.get("title"), max_chars=2000),
                "start_date": start,
                "end_date": _source_text(entry.get("end"), max_chars=100),
                "location": _source_text(entry.get("location"), max_chars=2000),
                "summary": _source_lines_text(bullets, max_chars=2000),
                "industry": "",
            }


def _iter_educations(candidate: Candidate) -> Iterable[dict]:
    seen: set[str] = set()
    for entry in islice(
        candidate.education_entries or [],
        _MAX_STRUCTURED_SOURCE_ITEMS,
    ):
        if not isinstance(entry, dict):
            continue
        institution = _source_text(
            entry.get("school") or entry.get("institution"),
            max_chars=2000,
        )
        if not institution or institution.lower() in seen:
            continue
        seen.add(institution.lower())
        yield {
            "institution": institution,
            "degree": _source_text(entry.get("degree"), max_chars=2000),
            "field": _source_text(
                entry.get("field_of_study") or entry.get("field"),
                max_chars=2000,
            ),
            "start": _source_text(entry.get("start_date"), max_chars=100),
            "end": _source_text(entry.get("end_date"), max_chars=100),
        }
    cv_sections = candidate.cv_sections or {}
    if isinstance(cv_sections, dict):
        for entry in islice(
            cv_sections.get("education") or [],
            _MAX_STRUCTURED_SOURCE_ITEMS,
        ):
            if not isinstance(entry, dict):
                continue
            institution = _source_text(
                entry.get("institution") or entry.get("school"),
                max_chars=2000,
            )
            if not institution or institution.lower() in seen:
                continue
            seen.add(institution.lower())
            yield {
                "institution": institution,
                "degree": _source_text(entry.get("degree"), max_chars=2000),
                "field": _source_text(entry.get("field"), max_chars=2000),
                "start": _source_text(entry.get("start"), max_chars=100),
                "end": _source_text(entry.get("end"), max_chars=100),
            }


def _collect_skills(candidate: Candidate, *, limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for source in (
        candidate.skills,
        (candidate.cv_sections or {}).get("skills")
        if isinstance(candidate.cv_sections, dict)
        else None,
    ):
        if not isinstance(source, list):
            continue
        for item in islice(source, _MAX_STRUCTURED_SOURCE_ITEMS):
            value = _source_text(item, max_chars=600)
            if value and value.lower() not in seen:
                seen.add(value.lower())
                out.append(value)
                if len(out) >= limit:
                    return out
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
    return _bounded_episode_lines(lines)


def _format_speakers(speakers: Any) -> str:
    if not isinstance(speakers, list):
        return ""
    names = []
    for s in islice(speakers, _MAX_STRUCTURED_SOURCE_ITEMS):
        if isinstance(s, dict):
            name = s.get("name") or s.get("speaker") or s.get("label")
            if name:
                name_text = _source_text(name, max_chars=600)
                if name_text:
                    names.append(name_text)
        elif isinstance(s, str):
            names.append(s[:600])
        if len(names) >= 10:
            break
    return ", ".join(names)


def _safe_slug(value: str) -> str:
    cleaned = "".join(c if c.isalnum() else "-" for c in (value or "")).strip("-").lower()
    return cleaned[:60] or "x"
