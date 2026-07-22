"""Routed model verification for soft NL-search criteria.

When a query carries qualitative phrases ("large enterprise", "in
production") that the SQL layer can't decide, this module asks a routed model
which of the SQL-passing candidates actually match. Each candidate is
sent with a compact summary plus their graph neighbourhood (top
companies, schools, skills, colleagues) so the model can reason about
employer scale, peer cohort, etc.

One logical route per candidate. The caller bounds the input set
(``RERANK_TOP_N`` in runner.py), while the task profile owns deployment,
iteration, output, and cost ceilings.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from ..components.ai_routing import (
    RoutingAttribution,
    TaskKey,
    estimate_anthropic_messages,
    prepare_route,
    routed_messages_client,
)
from ..llm import CallUsage, MeteringContext, one_call, strip_json_fences
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..services.pricing_service import Feature
from ..services.metered_async_anthropic_client import GraphProviderAdmissionError
from .metering import search_metering

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


def _build_candidate_summary(
    candidate: Candidate, application: CandidateApplication
) -> dict:
    """Compact model context, token-bounded by truncation."""
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
    *,
    organization_id: int,
    candidate_id: int,
    role_id: int | None = None,
    require_role_authority: bool = False,
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
            require_role_authority=bool(require_role_authority),
        )
    except GraphProviderAdmissionError:
        raise
    except Exception as exc:
        logger.debug(
            "Graph context unavailable for candidate=%s: %s", candidate_id, exc
        )
        return None


_SYSTEM_PROMPT = (
    "You are a recruiter's qualitative filter. Given soft criteria and one "
    "candidate's profile (with optional graph neighbourhood), decide if the "
    "candidate clearly matches ALL the criteria.\n\n"
    'Respond with ONLY this JSON: {"match": true|false, "reason": "<short>"}.\n'
    "- 'match' is true ONLY when every soft criterion has supporting evidence.\n"
    "- 'reason' is one sentence, ≤25 words, citing the strongest evidence (or absence).\n"
    "- Do not invent facts. If evidence is absent, set match=false.\n"
)


def _shared_prefix_text(soft_criteria: list[str]) -> str:
    """Build the per-query shared block: soft criteria + decision rules.

    Identical for every candidate in a rerank query, so we mark it
    ``cache_control=ephemeral`` so Anthropic can serve it at the cheap
    cache-read rate on candidates 2..N.

    Short queries may not meet the selected deployment's minimum cacheable
    prefix. The structural cost is zero, so we mark it unconditionally and let
    the provider decide.
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
    model: str,
    usage: CallUsage | None = None,
    metering: dict | None = None,
    messages: list[dict] | None = None,
) -> _EvaluationResult:
    """Return a completed positive/negative decision or an explicit error.

    ``usage`` (optional) accumulates
    ``input_tokens`` / ``output_tokens`` / ``cache_read_tokens`` /
    ``cache_creation_tokens`` across all candidates in a rerank batch,
    so the caller can observe whether prompt caching is actually
    materialising for this query.
    """
    messages = messages or [
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
    if not metering or metering.get("organization_id") is None:
        return _EvaluationResult(status="error", error_code="metering_unavailable")
    try:
        response = one_call(
            client,
            model=model,
            max_tokens=RERANK_MAX_TOKENS,
            temperature=RERANK_TEMPERATURE,
            system=_SYSTEM_PROMPT,
            messages=messages,
            metering=MeteringContext.from_dict(
                metering,
                default_feature=Feature.CV_RERANK,
            ),
            usage_sink=usage,
        )
    except Exception as exc:
        logger.debug("Rerank model call failed: %s", exc)
        return _EvaluationResult(status="error", error_code="model_call_failed")

    try:
        raw = response.content[0].text  # type: ignore[attr-defined]
        text = strip_json_fences(str(raw or ""))
        decision = json.loads(text)
        if not isinstance(decision, dict) or not isinstance(
            decision.get("match"), bool
        ):
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
    route_client_factory=None,
    require_role_authority: bool = False,
) -> RerankBatchResult:
    """Tri-state verification for ``application_ids``.

    Order is preserved. Definitive non-matches are filtered out; verification
    errors remain in ``application_ids`` and carry ``status=error`` so callers
    can render them as unclassified instead of silently treating them as failed.
    """
    if not application_ids or not soft_criteria:
        return RerankBatchResult(application_ids=list(application_ids), outcomes=[])

    apps = (
        db.query(CandidateApplication)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.id.in_(application_ids),
        )
        .all()
    )
    by_id = {int(a.id): a for a in apps}

    usage = CallUsage()
    kept: list[int] = []
    outcomes: list[CandidateRerankOutcome] = []
    for app_id in application_ids:
        application = by_id.get(int(app_id))
        if application is None or application.candidate is None:
            kept.append(int(app_id))
            outcomes.append(
                CandidateRerankOutcome(
                    application_id=int(app_id),
                    status="error",
                    error_code="candidate_unavailable",
                )
            )
            continue
        execution = None
        evaluation: _EvaluationResult | None = None
        try:
            candidate = application.candidate
            summary = _build_candidate_summary(candidate, application)
            graph = _build_graph_context(
                organization_id=organization_id,
                candidate_id=int(candidate.id),
                role_id=role_id,
                require_role_authority=bool(require_role_authority),
            )
            evaluation_messages = [
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
            execution = prepare_route(
                TaskKey.SEARCH_RERANK,
                request_estimate=estimate_anthropic_messages(
                    system=_SYSTEM_PROMPT,
                    messages=evaluation_messages,
                    max_tokens=RERANK_MAX_TOKENS,
                ),
                attribution=RoutingAttribution(
                    organization_id=int(organization_id),
                    role_id=int(role_id) if role_id is not None else None,
                    entity_id=f"application:{app_id}",
                ),
                operation="candidate_search.rerank_candidate",
                require_role_authority=bool(require_role_authority),
            )
            call_metering = search_metering(
                organization_id=organization_id,
                role_id=role_id,
                feature=Feature.CV_RERANK,
                entity_id=f"application:{app_id}",
                sub_feature="candidate_search_rerank",
                trace_id=f"candidate-search:rerank:application:{app_id}",
                base_metering={"db": db},
                require_role_authority=bool(require_role_authority),
            )
            evaluation = _evaluate_one(
                soft_criteria=soft_criteria,
                summary=summary,
                graph=graph,
                client=(route_client_factory or routed_messages_client)(execution),
                model=execution.selected_model_id,
                usage=usage,
                metering=call_metering,
                messages=evaluation_messages,
            )
        except Exception as exc:  # one admission/profile failure must stay local
            logger.debug("Rerank setup failed for app=%s: %s", app_id, exc)
            evaluation = _EvaluationResult(
                status="error", error_code="verification_setup_failed"
            )
        finally:
            if execution is not None:
                execution.finish_workflow(
                    succeeded=(evaluation is not None and evaluation.status != "error")
                )

        assert evaluation is not None

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

    if any(
        (
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_tokens,
            usage.cache_creation_tokens,
        )
    ):
        cached = usage.cache_read_tokens
        written = usage.cache_creation_tokens
        billed = usage.input_tokens
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
