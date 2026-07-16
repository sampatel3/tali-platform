"""cv_parse via the Message Batches API (app/cv_parsing/batch.py).

Pins: request params bit-identical to the sync path, per-org batch
submission with in-flight dedup, cache-hit write-through without an API
call, result application (cv_sections + parse cache), failure hand-off
to the live task, and the CV_PARSE_BATCH_ENABLED gate on the
per-application enqueue.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional

from app.cv_parsing import MODEL_VERSION, PROMPT_VERSION
from app.cv_parsing import batch as batch_module
from app.cv_parsing import cache as cache_module
from app.cv_parsing.batch import (
    DEFAULT_SWEEP_LIMIT,
    application_id_from,
    apply_batch_results,
    build_cv_parse_request,
    custom_id_for,
    sweep_pending_applications,
)
from app.cv_parsing.runner import (
    CV_TEXT_CEILING,
    OUTPUT_TOKEN_CEILING,
    TEMPERATURE,
    _SYSTEM_PROMPT,
)
from app.cv_parsing.schemas import ParsedCV, ParsedCVSections
from app.cv_parsing.origins import (
    CV_PARSE_ORIGIN_ATS_INGEST,
    CV_PARSE_ORIGIN_NATIVE_APPLY,
    CV_PARSE_ORIGIN_RECRUITER_UPLOAD,
    autonomous_origin_for_application,
)
from app.models.anthropic_batch_job import AnthropicBatchJob
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import JOB_STATUS_OPEN, Role
from app.services.metered_anthropic_client import MeteredAnthropicClient

CV_TEXT = "Jane Doe. Senior engineer at Acme Corp since 2019. Python, SQL."


def _seed_app(db, *, org=None, role=None, cv_text=CV_TEXT, email="c@x.test"):
    if org is None:
        org = Organization(name="O", slug=f"o-{id(db)}-{email}")
        db.add(org)
        db.flush()
    if role is None:
        role = Role(
            organization_id=org.id,
            name="R",
            source="manual",
            job_spec_text="hire",
            agentic_mode_enabled=True,
            monthly_usd_budget_cents=5000,
        )
        db.add(role)
        db.flush()
    cand = Candidate(organization_id=org.id, email=email, full_name="C")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="careers",
        cv_text=cv_text,
    )
    db.add(app)
    db.flush()
    return org, role, app


# ---- request rendering -----------------------------------------------------


def test_custom_id_roundtrip():
    assert application_id_from(custom_id_for(42)) == 42
    assert application_id_from("cvparse-notanint") is None
    assert application_id_from("other-42") is None


def test_autonomous_origin_uses_application_source_not_role_linkage():
    ats_linked_role = SimpleNamespace(
        workable_job_id="ATS-1", bullhorn_job_order_id=None
    )
    assert (
        autonomous_origin_for_application(
            SimpleNamespace(source="careers", role=ats_linked_role)
        )
        == CV_PARSE_ORIGIN_NATIVE_APPLY
    )
    assert (
        autonomous_origin_for_application(
            SimpleNamespace(source="manual", role=ats_linked_role)
        )
        is None
    )


def test_build_request_matches_sync_params():
    request = build_cv_parse_request(42, CV_TEXT)
    assert request is not None
    assert request["custom_id"] == "cvparse-42"
    params = request["params"]
    assert params["model"] == MODEL_VERSION
    assert params["max_tokens"] == OUTPUT_TOKEN_CEILING
    assert params["temperature"] == TEMPERATURE
    assert params["system"] == _SYSTEM_PROMPT
    assert CV_TEXT in params["messages"][0]["content"]
    # Forced tool-use, same synthetic tool the sync gateway builds.
    assert params["tool_choice"] == {
        "type": "tool",
        "name": "emit_parsed_cv_sections",
    }
    assert params["tools"][0]["name"] == "emit_parsed_cv_sections"
    assert (
        params["tools"][0]["input_schema"]
        == ParsedCVSections.model_json_schema()
    )


# ---- submission sweep -------------------------------------------------------


@dataclass
class _FakeBatch:
    id: str = "msgbatch_cvp_1"
    processing_status: str = "in_progress"


class _FakeBatches:
    def __init__(self):
        self.created: list[dict] = []

    def create(self, **kwargs: Any) -> _FakeBatch:
        assert "metering" not in kwargs  # wrapper must strip it
        self.created.append(kwargs)
        return _FakeBatch(id=f"msgbatch_cvp_{len(self.created)}")

    def results(self, batch_id: str, **_: Any):
        return iter([])


class _FakeAnthropic:
    def __init__(self, batches: _FakeBatches):
        self.messages = SimpleNamespace(batches=batches)


def _patch_client(monkeypatch, fake_batches: _FakeBatches):
    def _get_metered_client(*, organization_id=None):
        return MeteredAnthropicClient(
            inner=_FakeAnthropic(fake_batches), organization_id=organization_id
        )

    monkeypatch.setattr(
        "app.services.claude_client_resolver.get_metered_client",
        _get_metered_client,
    )


def test_sweep_submits_per_org_and_dedupes_in_flight(db, monkeypatch):
    org1, role1, app1 = _seed_app(db, email="a@x.test")
    _seed_app(db, org=org1, role=role1, cv_text=CV_TEXT + " More.", email="b@x.test")
    _seed_app(db, email="c@other.test")  # second org
    db.commit()

    fake = _FakeBatches()
    _patch_client(monkeypatch, fake)

    summary = sweep_pending_applications(db)
    db.commit()

    # One single-org batch per organization.
    assert len(summary["batches"]) == 2
    assert len(fake.created) == 2
    sizes = sorted(len(c["requests"]) for c in fake.created)
    assert sizes == [1, 2]

    rows = db.query(AnthropicBatchJob).all()
    assert len(rows) == 2
    assert all(r.feature == "cv_parse" for r in rows)
    assert all(r.status == "submitted" for r in rows)
    ctx = next(r.context for r in rows if r.request_count == 2)
    assert ctx[custom_id_for(app1.id)]["entity_id"] == f"application:{app1.id}"
    assert ctx[custom_id_for(app1.id)]["origin"] == CV_PARSE_ORIGIN_NATIVE_APPLY

    # Second sweep: everything is in-flight, nothing resubmitted.
    summary2 = sweep_pending_applications(db)
    assert summary2["in_flight"] == 3
    assert len(fake.created) == 2


def test_sweep_defers_ats_parse_backlog_until_agent_is_running(db, monkeypatch):
    org = Organization(name="ATS hold", slug=f"ats-hold-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Workable role",
        source="workable",
        workable_job_id="ATS-HOLD",
        workable_job_data={"state": "published"},
        job_status=JOB_STATUS_OPEN,
        starred_for_auto_sync=True,
        agentic_mode_enabled=False,
        monthly_usd_budget_cents=5000,
    )
    db.add(role)
    db.flush()
    _, _, app = _seed_app(
        db,
        org=org,
        role=role,
        cv_text="Unique ATS-held CV text for batch admission.",
        email="ats-hold@x.test",
    )
    app.source = "workable"
    db.commit()

    fake = _FakeBatches()
    _patch_client(monkeypatch, fake)

    held = sweep_pending_applications(db)
    assert held["batches"] == []
    assert fake.created == []
    assert app.cv_sections is None

    role.agentic_mode_enabled = True
    db.commit()
    resumed = sweep_pending_applications(db)

    assert len(resumed["batches"]) == 1
    assert len(fake.created) == 1
    assert fake.created[0]["requests"][0]["custom_id"] == custom_id_for(app.id)


def test_sweep_defers_native_parse_and_excludes_unknown_manual_rows(db, monkeypatch):
    org, role, native_app = _seed_app(
        db,
        cv_text="Unique held native CV text.",
        email="native-held@x.test",
    )
    _, _, manual_app = _seed_app(
        db,
        org=org,
        role=role,
        cv_text="Unique unknown manual CV text.",
        email="manual-unknown@x.test",
    )
    manual_app.source = "manual"
    # A linked ATS role is not authority to reinterpret a manual row as ATS;
    # the native row's own `careers` source also remains native.
    role.workable_job_id = "LINKED-ROLE"
    role.workable_job_data = {"state": "published"}
    role.agent_paused_at = datetime.now(timezone.utc)
    db.commit()

    fake = _FakeBatches()
    _patch_client(monkeypatch, fake)
    assert sweep_pending_applications(db)["batches"] == []

    role.agent_paused_at = None
    db.commit()
    resumed = sweep_pending_applications(db)

    submitted = {
        request["custom_id"]
        for batch in fake.created
        for request in batch["requests"]
    }
    assert resumed["batches"]
    assert submitted == {custom_id_for(native_app.id)}
    assert custom_id_for(manual_app.id) not in submitted


def test_sweep_defers_native_parse_while_workspace_agent_is_paused(db, monkeypatch):
    org, _role, app = _seed_app(
        db,
        cv_text="Unique workspace-held native CV text.",
        email="native-workspace-held@x.test",
    )
    org.agent_workspace_paused_at = datetime.now(timezone.utc)
    org.agent_workspace_paused_reason = "workspace paused by recruiter"
    db.commit()

    fake = _FakeBatches()
    _patch_client(monkeypatch, fake)

    held = sweep_pending_applications(db)
    assert held["batches"] == []
    assert fake.created == []
    assert app.cv_sections is None

    org.agent_workspace_paused_at = None
    org.agent_workspace_paused_reason = None
    db.commit()

    resumed = sweep_pending_applications(db)
    assert len(resumed["batches"]) == 1
    assert fake.created[0]["requests"][0]["custom_id"] == custom_id_for(app.id)


def test_sweep_blocks_batch_submit_before_provider_when_credits_are_empty(
    db, monkeypatch
):
    org, role, _ = _seed_app(
        db,
        cv_text="Unique zero-credit batch CV text.",
        email="zero-credit@x.test",
    )
    org.credits_balance = 0
    role.monthly_usd_budget_cents = 5_000
    db.commit()
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    fake = _FakeBatches()
    _patch_client(monkeypatch, fake)

    summary = sweep_pending_applications(db)

    assert summary["admission_blocked"] == 1
    assert summary["batches"] == []
    assert fake.created == []


def test_sweep_excludes_closed_and_archived_roles(db, monkeypatch):
    """Apps on dead reqs are excluded in SQL — the 2026-06 audit's
    dead-req-spend lesson. Covers both Workable states (closed/archived)
    — the shared WORKABLE_NON_LIVE_JOB_STATES set incl. draft —
    and recruiter fill-marks on job_status (which often have no Workable
    payload at all). Manual roles without either count as live."""
    org, live_role, live_app = _seed_app(db, email="live@x.test")
    for state, email in (
        ("archived", "arch@x.test"),
        ("closed", "closed@x.test"),
        ("draft", "draft@x.test"),
    ):
        dead_role = Role(
            organization_id=org.id,
            name=f"dead-{state}",
            source="manual",
            job_spec_text="hire",
            workable_job_data={"state": state},
        )
        db.add(dead_role)
        db.flush()
        _seed_app(db, org=org, role=dead_role, email=email)
    for job_status, email in (
        ("filled", "filled@x.test"),
        ("filled_external", "fext@x.test"),
        ("cancelled", "cxl@x.test"),
    ):
        marked_role = Role(
            organization_id=org.id,
            name=f"marked-{job_status}",
            source="manual",
            job_spec_text="hire",
            job_status=job_status,
        )
        db.add(marked_role)
        db.flush()
        _seed_app(db, org=org, role=marked_role, email=email)
    published_role = Role(
        organization_id=org.id,
        name="pub",
        source="manual",
        job_spec_text="hire",
        workable_job_data={"state": "published"},
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
    )
    db.add(published_role)
    db.flush()
    _, _, pub_app = _seed_app(db, org=org, role=published_role, email="pub@x.test")
    db.commit()

    fake = _FakeBatches()
    _patch_client(monkeypatch, fake)

    sweep_pending_applications(db)
    db.commit()

    assert len(fake.created) == 1
    submitted = {r["custom_id"] for r in fake.created[0]["requests"]}
    assert submitted == {custom_id_for(live_app.id), custom_id_for(pub_app.id)}


def test_sweep_applies_cache_hits_without_api_call(db, monkeypatch):
    _, _, app = _seed_app(db)
    db.commit()

    cached = ParsedCV.from_sections(
        ParsedCVSections(skills=["Python"]),
        prompt_version=PROMPT_VERSION,
        model_version=MODEL_VERSION,
    )
    cache_module.set(
        cache_module.compute_cache_key(
            cv_text=CV_TEXT[:CV_TEXT_CEILING],
            prompt_version=PROMPT_VERSION,
            model_version=MODEL_VERSION,
        ),
        cached,
    )

    fake = _FakeBatches()
    _patch_client(monkeypatch, fake)

    summary = sweep_pending_applications(db)
    db.commit()

    assert summary["cache_applied"] == 1
    assert summary["batches"] == []
    assert fake.created == []
    db.refresh(app)
    assert app.cv_sections is not None
    assert app.cv_sections["skills"] == ["Python"]


def test_sweep_skips_cached_deterministic_failures(db, monkeypatch):
    _, _, app = _seed_app(db)
    db.commit()

    cache_module.set(
        cache_module.compute_cache_key(
            cv_text=CV_TEXT[:CV_TEXT_CEILING],
            prompt_version=PROMPT_VERSION,
            model_version=MODEL_VERSION,
        ),
        ParsedCV.failed(
            reason="validation_failed_after_retry: nope",
            prompt_version=PROMPT_VERSION,
            model_version=MODEL_VERSION,
        ),
    )

    fake = _FakeBatches()
    _patch_client(monkeypatch, fake)

    summary = sweep_pending_applications(db)
    assert summary["cache_failed_skip"] == 1
    assert fake.created == []


# ---- result application ------------------------------------------------------


def _succeeded_entry(app_id: int, sections: Optional[dict] = None):
    block = SimpleNamespace(
        type="tool_use",
        name="emit_parsed_cv_sections",
        input=sections
        if sections is not None
        else {"skills": ["Python"], "headline": "Senior engineer"},
    )
    message = SimpleNamespace(content=[block])
    return SimpleNamespace(
        custom_id=custom_id_for(app_id),
        result=SimpleNamespace(type="succeeded", message=message),
    )


def test_apply_results_writes_sections_and_cache(db):
    _, _, app = _seed_app(db)
    db.commit()

    summary = apply_batch_results(db, [_succeeded_entry(app.id)])
    db.commit()

    assert summary == {"applied": 1, "requeued": 0, "skipped": 0, "stale_skipped": 0}
    db.refresh(app)
    assert app.cv_sections["skills"] == ["Python"]
    assert app.cv_sections["parse_failed"] is False
    assert app.candidate.cv_sections["skills"] == ["Python"]

    # Cache populated so sibling applications with the same text hit it.
    cached = cache_module.get(
        cache_module.compute_cache_key(
            cv_text=CV_TEXT[:CV_TEXT_CEILING],
            prompt_version=PROMPT_VERSION,
            model_version=MODEL_VERSION,
        )
    )
    assert cached is not None and not cached.parse_failed


def test_apply_results_hands_failures_to_live_task(db, monkeypatch):
    _, role, app_ok = _seed_app(db, email="ok@x.test")
    org = db.get(Organization, app_ok.organization_id)
    _, _, app_bad = _seed_app(db, org=org, role=role, email="bad@x.test")
    _, _, app_err = _seed_app(db, org=org, role=role, email="err@x.test")
    db.commit()

    requeued: list[tuple[int, str | None]] = []
    monkeypatch.setattr(
        "app.tasks.automation_tasks.parse_application_cv_sections.delay",
        lambda app_id, *, origin=None: requeued.append((app_id, origin)),
    )

    entries = [
        _succeeded_entry(app_ok.id),
        # schema violation → ValidationFailure → live task
        _succeeded_entry(app_bad.id, sections={"skills": "not-a-list"}),
        # request-level error → live task
        SimpleNamespace(
            custom_id=custom_id_for(app_err.id),
            result=SimpleNamespace(type="errored"),
        ),
    ]
    context = {
        custom_id_for(app_bad.id): {"origin": CV_PARSE_ORIGIN_NATIVE_APPLY},
        custom_id_for(app_err.id): {"origin": CV_PARSE_ORIGIN_ATS_INGEST},
    }
    summary = apply_batch_results(db, entries, context=context)
    db.commit()

    assert summary == {"applied": 1, "requeued": 2, "skipped": 0, "stale_skipped": 0}
    assert sorted(requeued) == sorted(
        [
            (app_bad.id, CV_PARSE_ORIGIN_NATIVE_APPLY),
            (app_err.id, CV_PARSE_ORIGIN_ATS_INGEST),
        ]
    )
    db.refresh(app_bad)
    assert app_bad.cv_sections is None  # untouched; live task owns it


def test_sweep_failure_skips_dont_consume_budget(db, monkeypatch):
    """Failure-cached rows at the top of the id ordering must not eat the
    sweep limit — older parseable rows would starve every sweep."""
    org, role, older_app = _seed_app(db, email="older@x.test")
    newer_text = CV_TEXT + " Newer distinct CV."
    _, _, newer_app = _seed_app(db, org=org, role=role, cv_text=newer_text, email="newer@x.test")
    assert newer_app.id > older_app.id
    db.commit()

    cache_module.set(
        cache_module.compute_cache_key(
            cv_text=newer_text[:CV_TEXT_CEILING],
            prompt_version=PROMPT_VERSION,
            model_version=MODEL_VERSION,
        ),
        ParsedCV.failed(
            reason="validation_failed_after_retry: nope",
            prompt_version=PROMPT_VERSION,
            model_version=MODEL_VERSION,
        ),
    )

    fake = _FakeBatches()
    _patch_client(monkeypatch, fake)

    summary = sweep_pending_applications(db, limit=1)
    assert summary["cache_failed_skip"] == 1
    assert len(fake.created) == 1
    submitted = {r["custom_id"] for r in fake.created[0]["requests"]}
    assert submitted == {custom_id_for(older_app.id)}  # older row still reached


def test_apply_results_skips_stale_cv(db):
    """A CV replaced while the batch was in flight must not get the stale
    result; it's cached under the submitted text and the row stays pending."""
    _, _, app = _seed_app(db)
    db.commit()

    submitted_key = cache_module.compute_cache_key(
        cv_text=CV_TEXT[:CV_TEXT_CEILING],
        prompt_version=PROMPT_VERSION,
        model_version=MODEL_VERSION,
    )
    context = {custom_id_for(app.id): {"cache_key": submitted_key}}

    # CV replaced mid-flight.
    app.cv_text = "A completely different CV uploaded later."
    db.commit()

    summary = apply_batch_results(db, [_succeeded_entry(app.id)], context=context)
    db.commit()

    assert summary["stale_skipped"] == 1
    assert summary["applied"] == 0
    db.refresh(app)
    assert app.cv_sections is None  # row stays pending for the next sweep
    # Result cached under the text it came from, not the new text.
    assert cache_module.get(submitted_key) is not None


