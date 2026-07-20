"""Worker + beat liveness for the one-switch agent workflow.

Periodic Celery canaries write one short-lived Redis heartbeat per required
queue. A fresh value proves both that Beat dispatched scheduled work and that a
worker consuming *that queue* executed it; plain Redis ``PING`` or a heartbeat
from only the default queue proves neither for scoring. Production activation
uses the aggregate signal to fail closed instead of showing an enabled-but-idle
agent.
"""
from __future__ import annotations

import json
import time
from typing import Any

from ..platform.config import settings


HEARTBEAT_KEY_PREFIX = "taali:agent-worker-beat-heartbeat:v2"
DEFAULT_QUEUE = "celery"
REQUIRED_QUEUES = (DEFAULT_QUEUE, "scoring")
HEARTBEAT_TTL_SECONDS = 300
HEARTBEAT_STALE_SECONDS = 180
PROVIDER_PROBE_KEY_PREFIX = "taali:agent-worker-provider-probe:v1"
PROVIDER_PROBE_TTL_SECONDS = 600
PROVIDER_PROBE_STALE_SECONDS = 300
RESEND_PROBE_KEY = "taali:agent-worker-resend-probe:v1"
# A real send validates the API key and configured sender/domain together.  It
# uses Resend's non-delivering test recipient, so once daily is enough; failures
# retry on the normal short provider-probe cadence instead of staying cached.
RESEND_PROBE_SUCCESS_TTL_SECONDS = 90_000
RESEND_PROBE_SUCCESS_STALE_SECONDS = 86_400
RESEND_PROBE_FAILURE_TTL_SECONDS = PROVIDER_PROBE_TTL_SECONDS
RESEND_TEST_RECIPIENT = "delivered@resend.dev"


def heartbeat_key(queue_name: str) -> str:
    queue = (queue_name or "").strip()
    if queue not in REQUIRED_QUEUES:
        raise ValueError(f"unsupported worker queue heartbeat: {queue!r}")
    return f"{HEARTBEAT_KEY_PREFIX}:{queue}"


def provider_probe_key(queue_name: str) -> str:
    queue = (queue_name or "").strip()
    if queue not in REQUIRED_QUEUES:
        raise ValueError(f"unsupported worker queue provider probe: {queue!r}")
    return f"{PROVIDER_PROBE_KEY_PREFIX}:{queue}"


# Backwards-compatible name for callers/tests that probe the default queue.
HEARTBEAT_KEY = heartbeat_key(DEFAULT_QUEUE)


def _client():
    import redis

    return redis.Redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def runtime_capabilities(*, settings_obj: Any = settings) -> dict[str, Any]:
    """Non-secret capability fingerprint produced inside the worker process."""

    def configured(value: str | None) -> bool:
        cleaned = (value or "").strip().lower()
        return bool(cleaned and cleaned not in {"skip", "changeme"}) and not cleaned.startswith(
            "your-"
        )

    return {
        "anthropic_configured": configured(
            getattr(settings_obj, "ANTHROPIC_API_KEY", None)
        ),
        "usage_meter_live": bool(
            getattr(settings_obj, "USAGE_METER_LIVE", False)
        ),
        "e2b_configured": configured(getattr(settings_obj, "E2B_API_KEY", None)),
        "resend_configured": configured(
            getattr(settings_obj, "RESEND_API_KEY", None)
        ),
        # Feature flags are process-local. Reporting Bullhorn here prevents a
        # web pod with the integration enabled from activating a role while the
        # default worker would silently suppress every Bullhorn handoff.
        "bullhorn_enabled": bool(
            getattr(settings_obj, "BULLHORN_ENABLED", False)
        ),
        "claude_model": str(getattr(settings_obj, "CLAUDE_MODEL", "") or ""),
        "claude_scoring_batch_model": str(
            getattr(settings_obj, "CLAUDE_SCORING_BATCH_MODEL", "") or ""
        ),
    }


def _run_provider_probe(
    queue_name: str, *, settings_obj: Any = settings
) -> dict[str, Any]:
    """Validate model access without spend."""

    checked_at = time.time()
    result: dict[str, Any] = {
        "provider_checked_at_epoch": checked_at,
        "anthropic_probe_ok": False,
    }
    try:
        from .claude_client_resolver import get_raw_shared_client

        if queue_name == "scoring":
            models = [settings_obj.resolved_claude_scoring_model]
        else:
            models = [
                settings_obj.resolved_claude_model,
                settings_obj.resolved_agent_autonomous_model,
                settings_obj.resolved_claude_chat_model,
            ]
        unique_models = list(dict.fromkeys(str(model) for model in models if model))
        # Provider metadata calls are non-billable, so use the resolver's
        # explicitly raw client rather than constructing an SDK client here.
        # The architecture gate keeps all client construction centralized.
        client = get_raw_shared_client()
        for model in unique_models:
            client.models.retrieve(model)
        result["anthropic_probe_ok"] = True
        result["anthropic_models_verified"] = unique_models
    except Exception as exc:
        result["anthropic_probe_error"] = str(exc)[:300]

    return result


