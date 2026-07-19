"""Pre-screen runner — single tight LLM call, cached, fast-fails to "maybe".

Cost discipline:
- Model: ``claude-haiku-4-5-20251001`` (same as v3 for consistency).
- Temperature 0, max_tokens 256.
- One call. No retry on JSON parse failure — return decision="error". The
  orchestrator records a retryable score-job error and does not run the costly
  full scorer until pre-screen succeeds.
- Cache key separate from v3 cache: prompt_version is the discriminator.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from ..llm import (
    CallUsage,
    MeteringContext,
    one_call,
    one_call_request,
    strip_json_fences,
)
from ..services.provider_error_evidence import (
    safe_anthropic_error_code,
    safe_provider_error_code,
)
from .holistic_cache_policy import (
    ProtectedWorkableEvidenceOverflow,
    compact_workable_context,
)
from . import MODEL_VERSION
from .prompts_pre_screen import (
    PRE_SCREEN_PROMPT_VERSION,
    build_pre_screen_system,
    build_pre_screen_user_messages,
    pre_screen_requirement_entries,
)
from .schemas import RequirementInput

logger = logging.getLogger("taali.cv_match.pre_screen")

_WORKABLE_CONTEXT_CHARS = 2_500


PreScreenDecision = Literal["yes", "no", "maybe", "error"]


@dataclass
class PreScreenResult:
    decision: PreScreenDecision
    reason: str
    prompt_version: str
    model_version: str
    trace_id: str
    cache_hit: bool
    score: float | None = None  # 0-100 numeric pre-screen score (v2.0+)
    # v2.2: gate flag — candidate leans on an extraordinary, CV-uncorroborated
    # claim (named hackathon win, award, publication). Drives a soft penalty
    # downstream; never a hard reject on its own.
    unverified_claim: bool = False
    # Token usage (populated by the runner, used by usage_metering_service).
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


def _compute_pre_screen_cache_key_from_visible_context(
    *,
    cv_text: str,
    jd_text: str,
    requirements: list[RequirementInput] | None,
    workable_context: str,
) -> str:
    payload = {
        "cv": cv_text or "",
        "jd": jd_text or "",
        "screening_requirements": [
            {"priority": priority, "requirement": requirement}
            for priority, requirement in pre_screen_requirement_entries(requirements)
        ],
        "workable_context": (workable_context or "").strip(),
        "prompt_version": PRE_SCREEN_PROMPT_VERSION,
        "model_version": MODEL_VERSION,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _provider_visible_workable_context(workable_context: str | None) -> str:
    """Apply the one protected-evidence boundary before cache or provider use."""

    return compact_workable_context(
        workable_context,
        max_chars=_WORKABLE_CONTEXT_CHARS,
    )


def compute_pre_screen_cache_key(
    *,
    cv_text: str,
    jd_text: str,
    requirements: list[RequirementInput] | None,
    workable_context: str | None = None,
) -> str:
    """Hash the exact context bytes the provider can observe."""

    return _compute_pre_screen_cache_key_from_visible_context(
        cv_text=cv_text,
        jd_text=jd_text,
        requirements=requirements,
        workable_context=_provider_visible_workable_context(workable_context),
    )


def _normalize_decision(value: str) -> PreScreenDecision:
    label = (value or "").strip().lower()
    if label in {"yes", "no", "maybe"}:
        return label  # type: ignore[return-value]
    return "error"


def _resolve_anthropic_client(*, organization_id: int | None = None):
    # Always return a ``MeteredAnthropicClient``. Paid callers must provide
    # organization metering context; direct no-context calls are limited to
    # injected non-provider test clients.
    #
    # ``organization_id`` flows to the gated resolver (per-org workspace-key
    # routing when ANTHROPIC_WORKSPACE_KEYS_ENABLED; shared key otherwise).
    from ..services.claude_client_resolver import get_metered_client

    return get_metered_client(organization_id=organization_id)


def _cache_get(
    cache_key: str, *, cache_session: Any = None
) -> PreScreenResult | None:
    """Reuse the existing cv_score_cache table (different prompt_version
    keeps it isolated from v3 rows).

    ``cache_session`` lets the application transaction own hit bookkeeping.
    Miss writes remain independently committed so a later application rollback
    does not discard a paid result and cause another provider call.
    """
    try:
        from datetime import datetime, timezone

        from ..models.cv_score_cache import CvScoreCache
    except Exception as exc:
        logger.debug(
            "Pre-screen cache get skipped (no DB) error_type=%s",
            type(exc).__name__,
        )
        return None

    owns_session = cache_session is None
    if owns_session:
        try:
            from ..platform.database import SessionLocal
        except Exception as exc:
            logger.debug(
                "Pre-screen cache get skipped (no DB) error_type=%s",
                type(exc).__name__,
            )
            return None
        session = SessionLocal()
    else:
        session = cache_session
    try:
        query = session.query(CvScoreCache).filter_by(cache_key=cache_key)
        if owns_session:
            row = query.one_or_none()
        else:
            # Cache inspection must not flush tentative application/job state
            # immediately before a provider call on a miss.
            with session.no_autoflush:
                row = query.one_or_none()
        if row is None:
            return None
        result = row.result if isinstance(row.result, dict) else {}
        decision = _normalize_decision(str(result.get("decision") or ""))
        reason = str(result.get("reason") or "")
        raw_score = result.get("score")
        try:
            cached_score: float | None = max(0.0, min(100.0, float(raw_score)))
        except (TypeError, ValueError):
            cached_score = None
        unverified_claim = bool(result.get("unverified_extraordinary_claim") or False)
        row.hit_count = (row.hit_count or 0) + 1
        row.last_hit_at = datetime.now(timezone.utc)
        if owns_session:
            try:
                session.commit()
            except Exception:  # pragma: no cover — defensive
                session.rollback()
        return PreScreenResult(
            decision=decision,
            reason=reason,
            prompt_version=PRE_SCREEN_PROMPT_VERSION,
            model_version=MODEL_VERSION,
            trace_id=str(result.get("trace_id") or ""),
            cache_hit=True,
            score=cached_score,
            unverified_claim=unverified_claim,
        )
    finally:
        if owns_session:
            session.close()


def _cache_set(cache_key: str, result: PreScreenResult, score: float | None = None) -> None:
    if result.decision == "error":
        return  # don't poison the cache with parse failures
    try:
        from ..models.cv_score_cache import CvScoreCache
        from ..platform.database import SessionLocal
    except Exception as exc:
        logger.debug(
            "Pre-screen cache set skipped (no DB) error_type=%s",
            type(exc).__name__,
        )
        return

    session = SessionLocal()
    try:
        existing = session.query(CvScoreCache).filter_by(cache_key=cache_key).one_or_none()
        if existing is not None:
            return
        row = CvScoreCache(
            cache_key=cache_key,
            prompt_version=PRE_SCREEN_PROMPT_VERSION,
            model=MODEL_VERSION,
            score_100=score,
            result={
                "decision": result.decision,
                "score": result.score,
                "reason": result.reason,
                "trace_id": result.trace_id,
                "unverified_extraordinary_claim": result.unverified_claim,
            },
            hit_count=0,
        )
        session.add(row)
        session.commit()
    except Exception as exc:
        logger.warning(
            "Pre-screen cache write failed error_type=%s", type(exc).__name__
        )
        session.rollback()
    finally:
        session.close()


def run_pre_screen(
    cv_text: str,
    jd_text: str,
    requirements: list[RequirementInput] | None = None,
    *,
    client=None,
    skip_cache: bool = False,
    workable_context: str | None = None,
    metering_context: dict | None = None,
    cache_read_session: Any = None,
) -> PreScreenResult:
    """Run the pre-screen LLM call. Never raises.

    Returns a ``PreScreenResult`` with ``decision`` in
    {yes, no, maybe, error}. ``error`` means the call or parse failed —
    the orchestrator should treat it as ``maybe`` so v3 still runs.

    ``workable_context`` is an optional pre-rendered text block carrying
    every Workable surface for the candidate (questionnaire answers,
    recruiter comments, activity log, structured profile). It's threaded
    into the variable per-candidate prompt block so hard constraints
    expressed only in Workable get filtered at pre-screen.
    """
    trace_id = str(uuid.uuid4())
    try:
        prompt_workable_context = _provider_visible_workable_context(workable_context)
    except ProtectedWorkableEvidenceOverflow:
        # Do not inspect a cache produced from incomplete evidence and do not
        # pay for a model call whose hard-constraint corpus cannot be retained.
        return PreScreenResult(
            decision="error",
            reason="protected_workable_evidence_too_large",
            prompt_version=PRE_SCREEN_PROMPT_VERSION,
            model_version=MODEL_VERSION,
            trace_id=trace_id,
            cache_hit=False,
        )
    cache_key = _compute_pre_screen_cache_key_from_visible_context(
        cv_text=cv_text,
        jd_text=jd_text,
        requirements=requirements,
        workable_context=prompt_workable_context,
    )

    if not skip_cache:
        cached = _cache_get(cache_key, cache_session=cache_read_session)
        if cached is not None:
            return cached

    if not (cv_text or "").strip() or not (jd_text or "").strip():
        return PreScreenResult(
            decision="error",
            reason="missing CV or job spec text",
            prompt_version=PRE_SCREEN_PROMPT_VERSION,
            model_version=MODEL_VERSION,
            trace_id=trace_id,
            cache_hit=False,
        )

    if client is None:
        try:
            client = _resolve_anthropic_client(
                organization_id=(metering_context or {}).get("organization_id")
            )
        except Exception as exc:
            failure_code = safe_anthropic_error_code(
                exc,
                operation="client_init_failed",
            )
            logger.warning("Pre-screen client init failed: %s", failure_code)
            return PreScreenResult(
                decision="error",
                reason=failure_code,
                prompt_version=PRE_SCREEN_PROMPT_VERSION,
                model_version=MODEL_VERSION,
                trace_id=trace_id,
                cache_hit=False,
            )

    # Stable instructions + JD + must-haves live in a cached system block
    # (identical for every candidate in a role batch); the per-candidate
    # CV is the only uncached part. The old shape put the cacheable content
    # in a user-message block and produced zero cache hits in prod — the
    # system-param location is Anthropic's canonical, reliable spot.
    system_blocks = build_pre_screen_system(jd_text, requirements)
    messages = build_pre_screen_user_messages(
        cv_text,
        workable_context=prompt_workable_context,
    )
    provider_request = one_call_request(
        model=MODEL_VERSION,
        system=system_blocks,
        messages=messages,
        max_tokens=256,
        temperature=0,
    )
    started = time.monotonic()
    # The wrapper writes the pre-screen usage_event per call (FK-linked to
    # claude_call_log) when a metering_context is threaded through — captures
    # every actual call. The orchestrator records ONLY cache hits (no
    # Anthropic call → no wrapper run), so no double-count. Absent a context
    # (direct/test calls with a bare client) we skip so the bare client
    # doesn't choke on the metering kwarg.
    if metering_context:
        if (
            metering_context.get("organization_id") is not None
            and metering_context.get("role_id") is not None
        ):
            try:
                from ..services.pre_screen_usage_admission import (
                    release_pre_screen_usage,
                    reserve_pre_screen_usage,
                )

                existing_reservation = metering_context.get("credit_reservation")
                if existing_reservation:
                    release_pre_screen_usage(
                        existing_reservation,
                        reason="pre_screen_cache_miss_provider_rebind",
                    )
                reservation = reserve_pre_screen_usage(
                    metering_context,
                    trace_id=trace_id,
                    provider_request=provider_request,
                )
                if reservation is not None:
                    metering_context = dict(metering_context)
                    metering_context["credit_reservation"] = (
                        reservation.as_metering_payload()
                    )
            except Exception as exc:
                failure_code = safe_provider_error_code(
                    exc,
                    operation="budget_admission_failed",
                )
                logger.info("Pre-screen budget admission blocked: %s", failure_code)
                return PreScreenResult(
                    decision="error",
                    reason=failure_code,
                    prompt_version=PRE_SCREEN_PROMPT_VERSION,
                    model_version=MODEL_VERSION,
                    trace_id=trace_id,
                    cache_hit=False,
                )
        pre_metering = MeteringContext(
            feature="prescreen",
            organization_id=metering_context.get("organization_id"),
            role_id=metering_context.get("role_id"),
            entity_id=metering_context.get("entity_id"),
            candidate_id=metering_context.get("candidate_id"),
            user_id=metering_context.get("user_id"),
            credit_reservation=metering_context.get("credit_reservation"),
        )
    else:
        pre_metering = MeteringContext.skipped(metered_by="direct_call_no_context")
    usage = CallUsage()
    try:
        response = one_call(
            client,
            model=MODEL_VERSION,
            system=system_blocks,
            messages=messages,
            max_tokens=256,
            temperature=0,
            metering=pre_metering,
            usage_sink=usage,
        )
    except Exception as exc:
        failure_code = safe_anthropic_error_code(
            exc,
            operation="claude_call_failed",
        )
        logger.warning("Pre-screen Claude call failed: %s", failure_code)
        return PreScreenResult(
            decision="error",
            reason=failure_code,
            prompt_version=PRE_SCREEN_PROMPT_VERSION,
            model_version=MODEL_VERSION,
            trace_id=trace_id,
            cache_hit=False,
        )

    raw = ""
    try:
        raw = response.content[0].text  # type: ignore[attr-defined]
    except (AttributeError, IndexError):
        raw = ""

    in_tok = usage.input_tokens
    out_tok = usage.output_tokens
    cache_read_tok = usage.cache_read_tokens
    cache_creation_tok = usage.cache_creation_tokens

    text = strip_json_fences(raw)
    decision: PreScreenDecision = "error"
    reason = ""
    parsed_score: float | None = None
    parsed_unverified: bool = False
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            # v2.0: numeric score; v1.0 compat: fall back to decision field
            raw_score = parsed.get("score")
            if raw_score is not None:
                try:
                    parsed_score = max(0.0, min(100.0, float(raw_score)))
                    decision = "yes" if parsed_score >= 50.0 else "no"
                except (TypeError, ValueError):
                    pass
            if parsed_score is None:
                # v1.0 cache hit or malformed v2 response — use decision field
                decision = _normalize_decision(str(parsed.get("decision") or ""))
            reason = str(parsed.get("reason") or "")[:240]
            parsed_unverified = bool(parsed.get("unverified_extraordinary_claim") or False)
    except json.JSONDecodeError as exc:
        logger.warning(
            "Pre-screen JSON parse failed error_type=%s",
            type(exc).__name__,
        )
        decision = "error"
        reason = "json_parse_failed"

    elapsed_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "Pre-screen verdict: score=%s decision=%s elapsed_ms=%d",
        parsed_score,
        decision,
        elapsed_ms,
    )

    result = PreScreenResult(
        decision=decision,
        reason=reason,
        prompt_version=PRE_SCREEN_PROMPT_VERSION,
        model_version=MODEL_VERSION,
        trace_id=trace_id,
        cache_hit=False,
        score=parsed_score,
        unverified_claim=parsed_unverified,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=cache_read_tok,
        cache_creation_tokens=cache_creation_tok,
    )
    if not skip_cache and decision != "error":
        _cache_set(cache_key, result, score=parsed_score)
    return result
