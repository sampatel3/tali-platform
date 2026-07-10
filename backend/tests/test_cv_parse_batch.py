"""cv_parse via the Message Batches API (app/cv_parsing/batch.py).

Pins: request params bit-identical to the sync path, per-org batch
submission with in-flight dedup, cache-hit write-through without an API
call, result application (cv_sections + parse cache), failure hand-off
to the live task, and the CV_PARSE_BATCH_ENABLED gate on the
per-application enqueue.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
from app.models.anthropic_batch_job import AnthropicBatchJob
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.services.metered_anthropic_client import MeteredAnthropicClient

CV_TEXT = "Jane Doe. Senior engineer at Acme Corp since 2019. Python, SQL."


def _seed_app(db, *, org=None, role=None, cv_text=CV_TEXT, email="c@x.test"):
    if org is None:
        org = Organization(name="O", slug=f"o-{id(db)}-{email}")
        db.add(org)
        db.flush()
    if role is None:
        role = Role(
            organization_id=org.id, name="R", source="manual", job_spec_text="hire"
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
        source="manual",
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

    # Second sweep: everything is in-flight, nothing resubmitted.
    summary2 = sweep_pending_applications(db)
    assert summary2["in_flight"] == 3
    assert len(fake.created) == 2


def test_sweep_excludes_closed_and_archived_roles(db, monkeypatch):
    """Apps on dead Workable reqs are excluded in SQL — the 2026-06 audit's
    dead-req-spend lesson. Manual roles (no workable_job_data) count as live."""
    org, live_role, live_app = _seed_app(db, email="live@x.test")
    for state, email in (("archived", "arch@x.test"), ("closed", "closed@x.test")):
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
    published_role = Role(
        organization_id=org.id,
        name="pub",
        source="manual",
        job_spec_text="hire",
        workable_job_data={"state": "published"},
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

    assert summary == {"applied": 1, "requeued": 0, "skipped": 0}
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

    requeued: list[int] = []
    monkeypatch.setattr(
        "app.tasks.automation_tasks.parse_application_cv_sections.delay",
        lambda app_id: requeued.append(app_id),
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
    summary = apply_batch_results(db, entries)
    db.commit()

    assert summary == {"applied": 1, "requeued": 2, "skipped": 0}
    assert sorted(requeued) == sorted([app_bad.id, app_err.id])
    db.refresh(app_bad)
    assert app_bad.cv_sections is None  # untouched; live task owns it


def test_apply_results_skips_already_parsed(db):
    _, _, app = _seed_app(db)
    app.cv_sections = {"skills": ["existing"]}
    db.commit()

    summary = apply_batch_results(db, [_succeeded_entry(app.id)])
    assert summary == {"applied": 0, "requeued": 0, "skipped": 1}
    db.refresh(app)
    assert app.cv_sections == {"skills": ["existing"]}


# ---- enqueue gating ----------------------------------------------------------


def test_on_application_created_respects_batch_flag(db, monkeypatch):
    from app.platform.config import settings
    from app.services.application_events import on_application_created

    _, _, app = _seed_app(db)
    db.commit()

    enqueued: list[tuple] = []
    monkeypatch.setattr(
        "app.tasks.automation_tasks.parse_application_cv_sections.apply_async",
        lambda args, **kw: enqueued.append(args),
    )
    # Silence the unrelated auto-reject enqueue.
    monkeypatch.setattr(
        "app.tasks.automation_tasks.run_application_auto_reject.delay",
        lambda *a, **k: None,
    )

    monkeypatch.setattr(settings, "CV_PARSE_BATCH_ENABLED", True, raising=False)
    on_application_created(app)
    assert enqueued == []  # the batch sweep owns it

    monkeypatch.setattr(settings, "CV_PARSE_BATCH_ENABLED", False, raising=False)
    on_application_created(app)
    assert enqueued == [(app.id,)]  # live path unchanged