def _run_resend_probe(*, settings_obj: Any = settings) -> dict[str, Any]:
    """Verify Resend credentials and sender-domain access without delivery.

    ``delivered@resend.dev`` is Resend's documented test recipient.  The API
    accepts the message and records a successful delivery simulation, but no
    external recipient is contacted.
    """

    checked_at = time.time()
    result: dict[str, Any] = {
        "resend_probe_checked_at_epoch": checked_at,
        "resend_probe_ok": False,
    }
    api_key = str(getattr(settings_obj, "RESEND_API_KEY", "") or "").strip()
    if not api_key or api_key.lower() in {"skip", "changeme"} or api_key.lower().startswith("your-"):
        result["resend_probe_error"] = "RESEND_API_KEY is not configured"
        return result
    try:
        from ..components.notifications.email_client import EmailService

        service = EmailService(
            api_key=api_key,
            from_email=str(
                getattr(settings_obj, "EMAIL_FROM", None)
                or getattr(settings, "EMAIL_FROM", "")
            ),
        )
        delivery = service.send_internal_alert(
            RESEND_TEST_RECIPIENT,
            "Taali delivery readiness probe",
            "Automated worker readiness probe. No action is required.",
        )
        result["resend_probe_ok"] = bool(delivery.get("success"))
        if delivery.get("email_id"):
            result["resend_probe_email_id"] = str(delivery["email_id"])
        if not result["resend_probe_ok"]:
            result["resend_probe_error"] = "Resend rejected the test delivery"
    except Exception as exc:
        result["resend_probe_error"] = str(exc)[:300]
    return result


def resend_probe_status(
    *,
    client: Any | None = None,
    settings_obj: Any = settings,
) -> dict[str, Any]:
    """Return a cached live Resend send probe produced by the default worker."""

    redis_client = client or _client()
    try:
        raw = redis_client.get(RESEND_PROBE_KEY)
        if raw is not None:
            value = raw.decode() if isinstance(raw, bytes) else str(raw)
            cached = json.loads(value)
            age = max(
                0.0,
                time.time()
                - float(cached.get("resend_probe_checked_at_epoch", 0)),
            )
            if bool(cached.get("resend_probe_ok")) and (
                age <= RESEND_PROBE_SUCCESS_STALE_SECONDS
            ):
                return cached
            if not bool(cached.get("resend_probe_ok")) and (
                age <= PROVIDER_PROBE_STALE_SECONDS
            ):
                return cached
    except Exception:
        # A missing/corrupt result triggers a new test delivery.
        pass

    result = _run_resend_probe(settings_obj=settings_obj)
    ttl = (
        RESEND_PROBE_SUCCESS_TTL_SECONDS
        if result.get("resend_probe_ok") is True
        else RESEND_PROBE_FAILURE_TTL_SECONDS
    )
    redis_client.set(
        RESEND_PROBE_KEY,
        json.dumps(result, sort_keys=True, separators=(",", ":")),
        ex=ttl,
    )
    return result


def invalidate_resend_probe_cache(
    *,
    error: str | None = None,
    client: Any | None = None,
) -> None:
    """Record a short-lived failed probe after a real invite send fails.

    A provider call is stronger evidence than the daily readiness canary.  By
    replacing the cached success with a failure, the recovery sweep stays
    closed until a default-worker heartbeat performs a fresh live probe.
    """

    redis_client = client or _client()
    payload = {
        "resend_probe_checked_at_epoch": time.time(),
        "resend_probe_ok": False,
        "resend_probe_error": str(error or "assessment invite delivery failed")[:300],
    }
    redis_client.set(
        RESEND_PROBE_KEY,
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        ex=RESEND_PROBE_FAILURE_TTL_SECONDS,
    )


def provider_probe_status(
    queue_name: str,
    *,
    client: Any | None = None,
    settings_obj: Any = settings,
) -> dict[str, Any]:
    """Return a short-lived live provider probe produced by this queue worker."""

    redis_client = client or _client()
    key = provider_probe_key(queue_name)
    result: dict[str, Any] | None = None
    try:
        raw = redis_client.get(key)
        if raw is not None:
            value = raw.decode() if isinstance(raw, bytes) else str(raw)
            cached = json.loads(value)
            age = max(
                0.0,
                time.time() - float(cached.get("provider_checked_at_epoch", 0)),
            )
            if age <= PROVIDER_PROBE_STALE_SECONDS:
                result = cached
    except Exception:
        # A corrupt/missing cache simply triggers a fresh non-billable probe.
        pass
    if result is None:
        result = _run_provider_probe(queue_name, settings_obj=settings_obj)
        redis_client.set(
            key,
            json.dumps(result, sort_keys=True, separators=(",", ":")),
            ex=PROVIDER_PROBE_TTL_SECONDS,
        )
    if queue_name == DEFAULT_QUEUE:
        result = {**result, **resend_probe_status(client=redis_client, settings_obj=settings_obj)}
    return result


