from __future__ import annotations

from app.services import agent_worker_health as health


_CAPABILITIES = {
    "anthropic_configured": True,
    "anthropic_probe_ok": True,
    "usage_meter_live": True,
    "e2b_configured": True,
    "resend_configured": True,
    "resend_probe_ok": True,
    "github_configured": True,
    "github_mock_mode": False,
    "github_probe_ok": True,
}


class _Redis:
    def __init__(self):
        self.values = {}

    def set(self, key, value, ex=None):
        self.values[key] = value
        self.ttl = ex
        return True

    def get(self, key):
        return self.values.get(key)


def test_worker_heartbeat_proves_beat_to_worker_path(monkeypatch):
    redis = _Redis()
    monkeypatch.setattr(health.time, "time", lambda: 1000.0)

    capabilities = dict(_CAPABILITIES)
    assert health.record_heartbeat(
        "celery", client=redis, capabilities=capabilities
    ) == 1000.0
    assert health.record_heartbeat(
        "scoring", client=redis, capabilities=capabilities
    ) == 1000.0
    assert redis.ttl == health.HEARTBEAT_TTL_SECONDS

    monkeypatch.setattr(health.time, "time", lambda: 1060.0)
    status = health.worker_beat_status(client=redis)
    assert status["ready"] is True
    assert status["age_seconds"] == 60.0
    assert set(status["queues"]) == {"celery", "scoring"}
    assert status["capability_reporting"] is True
    assert status["queues"]["scoring"]["capabilities"] == capabilities


def test_worker_heartbeat_missing_or_stale_is_not_ready(monkeypatch):
    redis = _Redis()
    assert health.worker_beat_status(client=redis)["reason"] == "heartbeat_missing"

    redis.values[health.heartbeat_key("celery")] = b"1000.0"
    redis.values[health.heartbeat_key("scoring")] = b"1000.0"
    monkeypatch.setattr(
        health.time,
        "time",
        lambda: 1000.0 + health.HEARTBEAT_STALE_SECONDS + 1,
    )
    status = health.worker_beat_status(client=redis)
    assert status["ready"] is False
    assert status["reason"] == "heartbeat_stale"
    assert status["failed_queues"] == ["celery", "scoring"]


def test_default_queue_cannot_certify_missing_scoring_worker(monkeypatch):
    redis = _Redis()
    monkeypatch.setattr(health.time, "time", lambda: 1000.0)
    health.record_heartbeat(
        "celery", client=redis, capabilities=dict(_CAPABILITIES)
    )

    status = health.worker_beat_status(client=redis)

    assert status["ready"] is False
    assert status["reason"] == "heartbeat_missing"
    assert status["failed_queues"] == ["scoring"]
    assert status["queues"]["celery"]["ready"] is True
    assert status["queues"]["scoring"]["ready"] is False


def test_legacy_numeric_heartbeat_does_not_certify_worker_capabilities(monkeypatch):
    redis = _Redis()
    redis.values[health.heartbeat_key("celery")] = b"1000.0"
    redis.values[health.heartbeat_key("scoring")] = b"1000.0"
    monkeypatch.setattr(health.time, "time", lambda: 1001.0)

    status = health.worker_beat_status(client=redis)

    assert status["ready"] is False
    assert status["reason"] == "capabilities_missing"
    assert status["capability_reporting"] is False


def test_assessment_only_capabilities_do_not_define_queue_liveness(monkeypatch):
    redis = _Redis()
    monkeypatch.setattr(health.time, "time", lambda: 1000.0)
    core_only = {
        **_CAPABILITIES,
        "e2b_configured": False,
        "resend_configured": False,
        "github_configured": False,
        "github_mock_mode": True,
        "github_probe_ok": False,
    }
    health.record_heartbeat("celery", client=redis, capabilities=core_only)
    health.record_heartbeat("scoring", client=redis, capabilities=core_only)

    status = health.worker_beat_status(client=redis)

    assert status["ready"] is True
    assert status["capability_reporting"] is True
    assert status["queues"]["celery"]["capabilities"] == core_only


def test_provider_probe_is_cached_per_worker_queue(monkeypatch):
    redis = _Redis()
    calls: list[str] = []
    monkeypatch.setattr(health.time, "time", lambda: 1000.0)
    monkeypatch.setattr(
        health,
        "_run_provider_probe",
        lambda queue_name, *, settings_obj: (
            calls.append(queue_name)
            or {
                "provider_checked_at_epoch": 1000.0,
                "anthropic_probe_ok": True,
            }
        ),
    )

    first = health.provider_probe_status("scoring", client=redis)
    second = health.provider_probe_status("scoring", client=redis)

    assert first == second
    assert calls == ["scoring"]
    assert redis.ttl == health.PROVIDER_PROBE_TTL_SECONDS


