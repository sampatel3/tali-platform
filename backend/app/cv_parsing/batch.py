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
import uuid
from typing import Any, Optional

from ..llm import (
    ValidationFailure,
    extract_structured_tool_input,
    structured_tool_params,
)
from . import MODEL_VERSION, PROMPT_VERSION
from .origins import (
    autonomous_origin_for_application,
    normalize_cv_parse_origin,
)
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

    Only applications on LIVE roles: closed/archived Workable reqs and
    recruiter-marked filled/cancelled roles are excluded in SQL (2026-06
    audit: 51% of the score line went to reqs nobody recruits — don't
    repeat that here; the backlog on dead reqs stays unparsed unless those
    reqs reopen). Only classifiable autonomous ATS/native rows are swept, and
    they require an enabled, unpaused agent. Explicit recruiter uploads use the
    live task so their authority is carried in the queue instead of inferred
    from application source. Unknown/manual legacy backlog fails closed.

    Per application: cache hit (success) → applied inline, no API call;
    cache hit (deterministic failure) → skipped, nothing would change;
    prompt-render failure → failure cached so the sweep stops re-picking;
    otherwise → bundled into that org's batch request list.

    ``limit`` bounds ACTIONABLE rows (batched + cache-applied), not rows
    scanned — otherwise a run of failure-cached rows at the top of the id
    ordering would eat the whole window every sweep and starve older
    parseable rows. Scanning is bounded at 4×limit per sweep.

    The caller owns the transaction (commits applied cache hits).
    """
    from sqlalchemy import and_, func, or_
    from sqlalchemy.orm import joinedload

    from ..models.candidate_application import CandidateApplication
    from ..models.organization import Organization
    from ..models.role import (
        JOB_STATUS_CANCELLED,
        JOB_STATUS_FILLED,
        JOB_STATUS_FILLED_EXTERNAL,
        JOB_STATUS_OPEN,
        ROLE_KIND_STANDARD,
        Role,
    )
    from ..services.claude_client_resolver import get_metered_client
    from ..services.job_page_lifecycle import role_allows_new_paid_ats_work
    from ..services.pricing_service import Feature
    from ..services.workable_actions_service import WORKABLE_NON_LIVE_JOB_STATES
    from . import cache as cache_module
    from .apply import parse_and_store_cv_sections

    summary = {
        "scanned": 0,
        "in_flight": 0,
        "cache_applied": 0,
        "cache_failed_skip": 0,
        "render_failed": 0,
        "runtime_blocked": 0,
        "admission_blocked": 0,
        "admission_failed": 0,
        "lock_contended": False,
        "batches": [],
    }

    # Beat should be singleton, but deploy overlap and manual invocations can
    # race. Serialize the scan→reserve→submit window in Postgres so one CV is
    # never admitted into two batches before either anchor row becomes visible.
    bind = getattr(db, "bind", None)
    if bind is not None and getattr(bind.dialect, "name", None) == "postgresql":
        from sqlalchemy import text

        try:
            acquired = bool(
                db.execute(
                    text(
                        "SELECT pg_try_advisory_xact_lock("
                        "hashtext('cv_parse_batch_sweep'), 0)"
                    )
                ).scalar()
            )
        except Exception:
            logger.exception("cv_parse batch sweep lock failed")
            summary["admission_failed"] = 1
            return summary
        if not acquired:
            summary["lock_contended"] = True
            return summary

    in_flight = in_flight_application_ids(db)

    autonomous_application = func.lower(
        func.coalesce(CandidateApplication.source, "")
    ).in_(
        ["workable", "bullhorn", "careers"]
    )
    autonomous_runtime_ready = and_(
        Role.agentic_mode_enabled.is_(True),
        Role.agent_paused_at.is_(None),
        Role.role_kind == ROLE_KIND_STANDARD,
        or_(Role.job_status.is_(None), Role.job_status == JOB_STATUS_OPEN),
    )

    apps = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .join(Role, Role.id == CandidateApplication.role_id)
        .join(Organization, Organization.id == Role.organization_id)
        .filter(
            CandidateApplication.cv_sections.is_(None),
            CandidateApplication.cv_text.isnot(None),
            CandidateApplication.cv_text != "",
            CandidateApplication.deleted_at.is_(None),
            Role.deleted_at.is_(None),
            Organization.agent_workspace_paused_at.is_(None),
            # A batch sweep has no request-time human principal. Admit only
            # persisted autonomous intake rows while their agent is running.
            and_(autonomous_application, autonomous_runtime_ready),
            # Live reqs only. The filter must be in SQL (not post-load):
            # with a large dead-req backlog a Python-side skip could fill
            # the whole scan window with skipped rows and starve live ones.
            func.coalesce(
                Role.workable_job_data["state"].as_string(), ""
            ).notin_(list(WORKABLE_NON_LIVE_JOB_STATES)),
            # Recruiter fill-marks live on job_status (often with no
            # Workable payload at all). NULL = never marked = live.
            or_(
                Role.job_status.is_(None),
                Role.job_status.notin_(
                    [
                        JOB_STATUS_FILLED,
                        JOB_STATUS_FILLED_EXTERNAL,
                        JOB_STATUS_CANCELLED,
                    ]
                ),
            ),
        )
        .order_by(CandidateApplication.id.desc())
        .limit(limit * 4)
        .all()
    )

    summary["scanned"] = len(apps)
    requests_by_org: dict[int, list[dict]] = {}
    context_by_org: dict[int, dict[str, dict]] = {}
    actionable = 0

    for app in apps:
        if actionable >= limit:
            break
        if app.id in in_flight:
            summary["in_flight"] += 1
            continue

        role = getattr(app, "role", None)
        origin = autonomous_origin_for_application(app)
        if origin is None:
            summary["runtime_blocked"] += 1
            continue
        if not role_allows_new_paid_ats_work(role, db=db):
            # SQL handles the common enabled/paused/job-status cases so a large
            # held backlog cannot starve live rows. This final shared-policy
            # check covers the workspace overlay for both native and ATS
            # origins plus provider-specific lifecycle payloads (for example a
            # Bullhorn ``isOpen:false`` snapshot) without duplicating them here.
            summary["runtime_blocked"] += 1
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
                from ..services.ats_cv_parse_outbox import (
                    record_application_parse_failure,
                )

                record_application_parse_failure(
                    db,
                    application_id=int(app.id),
                    error=str(cached.error_reason or "deterministic CV parse failure"),
                    terminal=True,
                )
                summary["cache_failed_skip"] += 1
            else:
                parse_and_store_cv_sections(app, db=db)
                from ..services.ats_cv_parse_outbox import (
                    record_application_parse_success,
                )

                record_application_parse_success(db, application_id=int(app.id))
                summary["cache_applied"] += 1
                actionable += 1
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
            from ..services.ats_cv_parse_outbox import (
                record_application_parse_failure,
            )

            record_application_parse_failure(
                db,
                application_id=int(app.id),
                error="prompt_render_failed: batch request build",
                terminal=True,
            )
            summary["render_failed"] += 1
            continue

        org_id = int(app.organization_id)
        requests_by_org.setdefault(org_id, []).append(request)
        context_by_org.setdefault(org_id, {})[request["custom_id"]] = {
            "organization_id": org_id,
            "role_id": app.role_id,
            "entity_id": f"application:{app.id}",
            # Durable authority for any live retry after this batch ends.
            "origin": origin,
            # The key of the text this request was rendered from. Apply
            # compares it against the row's CURRENT text so a CV replaced
            # mid-flight doesn't get stale sections (and the result is
            # still cached under the text it actually came from).
            "cache_key": cache_key,
        }
        actionable += 1

    for org_id, requests in requests_by_org.items():
        # Hold one CV_PARSE estimate per request before the batch reaches
        # Anthropic. A dedicated transaction makes the holds visible to the
        # results poller's independent settlement sessions. Expected balance/
        # role-cap refusals skip only that application; an admission-system
        # error fails the whole org batch closed.
        from ..platform.database import SessionLocal
        from ..services.usage_credit_reservations import (
            InsufficientRoleBudgetError,
            release_credit_reservation,
            reserve_credits,
        )
        from ..services.usage_metering_service import InsufficientCreditsError

        admitted_requests: list[dict] = []
        reservations: dict[str, Any] = {}
        meter_db = SessionLocal()
        admission_error = False
        try:
            for request in requests:
                custom_id = str(request.get("custom_id") or "")
                per = context_by_org[org_id].get(custom_id) or {}
                role_id = per.get("role_id")
                if role_id is None:
                    summary["admission_blocked"] += 1
                    continue
                try:
                    reservation = reserve_credits(
                        meter_db,
                        organization_id=int(org_id),
                        feature=Feature.CV_PARSE,
                        external_ref=(
                            f"usage-hold:cv-parse-batch:{custom_id}:"
                            f"{uuid.uuid4().hex}"
                        ),
                        metadata={
                            "sub_feature": "application_cv_parse_batch",
                            "role_id": int(role_id),
                            "entity_id": per.get("entity_id"),
                            "custom_id": custom_id,
                        },
                        role_id=int(role_id),
                        enforce_role_budget=True,
                    )
                except (InsufficientCreditsError, InsufficientRoleBudgetError):
                    summary["admission_blocked"] += 1
                    continue
                reservations[custom_id] = reservation
                admitted_requests.append(request)
            meter_db.commit()
        except Exception:
            meter_db.rollback()
            admission_error = True
            summary["admission_failed"] += len(requests)
            logger.exception(
                "cv_parse batch admission failed org=%s; provider submit skipped",
                org_id,
            )
        finally:
            meter_db.close()

        if admission_error or not admitted_requests:
            continue
        admitted_context: dict[str, dict] = {}
        for request in admitted_requests:
            custom_id = str(request.get("custom_id") or "")
            admitted_context[custom_id] = {
                **context_by_org[org_id][custom_id],
                "credit_reservation": reservations[
                    custom_id
                ].as_metering_payload(),
            }
        try:
            client = get_metered_client(organization_id=org_id)
            batch = client.messages.batches.create(
                requests=admitted_requests,
                metering={
                    "feature": Feature.CV_PARSE,
                    "organization_id": org_id,
                    "by_custom_id": admitted_context,
                },
            )
            summary["batches"].append(
                {
                    "batch_id": str(getattr(batch, "id", "")),
                    "organization_id": org_id,
                    "requests": len(admitted_requests),
                }
            )
            logger.info(
                "cv_parse batch submitted org=%s batch_id=%s requests=%d",
                org_id,
                getattr(batch, "id", None),
                len(admitted_requests),
            )
        except Exception:
            # Leave these rows pending for a later sweep and immediately return
            # every committed hold. The metered wrapper performs the same
            # release on SDK failure for single-message calls; batch submit has
            # one hold per request, so compensate them together here.
            release_db = SessionLocal()
            try:
                for reservation in reservations.values():
                    release_credit_reservation(
                        release_db,
                        reservation=reservation,
                        reason="cv_parse_batch_submit_failed",
                    )
                release_db.commit()
            except Exception:
                release_db.rollback()
                logger.exception(
                    "cv_parse batch reservation release failed org=%s",
                    org_id,
                )
            finally:
                release_db.close()
            logger.exception(
                "cv_parse batch submission failed org=%s (%d requests)",
                org_id,
                len(admitted_requests),
            )

    return summary


def apply_batch_results(db: Any, entries: Any, context: Optional[dict] = None) -> dict:
    """Apply one ended batch's results to application rows.

    Succeeded + valid → ``cv_sections`` written and the parse cache
    populated (so sibling applications with the same CV text hit it).
    Validation failures and errored/expired/canceled entries are handed
    to the live per-application task, which owns the retry-once and
    deterministic-failure-caching semantics. The caller commits.

    ``context`` is the batch row's per-custom_id attribution map. It carries
    the submit-time parse ``origin`` into any live retry. Missing/unknown
    legacy origins fail closed. When it carries the submit-time ``cache_key``,
    results whose application text
    changed mid-flight (CV re-uploaded/refetched) are NOT stored on the
    row — the result is cached under the text it came from and the row is
    left pending for the next sweep to submit with the fresh text.
    """
    from sqlalchemy.orm import joinedload

    from ..models.candidate_application import CandidateApplication
    from ..tasks.automation_tasks import parse_application_cv_sections
    from . import cache as cache_module
    from .apply import store_parsed_cv_sections

    context = context if isinstance(context, dict) else {}
    _, _, tool_name = structured_tool_params(ParsedCVSections)
    summary = {"applied": 0, "requeued": 0, "skipped": 0, "stale_skipped": 0}

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

        custom_id = str(getattr(entry, "custom_id", ""))
        per_context = context.get(custom_id) or {}
        origin = normalize_cv_parse_origin(
            per_context.get("origin") if isinstance(per_context, dict) else None
        )

        result = getattr(entry, "result", None)
        if getattr(result, "type", None) != "succeeded":
            # Request-level failure (errored/expired/canceled). Hand to the
            # live task — it retries and caches deterministic failures.
            if _requeue_live(
                parse_application_cv_sections, app_id, origin=origin
            ):
                summary["requeued"] += 1
            else:
                summary["skipped"] += 1
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
            if _requeue_live(
                parse_application_cv_sections, app_id, origin=origin
            ):
                summary["requeued"] += 1
            else:
                summary["skipped"] += 1
            continue

        parsed = ParsedCV.from_sections(
            sections,
            prompt_version=PROMPT_VERSION,
            model_version=MODEL_VERSION,
        )
        current_key = cache_module.compute_cache_key(
            cv_text=cv_text,
            prompt_version=PROMPT_VERSION,
            model_version=MODEL_VERSION,
        )
        submitted_key = (context.get(custom_id) or {}).get("cache_key")
        try:
            # Cache under the text the result actually came from, so a
            # sibling with the same (old) text still hits.
            cache_module.set(submitted_key or current_key, parsed)
        except Exception:  # pragma: no cover — defensive
            logger.exception("cv_parse batch cache write failed app_id=%s", app_id)
        if submitted_key and submitted_key != current_key:
            # CV changed while the batch was in flight — don't store stale
            # sections; the next sweep resubmits with the fresh text.
            logger.info(
                "cv_parse batch result stale for app_id=%s (CV changed "
                "mid-flight) — leaving row pending.",
                app_id,
            )
            summary["stale_skipped"] += 1
            continue
        store_parsed_cv_sections(app, parsed=parsed, cv_text=cv_text)
        from ..services.ats_cv_parse_outbox import record_application_parse_success

        record_application_parse_success(db, application_id=int(app.id))
        summary["applied"] += 1

    return summary


def _requeue_live(
    task: Any, application_id: int, *, origin: str | None
) -> bool:
    """Enqueue the live per-application parse; never raises (a requeue
    failure just leaves the row for a later sweep)."""
    normalized_origin = normalize_cv_parse_origin(origin)
    if normalized_origin is None:
        logger.warning(
            "cv_parse live requeue blocked: unknown origin app_id=%s",
            application_id,
        )
        return False
    try:
        task.delay(application_id, origin=normalized_origin)
        return True
    except Exception:  # pragma: no cover — defensive
        logger.exception("cv_parse live requeue failed app_id=%s", application_id)
        return False
