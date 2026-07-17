"""Claude rerank for soft NL-search criteria.

When a query carries qualitative phrases ("large enterprise", "in
production") that the SQL layer can't decide, this module asks Claude
which of the SQL-passing candidates actually match. Each candidate is
sent with a compact summary plus their graph neighbourhood (top
companies, schools, skills, colleagues) so the model can reason about
employer scale, peer cohort, etc.

Single Claude call PER CANDIDATE in v1. Caller bounds the input set
(``RERANK_TOP_N`` in runner.py). Each call is ~$0.001 at Haiku rates;
for a 50-candidate rerank, that's ~$0.05 worst-case per query.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from ..llm import strip_json_fences
from . import MODEL_VERSION
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..services.pricing_service import Feature
from .metering import admitted_search_metering

logger = logging.getLogger("taali.candidate_search.rerank")

RERANK_MAX_TOKENS = 256
RERANK_TEMPERATURE = 0.0


class RerankUnavailable(RuntimeError):
    """The requested evidence pass could not start, so coverage stays zero."""


@dataclass(frozen=True)
class CandidateRerankOutcome:
    """One candidate's tri-state verifier result.

    ``qualified`` and ``not_qualified`` are completed model decisions. ``error``
    is deliberately neither: transport, metering, and response-shape failures
    must stay visible instead of being collapsed into a negative hiring signal.
    """

    application_id: int
    status: Literal["qualified", "not_qualified", "error"]
    reason: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class RerankBatchResult:
    """Retained result ids plus the outcome for every attempted candidate."""

    application_ids: list[int]
    outcomes: list[CandidateRerankOutcome]

    @property
    def evidence_succeeded(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status != "error")

    @property
    def evidence_failed(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == "error")

    @property
    def qualified(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == "qualified")


@dataclass(frozen=True)
class _EvaluationResult:
    status: Literal["qualified", "not_qualified", "error"]
    reason: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class _CandidateRerankInput:
    """Provider-safe candidate evidence detached from the ORM session."""

    application_id: int
    candidate_id: int
    summary: dict


def _resolve_anthropic_client(*, organization_id: int | None = None):
    """Build a metered Anthropic client. ``organization_id`` is bound at
    construction so every rerank call records to the right org without
    each call repeating it."""
    from ..services.claude_client_resolver import get_metered_client

    return get_metered_client(organization_id=organization_id)


def _build_candidate_summary(candidate: Candidate, application: CandidateApplication) -> dict:
    """Compact summary used as Claude prompt context. Token-bounded by truncation."""
    cv_sections = candidate.cv_sections or {}
    skills_top = (cv_sections.get("skills") or candidate.skills or [])[:30]
    experience = (cv_sections.get("experience") or [])[:6]
    return {
        "headline": (candidate.headline or "")[:160],
        "summary": (cv_sections.get("summary") or candidate.summary or "")[:600],
        "skills_top": [str(s)[:60] for s in skills_top],
        "experience_top": [
            {
                "company": str(e.get("company", ""))[:80],
                "title": str(e.get("title", ""))[:80],
                "start": str(e.get("start") or e.get("start_date", ""))[:16],
                "end": str(e.get("end") or e.get("end_date", ""))[:16],
            }
            for e in experience
            if isinstance(e, dict)
        ],
        "current_country": (candidate.location_country or "")[:80],
        "cv_match_score": application.cv_match_score,
    }


def _build_graph_context(
    *, organization_id: int, candidate_id: int, role_id: int | None = None
) -> dict | None:
    """Pull a compact graph neighbourhood from Neo4j (or None when unavailable)."""
    try:
        from ..candidate_graph import client as graph_client

        if not graph_client.is_configured():
            return None
        from ..candidate_graph import search as graph_search

        return graph_search.colleague_neighbourhood(
            organization_id=organization_id,
            candidate_id=candidate_id,
            role_id=role_id,
        )
    except Exception as exc:
        logger.debug("Graph context unavailable for candidate=%s: %s", candidate_id, exc)
        return None


_SYSTEM_PROMPT = (
    "You are a recruiter's qualitative filter. Given soft criteria and one "
    "candidate's profile (with optional graph neighbourhood), decide if the "
    "candidate clearly matches ALL the criteria.\n\n"
    "Respond with ONLY this JSON: {\"match\": true|false, \"reason\": \"<short>\"}.\n"
    "- 'match' is true ONLY when every soft criterion has supporting evidence.\n"
    "- 'reason' is one sentence, ≤25 words, citing the strongest evidence (or absence).\n"
    "- Do not invent facts. If evidence is absent, set match=false.\n"
)


def _shared_prefix_text(soft_criteria: list[str]) -> str:
    """Build the per-query shared block: soft criteria + decision rules.

    Identical for every candidate in a rerank query, so we mark it
    ``cache_control=ephemeral`` so Anthropic can serve it at the cheap
    cache-read rate on candidates 2..N.

    Note: the cache only materializes when the cached prefix exceeds the
    Haiku 4.5 minimum (~4096 tokens). Short queries (1-2 brief criteria)
    won't actually cache — but the structural cost is zero, so we mark it
    unconditionally and let Anthropic decide.
    """
    rendered_criteria = "\n".join(f"- {c}" for c in soft_criteria) or "(none)"
    return (
        "=== SOFT CRITERIA (shared across candidates in this query) ===\n"
        f"{rendered_criteria}\n\n"
        "=== DECISION RULES ===\n"
        "Match a soft criterion only when the candidate's profile or graph\n"
        "neighbourhood provides positive supporting evidence. Absence of\n"
        "evidence is NOT evidence of absence — but it IS sufficient grounds\n"
        "for match=false (we are filtering for confident matches, not\n"
        "borderline cases). Cite the strongest evidence (or absence) in\n"
        "the reason. Keep reason under 25 words.\n"
    )


def _candidate_block_text(summary: dict, graph: dict | None) -> str:
    """Per-candidate user block — the only thing that changes per call."""
    payload: dict = {"candidate": summary}
    if graph is not None:
        payload["graph_neighbourhood"] = graph
    return (
        "=== CANDIDATE TO EVALUATE ===\n"
        f"{json.dumps(payload, separators=(',', ':'))[:6000]}\n\n"
        "Return only the JSON match decision."
    )


def _evaluate_one(
    *,
    soft_criteria: list[str],
    summary: dict,
    graph: dict | None,
    client,
    metrics: dict | None = None,
    metering: dict | None = None,
) -> _EvaluationResult:
    """Return a completed positive/negative decision or an explicit error.

    ``metrics`` (optional) is mutated in place to accumulate
    ``input_tokens`` / ``output_tokens`` / ``cache_read_tokens`` /
    ``cache_creation_tokens`` across all candidates in a rerank batch,
    so the caller can observe whether prompt caching is actually
    materialising for this query.
    """
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": _shared_prefix_text(soft_criteria),
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": _candidate_block_text(summary, graph),
                },
            ],
        }
    ]
    if not metering or not metering.get("credit_reservation"):
        return _EvaluationResult(status="error", error_code="metering_unavailable")
    try:
        response = client.messages.create(
            model=MODEL_VERSION,
            max_tokens=RERANK_MAX_TOKENS,
            temperature=RERANK_TEMPERATURE,
            system=_SYSTEM_PROMPT,
            messages=messages,
            metering=metering,
        )
        if metrics is not None:
            usage = getattr(response, "usage", None)
            if usage is not None:
                metrics["input_tokens"] = metrics.get("input_tokens", 0) + int(
                    getattr(usage, "input_tokens", 0) or 0
                )
                metrics["output_tokens"] = metrics.get("output_tokens", 0) + int(
                    getattr(usage, "output_tokens", 0) or 0
                )
                metrics["cache_read_tokens"] = metrics.get("cache_read_tokens", 0) + int(
                    getattr(usage, "cache_read_input_tokens", 0) or 0
                )
                metrics["cache_creation_tokens"] = metrics.get(
                    "cache_creation_tokens", 0
                ) + int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    except Exception as exc:
        logger.debug("Rerank model call failed: %s", exc)
        return _EvaluationResult(status="error", error_code="model_call_failed")

    try:
        raw = response.content[0].text  # type: ignore[attr-defined]
        text = strip_json_fences(str(raw or ""))
        decision = json.loads(text)
        if not isinstance(decision, dict) or not isinstance(decision.get("match"), bool):
            raise ValueError("match must be a JSON boolean")
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        logger.debug("Rerank response invalid: %s", exc)
        return _EvaluationResult(status="error", error_code="invalid_model_response")

    reason = decision.get("reason")
    clean_reason = str(reason).strip()[:300] if reason is not None else None
    return _EvaluationResult(
        status="qualified" if decision["match"] else "not_qualified",
        reason=clean_reason or None,
    )


def rerank_application_ids(
    *,
    db: Session,
    organization_id: int,
    role_id: int | None = None,
    application_ids: list[int],
    soft_criteria: list[str],
    client=None,
) -> RerankBatchResult:
    """Tri-state verification for ``application_ids``.

    Order is preserved. Definitive non-matches are filtered out; verification
    errors remain in ``application_ids`` and carry ``status=error`` so callers
    can render them as unclassified instead of silently treating them as failed.
    """
    if not application_ids or not soft_criteria:
        return RerankBatchResult(application_ids=list(application_ids), outcomes=[])

    if client is None:
        try:
            client = _resolve_anthropic_client(organization_id=organization_id)
        except Exception as exc:
            logger.warning("Rerank client init failed; skipping rerank: %s", exc)
            # Let the runner retain the deterministic database matches while
            # reporting rerank_applied=false/deep_checked=0. Returning the
            # untouched ids here used to make the caller falsely claim that a
            # complete evidence pass qualified every candidate.
            raise RerankUnavailable("candidate evidence verification unavailable") from exc

    apps = (
        db.query(CandidateApplication)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.id.in_(application_ids),
        )
        .all()
    )
    snapshots: dict[int, _CandidateRerankInput] = {}
    snapshot_errors: set[int] = set()
    for application in apps:
        application_id = int(application.id)
        try:
            candidate = application.candidate
            if candidate is None:
                continue
            snapshots[application_id] = _CandidateRerankInput(
                application_id=application_id,
                candidate_id=int(candidate.id),
                summary=_build_candidate_summary(candidate, application),
            )
        except Exception:
            logger.debug(
                "Rerank evidence snapshot failed for app=%s",
                application_id,
                exc_info=True,
            )
            snapshot_errors.add(application_id)

    # This function is a read-only search boundary.  Do not retain its
    # PostgreSQL transaction/connection while Neo4j and Anthropic work runs
    # once per candidate.  Everything used below is an immutable primitive
    # snapshot; metering also persists through independent sessions.
    db.rollback()

    metrics: dict = {}
    kept: list[int] = []
    outcomes: list[CandidateRerankOutcome] = []
    for app_id in application_ids:
        application_id = int(app_id)
        snapshot = snapshots.get(application_id)
        if snapshot is None:
            kept.append(int(app_id))
            outcomes.append(
                CandidateRerankOutcome(
                    application_id=application_id,
                    status="error",
                    error_code=(
                        "verification_setup_failed"
                        if application_id in snapshot_errors
                        else "candidate_unavailable"
                    ),
                )
            )
            continue
        try:
            graph = _build_graph_context(
                organization_id=organization_id,
                candidate_id=snapshot.candidate_id,
                role_id=role_id,
            )
            call_metering = admitted_search_metering(
                organization_id=organization_id,
                role_id=role_id,
                feature=Feature.CV_RERANK,
                entity_id=f"application:{app_id}",
                sub_feature="candidate_search_rerank",
                trace_id=f"candidate-search:rerank:application:{app_id}",
            )
            evaluation = _evaluate_one(
                soft_criteria=soft_criteria,
                summary=snapshot.summary,
                graph=graph,
                client=client,
                metrics=metrics,
                metering=call_metering,
            )
        except Exception as exc:  # one admission/profile failure must stay local
            logger.debug("Rerank setup failed for app=%s: %s", app_id, exc)
            evaluation = _EvaluationResult(
                status="error", error_code="verification_setup_failed"
            )

        outcomes.append(
            CandidateRerankOutcome(
                application_id=int(app_id),
                status=evaluation.status,
                reason=evaluation.reason,
                error_code=evaluation.error_code,
            )
        )
        if evaluation.status != "not_qualified":
            kept.append(int(app_id))

    if metrics:
        cached = metrics.get("cache_read_tokens", 0)
        written = metrics.get("cache_creation_tokens", 0)
        billed = metrics.get("input_tokens", 0)
        total_input = cached + written + billed
        cache_hit_pct = (cached / total_input * 100.0) if total_input else 0.0
        logger.info(
            "Rerank usage: candidates=%d input=%d cache_read=%d cache_write=%d hit_pct=%.1f",
            len(application_ids),
            billed,
            cached,
            written,
            cache_hit_pct,
        )
    return RerankBatchResult(application_ids=kept, outcomes=outcomes)