def test_provider_probe_uses_real_resolved_model_properties(monkeypatch):
    """Readiness must not degrade because the probe references a stale alias."""
    retrieved: list[str] = []

    class _Models:
        def retrieve(self, model):
            retrieved.append(model)

    client = type("Client", (), {"models": _Models()})()
    monkeypatch.setattr(
        "app.services.claude_client_resolver.get_raw_shared_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "app.services.github_credentials.verify_github_credentials",
        lambda **kwargs: {"ok": True, "mock": False},
    )
    settings_obj = type(
        "Settings",
        (),
        {
            "resolved_claude_model": "claude-main",
            "resolved_agent_autonomous_model": "claude-agent",
            "resolved_claude_chat_model": "claude-chat",
            "resolved_claude_scoring_model": "claude-score",
            "GITHUB_ORG": "acme",
            "GITHUB_TOKEN": "gh-token",
            "GITHUB_MOCK_MODE": False,
        },
    )()

    default = health._run_provider_probe("celery", settings_obj=settings_obj)
    scoring = health._run_provider_probe("scoring", settings_obj=settings_obj)

    assert default["anthropic_probe_ok"] is True
    assert default["github_probe_ok"] is True
    assert scoring["anthropic_probe_ok"] is True
    assert retrieved == [
        "claude-main",
        "claude-agent",
        "claude-chat",
        "claude-score",
    ]


def test_default_provider_probe_adds_cached_live_resend_delivery(monkeypatch):
    redis = _Redis()
    provider_calls: list[str] = []
    resend_calls: list[object] = []
    monkeypatch.setattr(health.time, "time", lambda: 1000.0)
    monkeypatch.setattr(
        health,
        "_run_provider_probe",
        lambda queue_name, *, settings_obj: (
            provider_calls.append(queue_name)
            or {
                "provider_checked_at_epoch": 1000.0,
                "anthropic_probe_ok": True,
                "github_probe_ok": True,
            }
        ),
    )
    monkeypatch.setattr(
        health,
        "_run_resend_probe",
        lambda *, settings_obj: (
            resend_calls.append(settings_obj)
            or {
                "resend_probe_checked_at_epoch": 1000.0,
                "resend_probe_ok": True,
                "resend_probe_email_id": "probe-1",
            }
        ),
    )
    settings_obj = object()

    first = health.provider_probe_status(
        "celery", client=redis, settings_obj=settings_obj
    )
    second = health.provider_probe_status(
        "celery", client=redis, settings_obj=settings_obj
    )

    assert first == second
    assert first["resend_probe_ok"] is True
    assert provider_calls == ["celery"]
    assert resend_calls == [settings_obj]
    assert redis.ttl == health.RESEND_PROBE_SUCCESS_TTL_SECONDS


def test_resend_probe_uses_non_delivering_test_recipient(monkeypatch):
    sent = []

    class _EmailService:
        def __init__(self, *, api_key, from_email):
            sent.append(("init", api_key, from_email))

        def send_internal_alert(self, to_email, subject, text_body):
            sent.append(("send", to_email, subject, text_body))
            return {"success": True, "email_id": "probe-2"}

    monkeypatch.setattr(
        "app.components.notifications.email_client.EmailService",
        _EmailService,
    )
    settings_obj = type(
        "Settings",
        (),
        {"RESEND_API_KEY": "re_live", "EMAIL_FROM": "Taali <noreply@taali.ai>"},
    )()

    result = health._run_resend_probe(settings_obj=settings_obj)

    assert result["resend_probe_ok"] is True
    assert result["resend_probe_email_id"] == "probe-2"
    assert sent[0] == ("init", "re_live", "Taali <noreply@taali.ai>")
    assert sent[1][0:2] == ("send", health.RESEND_TEST_RECIPIENT)


def test_failed_resend_probe_retries_on_short_probe_cadence(monkeypatch):
    redis = _Redis()
    now = [1000.0]
    calls = []
    monkeypatch.setattr(health.time, "time", lambda: now[0])
    monkeypatch.setattr(
        health,
        "_run_resend_probe",
        lambda *, settings_obj: (
            calls.append(now[0])
            or {
                "resend_probe_checked_at_epoch": now[0],
                "resend_probe_ok": False,
            }
        ),
    )

    assert health.resend_probe_status(client=redis)["resend_probe_ok"] is False
    now[0] += health.PROVIDER_PROBE_STALE_SECONDS - 1
    assert health.resend_probe_status(client=redis)["resend_probe_ok"] is False
    now[0] += 2
    assert health.resend_probe_status(client=redis)["resend_probe_ok"] is False

    assert calls == [1000.0, 1301.0]


def test_real_invite_failure_invalidates_cached_resend_success(monkeypatch):
    redis = _Redis()
    monkeypatch.setattr(health.time, "time", lambda: 1000.0)
    redis.set(
        health.RESEND_PROBE_KEY,
        '{"resend_probe_checked_at_epoch":1000,"resend_probe_ok":true}',
        ex=health.RESEND_PROBE_SUCCESS_TTL_SECONDS,
    )

    health.invalidate_resend_probe_cache(error="429 provider outage", client=redis)
    status = health.resend_probe_status(client=redis)

    assert status["resend_probe_ok"] is False
    assert status["resend_probe_error"] == "429 provider outage"
    assert redis.ttl == health.RESEND_PROBE_FAILURE_TTL_SECONDS


def test_beat_routes_high_priority_canary_to_each_required_queue():
    from app.tasks.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    expected = {
        "default-queue-worker-heartbeat-every-minute": "celery",
        "scoring-queue-worker-heartbeat-every-minute": "scoring",
    }
    for entry_name, queue_name in expected.items():
        entry = schedule[entry_name]
        assert entry["task"] == "app.tasks.health_tasks.queue_worker_heartbeat"
        assert entry["args"] == [queue_name]
        assert entry["options"]["queue"] == queue_name
        assert entry["options"]["priority"] == 9
