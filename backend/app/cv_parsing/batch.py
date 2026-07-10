"""Bulk CV parsing via the Anthropic Message Batches API.

The per-application parse (``parse_application_cv_sections``) is
background work nobody is waiting on — the candidate page falls back to
raw CV text until sections land. That makes it the purest fit for the
Batches API's 50% discount: same model, same prompt, same forced
tool-use schema, same cache; only the API surface differs.

Flow (both halves are Celery beat tasks in ``tasks/anthropic_batch_tasks``):

* :func:`sweep_pending_applications` — find applications with raw
  ``cv_text`` but null ``cv_sections`` that aren't already in an open
  batch, apply parse-cache hits inline (free), and submit the rest as
  one Message Batch per organization. Single-org batches only: a batch
  runs on ONE API key, and per-org workspace keys are enabled in prod.
* :func:`apply_batch_results` — for an ended batch, validate each result
  against the same ``ParsedCVSections`` schema the sync path uses, write
  ``cv_sections`` + the parse cache, and hand validation/API failures to
  the live per-application task (which owns retry + deterministic-failure
  caching).

Metering is NOT handled here — ``MeteredAnthropicClient`` intercepts
``messages.batches.create`` / ``.results`` and writes claude_call_log +
usage_events rows at ``service_tier="batch"`` (50% pricing) itself.

Requests are rendered bit-identically to the sync path (same prompt
builder, same ``structured_tool_params`` tool spec, same 30KB truncation)
so results are interchangeable and cache keys match.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..llm import (
    ValidationFailure,
    extract_structured_tool_input,
    structured_tool_params,
)
from . import MODEL_VERSION, PROMPT_VERSION
from .prompts import build_cv_parse_prompt
from .runner import (
    CV_TEXT_CEILING,
    OUTPUT_TOKEN_CEILING,
    TEMPERATURE,
    _SYSTEM_PROMPT,
)
from .schemas import ParsedCV, ParsedCVSections

logger = logging.getLogger("taali.cv_parsing.batch")

CUSTOM_ID_PREFIX = "cvparse"

# Cap on requests per sweep. Well under Anthropic's 100K-request /
# 256MB batch limits; leftovers are picked up by the next sweep.
DEFAULT_SWEEP_LIMIT = 500


def custom_id_for(application_id: int) -> str:
    return f"{CUSTOM_ID_PREFIX}-{int(application_id)}"


def application_id_from(custom_id: str) -> Optional[int]:
    prefix = f"{CUSTOM_ID_PREFIX}-"
    if not (custom_id or "").startswith(prefix):
        return None
    try:
        return int(custom_id[len(prefix):])
    except ValueError:
        return None


def _effective_cv_text(app: Any) -> str:
    """The text the sync path would parse: application text, falling back
    to the candidate's, truncated exactly like ``parse_cv`` so cache keys
    computed here match the sync path's."""
    cv_text = (getattr(app, "cv_text", "") or "").strip()
    if not cv_text:
        candidate = getattr(app, "candidate", None)
        if candidate is not None:
            cv_text = (getattr(candidate, "cv_text", "") or "").strip()
    return cv_text[:CV_TEXT_CEILING]


def build_cv_parse_request(application_id: int, cv_text: str) -> Optional[dict]:
    """Render one batch request with params bit-identical to the sync call.

    Returns None when the prompt can't be rendered — the caller caches
    that as a deterministic failure so the sweep stops re-picking the row.
    """
    try:
        prompt = build_cv_parse_prompt(cv_text)
    except Exception as exc:
        logger.warning(
            "batch prompt render failed application_id=%s: %s", application_id, exc
        )
        return None

    tools, tool_choice, _ = structured_tool_params(ParsedCVSections)
    return {
        "custom_id": custom_id_for(application_id),
        "params": {
            "model": MODEL_VERSION,
            "max_tokens": OUTPUT_TOKEN_CEILING,
            "temperature": TEMPERATURE,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
            "tools": tools,
            "tool_choice": tool_choice,
        },
    }