def test_apply_results_matching_key_applies(db):
    _, _, app = _seed_app(db)
    db.commit()
    key = cache_module.compute_cache_key(
        cv_text=CV_TEXT[:CV_TEXT_CEILING],
        prompt_version=PROMPT_VERSION,
        model_version=MODEL_VERSION,
    )
    context = {custom_id_for(app.id): {"cache_key": key}}
    summary = apply_batch_results(db, [_succeeded_entry(app.id)], context=context)
    db.commit()
    assert summary["applied"] == 1 and summary["stale_skipped"] == 0
    db.refresh(app)
    assert app.cv_sections is not None


def test_apply_results_skips_already_parsed(db):
    _, _, app = _seed_app(db)
    app.cv_sections = {"skills": ["existing"]}
    db.commit()

    summary = apply_batch_results(db, [_succeeded_entry(app.id)])
    assert summary == {"applied": 0, "requeued": 0, "skipped": 1, "stale_skipped": 0}
    db.refresh(app)
    assert app.cv_sections == {"skills": ["existing"]}


# ---- enqueue gating ----------------------------------------------------------


def test_on_application_created_respects_batch_flag(db, monkeypatch):
    from app.platform.config import settings
    from app.services.application_events import on_application_created

    _, _, app = _seed_app(db)
    db.commit()

    enqueued: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        "app.tasks.automation_tasks.parse_application_cv_sections.apply_async",
        lambda args, **kw: enqueued.append((args, kw)),
    )
    # Silence the unrelated auto-reject enqueue.
    monkeypatch.setattr(
        "app.tasks.automation_tasks.run_application_auto_reject.delay",
        lambda *a, **k: None,
    )

    monkeypatch.setattr(settings, "CV_PARSE_BATCH_ENABLED", True, raising=False)
    on_application_created(app, parse_origin=CV_PARSE_ORIGIN_NATIVE_APPLY)
    assert enqueued == []  # the batch sweep owns it

    on_application_created(app, parse_origin=CV_PARSE_ORIGIN_RECRUITER_UPLOAD)
    assert enqueued == [
        (
            (app.id,),
            {
                "kwargs": {"origin": CV_PARSE_ORIGIN_RECRUITER_UPLOAD},
                "countdown": 15,
            },
        )
    ]
    enqueued.clear()

    monkeypatch.setattr(settings, "CV_PARSE_BATCH_ENABLED", False, raising=False)
    on_application_created(app, parse_origin=CV_PARSE_ORIGIN_NATIVE_APPLY)
    assert enqueued == [
        (
            (app.id,),
            {
                "kwargs": {"origin": CV_PARSE_ORIGIN_NATIVE_APPLY},
                "countdown": 15,
            },
        )
    ]


def test_on_application_created_paid_hold_suppresses_parse_and_score(db, monkeypatch):
    from app.platform.config import settings
    from app.services.application_events import on_application_created

    _, _, app = _seed_app(db)
    db.commit()

    parse_calls: list[int] = []
    score_calls: list[int] = []
    monkeypatch.setattr(settings, "CV_PARSE_BATCH_ENABLED", False, raising=False)
    monkeypatch.setattr(
        "app.tasks.automation_tasks.parse_application_cv_sections.apply_async",
        lambda args, **kw: parse_calls.append(args[0]),
    )
    monkeypatch.setattr(
        "app.tasks.automation_tasks.run_application_auto_reject.delay",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.services.cv_score_orchestrator.enqueue_score",
        lambda db, application, **kw: score_calls.append(application.id),
    )

    on_application_created(
        app,
        score=True,
        allow_paid_work=False,
        parse_origin=CV_PARSE_ORIGIN_ATS_INGEST,
    )

    assert parse_calls == []
    assert score_calls == []