def record_heartbeat(
    queue_name: str = DEFAULT_QUEUE,
    *,
    client: Any | None = None,
    capabilities: dict[str, Any] | None = None,
) -> float:
    now = time.time()
    payload = json.dumps(
        {
            "recorded_at_epoch": now,
            "capabilities": capabilities or {},
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    (client or _client()).set(
        heartbeat_key(queue_name),
        payload,
        ex=HEARTBEAT_TTL_SECONDS,
    )
    return now


def worker_beat_status(
    *,
    client: Any | None = None,
    required_queues: tuple[str, ...] = REQUIRED_QUEUES,
) -> dict[str, Any]:
    try:
        redis_client = client or _client()
        statuses: dict[str, dict[str, Any]] = {}
        now = time.time()
        for queue_name in required_queues:
            raw = redis_client.get(heartbeat_key(queue_name))
            if raw is None:
                statuses[queue_name] = {
                    "ready": False,
                    "reason": "heartbeat_missing",
                    "age_seconds": None,
                }
                continue
            value = raw.decode() if isinstance(raw, bytes) else str(raw)
            capabilities: dict[str, Any] = {}
            try:
                payload = json.loads(value)
            except (TypeError, ValueError):
                # Rolling-deploy compatibility with the original numeric
                # heartbeat. It proves consumption but not worker config.
                recorded_at = float(value)
            else:
                if isinstance(payload, dict):
                    recorded_at = float(payload["recorded_at_epoch"])
                    raw_capabilities = payload.get("capabilities")
                    if isinstance(raw_capabilities, dict):
                        capabilities = raw_capabilities
                elif isinstance(payload, (int, float)):
                    # ``1000.0`` is valid JSON as well as the legacy format.
                    recorded_at = float(payload)
                else:
                    raise ValueError("invalid heartbeat payload")
            age = max(0.0, now - recorded_at)
            heartbeat_fresh = age <= HEARTBEAT_STALE_SECONDS
            runtime_reason: str | None = None
            if not heartbeat_fresh:
                runtime_reason = "heartbeat_stale"
            elif not capabilities:
                runtime_reason = "capabilities_missing"
            elif capabilities.get("anthropic_probe_ok") is not True:
                runtime_reason = "provider_probe_failed"
            elif not bool(capabilities.get("usage_meter_live")):
                runtime_reason = "usage_meter_not_live"
            # E2B and Resend are assessment-path capabilities, not
            # queue liveness. Keep them in the heartbeat fingerprint so role
            # activation can enforce them when that role uses assessments;
            # an explicitly assessment-free role must not be blocked by
            # providers it will never call.
            statuses[queue_name] = {
                "ready": runtime_reason is None,
                "reason": runtime_reason,
                "heartbeat_fresh": heartbeat_fresh,
                "age_seconds": round(age, 1),
                "recorded_at_epoch": recorded_at,
                "capabilities": capabilities,
            }

        failed_queues = [
            queue_name
            for queue_name, status in statuses.items()
            if not bool(status.get("ready"))
        ]
        reasons = {
            str(statuses[queue_name].get("reason"))
            for queue_name in failed_queues
        }
        reason_priority = (
            "heartbeat_missing",
            "heartbeat_stale",
            "capabilities_missing",
            "provider_probe_failed",
            "usage_meter_not_live",
        )
        reason = next((item for item in reason_priority if item in reasons), None)
        ages = [
            float(status["age_seconds"])
            for status in statuses.values()
            if status.get("age_seconds") is not None
        ]
        return {
            "ready": not failed_queues,
            "reason": reason,
            "age_seconds": max(ages) if ages else None,
            "failed_queues": failed_queues,
            "queues": statuses,
            "capability_reporting": all(
                bool(status.get("capabilities")) for status in statuses.values()
            ),
        }
    except Exception as exc:
        return {
            "ready": False,
            "reason": "heartbeat_unavailable",
            "age_seconds": None,
            "failed_queues": list(required_queues),
            "queues": {},
            "detail": str(exc)[:200],
        }


__all__ = [
    "HEARTBEAT_KEY",
    "HEARTBEAT_KEY_PREFIX",
    "HEARTBEAT_STALE_SECONDS",
    "DEFAULT_QUEUE",
    "REQUIRED_QUEUES",
    "heartbeat_key",
    "provider_probe_key",
    "provider_probe_status",
    "invalidate_resend_probe_cache",
    "resend_probe_status",
    "record_heartbeat",
    "runtime_capabilities",
    "worker_beat_status",
]