def in_flight_application_ids(db: Any) -> set[int]:
    """Application ids already sitting in an open cv_parse batch, so the
    sweep doesn't resubmit them every tick while Anthropic processes."""
    from ..models.anthropic_batch_job import AnthropicBatchJob

    ids: set[int] = set()
    rows = (
        db.query(AnthropicBatchJob)
        .filter(
            AnthropicBatchJob.feature == "cv_parse",
            AnthropicBatchJob.status == "submitted",
        )
        .all()
    )
    for row in rows:
        for custom_id in (row.context or {}):
            app_id = application_id_from(custom_id)
            if app_id is not None:
                ids.add(app_id)
    return ids


def sweep_pending_applications(
    db: Any, *, limit: int = DEFAULT_SWEEP_LIMIT
) -> dict:
    """Find parse-pending applications and submit per-org batches.

    Per application: cache hit (success) → applied inline, no API call;
    cache hit (deterministic failure) → skipped, nothing would change;
    prompt-render failure → failure cached so the sweep stops re-picking;
    otherwise → bundled into that org's batch request list.

    The caller owns the transaction (commits applied cache hits).
    """
    from sqlalchemy.orm import joinedload

    from ..models.candidate_application import CandidateApplication
    from ..services.claude_client_resolver import get_metered_client
    from ..services.pricing_service import Feature
    from . import cache as cache_module
    from .apply import parse_and_store_cv_sections

    in_flight = in_flight_application_ids(db)

    apps = (
        db.query(CandidateApplication)
        .options(joinedload(CandidateApplication.candidate))
        .filter(
            CandidateApplication.cv_sections.is_(None),
            CandidateApplication.cv_text.isnot(None),
            CandidateApplication.cv_text != "",
            CandidateApplication.deleted_at.is_(None),
        )
        .order_by(CandidateApplication.id.desc())
        .limit(limit)
        .all()
    )

    summary = {
        "candidates": len(apps),
        "in_flight": 0,
        "cache_applied": 0,
        "cache_failed_skip": 0,
        "render_failed": 0,
        "batches": [],
    }
    requests_by_org: dict[int, list[dict]] = {}
    context_by_org: dict[int, dict[str, dict]] = {}

    for app in apps:
        if app.id in in_flight:
            summary["in_flight"] += 1
            continue

        cv_text = _effective_cv_text(app)
        if not cv_text:
            continue
        cache_key = cache_module.compute_cache_key(
            cv_text=cv_text,
            prompt_version=PROMPT_VERSION,
            model_version=MODEL_VERSION,
        )
        cached = cache_module.get(cache_key)
        if cached is not None:
            if cached.parse_failed:
                # Deterministic failure already cached — re-parsing the
                # same text re-fails; leave the row for a version bump.
                summary["cache_failed_skip"] += 1
            else:
                parse_and_store_cv_sections(app, db=db)
                summary["cache_applied"] += 1
            continue

        request = build_cv_parse_request(app.id, cv_text)
        if request is None:
            # Same deterministic-failure caching the sync path does, so
            # the sweep stops re-picking this row.
            try:
                cache_module.set(
                    cache_key,
                    ParsedCV.failed(
                        reason="prompt_render_failed: batch request build",
                        prompt_version=PROMPT_VERSION,
                        model_version=MODEL_VERSION,
                    ),
                )
            except Exception:  # pragma: no cover — defensive
                logger.exception(
                    "batch render-failure cache write failed app_id=%s", app.id
                )
            summary["render_failed"] += 1
            continue

        org_id = int(app.organization_id)
        requests_by_org.setdefault(org_id, []).append(request)
        context_by_org.setdefault(org_id, {})[request["custom_id"]] = {
            "organization_id": org_id,
            "role_id": app.role_id,
            "entity_id": f"application:{app.id}",
        }

    for org_id, requests in requests_by_org.items():
        try:
            client = get_metered_client(organization_id=org_id)
            batch = client.messages.batches.create(
                requests=requests,
                metering={
                    "feature": Feature.CV_PARSE,
                    "organization_id": org_id,
                    "by_custom_id": context_by_org[org_id],
                },
            )
            summary["batches"].append(
                {
                    "batch_id": str(getattr(batch, "id", "")),
                    "organization_id": org_id,
                    "requests": len(requests),
                }
            )
            logger.info(
                "cv_parse batch submitted org=%s batch_id=%s requests=%d",
                org_id,
                getattr(batch, "id", None),
                len(requests),
            )
        except Exception:
            # Leave these rows pending — the next sweep retries them.
            logger.exception(
                "cv_parse batch submission failed org=%s (%d requests)",
                org_id,
                len(requests),
            )

    return summary


def apply_batch_results(db: Any, entries: Any) -> dict:
    """Apply one ended batch's results to application rows.

    Succeeded + valid → ``cv_sections`` written and the parse cache
    populated (so sibling applications with the same CV text hit it).
    Validation failures and errored/expired/canceled entries are handed
    to the live per-application task, which owns the retry-once and
    deterministic-failure-caching semantics. The caller commits.
    """
    from sqlalchemy.orm import joinedload

    from ..models.candidate_application import CandidateApplication
    from ..tasks.automation_tasks import parse_application_cv_sections
    from . import cache as cache_module
    from .apply import store_parsed_cv_sections

    _, _, tool_name = structured_tool_params(ParsedCVSections)
    summary = {"applied": 0, "requeued": 0, "skipped": 0}

    for entry in entries:
        app_id = application_id_from(str(getattr(entry, "custom_id", "")))
        if app_id is None:
            summary["skipped"] += 1
            continue
        app = (
            db.query(CandidateApplication)
            .options(joinedload(CandidateApplication.candidate))
            .filter(CandidateApplication.id == app_id)
            .first()
        )
        if app is None or app.cv_sections is not None:
            summary["skipped"] += 1
            continue

        result = getattr(entry, "result", None)
        if getattr(result, "type", None) != "succeeded":
            # Request-level failure (errored/expired/canceled). Hand to the
            # live task — it retries and caches deterministic failures.
            _requeue_live(parse_application_cv_sections, app_id)
            summary["requeued"] += 1
            continue

        cv_text = _effective_cv_text(app)
        try:
            sections = extract_structured_tool_input(
                result.message, ParsedCVSections, tool_name=tool_name
            )
        except ValidationFailure as exc:
            # The sync path retries validation failures once with the error
            # folded into the prompt; the live task provides exactly that.
            logger.info(
                "cv_parse batch validation failed app_id=%s: %s", app_id, exc
            )
            _requeue_live(parse_application_cv_sections, app_id)
            summary["requeued"] += 1
            continue

        parsed = ParsedCV.from_sections(
            sections,
            prompt_version=PROMPT_VERSION,
            model_version=MODEL_VERSION,
        )
        try:
            cache_module.set(
                cache_module.compute_cache_key(
                    cv_text=cv_text,
                    prompt_version=PROMPT_VERSION,
                    model_version=MODEL_VERSION,
                ),
                parsed,
            )
        except Exception:  # pragma: no cover — defensive
            logger.exception("cv_parse batch cache write failed app_id=%s", app_id)
        store_parsed_cv_sections(app, parsed=parsed, cv_text=cv_text)
        summary["applied"] += 1

    return summary


def _requeue_live(task: Any, application_id: int) -> None:
    """Enqueue the live per-application parse; never raises (a requeue
    failure just leaves the row for a later sweep)."""
    try:
        task.delay(application_id)
    except Exception:  # pragma: no cover — defensive
        logger.exception("cv_parse live requeue failed app_id=%s", application_id)
