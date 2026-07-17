"""Recovery of known-accepted Anthropic batch submission anchors."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.cv_parsing.batch import in_flight_application_ids
from app.models.anthropic_batch_job import AnthropicBatchJob
from app.models.organization import Organization
from app.services import anthropic_batch_recovery as recovery
from app.services import anthropic_batch_submission as submission
from app.tasks.anthropic_batch_tasks import poll_cv_parse_batches
from tests.conftest import TestingSessionLocal


def _claim_context(
    *,
    claim_batch_id: str,
    attempt_id: str,
    application_id: int,
    organization_id: int,
    state: str,
    provider_batch_id: str | None = None,
) -> dict:
    claim = {
        "version": 2,
        "state": state,
        "claim_batch_id": claim_batch_id,
        "request_sha256": claim_batch_id.removeprefix("claim:cv_parse:"),
        "request_count": 1,
        "attempt": 1,
        "attempt_id": attempt_id,
    }
    if provider_batch_id is not None:
        claim["provider_batch_id"] = provider_batch_id
    return {
        f"cvparse-{application_id}": {
            "organization_id": organization_id,
            "entity_id": f"application:{application_id}",
            "origin": "workable_autonomous",
        },
        "_submission_claim": claim,
    }


def _add_claim(
    db,
    *,
    claim_batch_id: str,
    attempt_id: str,
    application_id: int,
    state: str,
    status: str,
    provider_batch_id: str | None = None,
    organization: Organization | None = None,
) -> AnthropicBatchJob:
    if organization is None:
        organization = Organization(
            name=f"Batch recovery {application_id}",
            slug=f"batch-recovery-{application_id}-{id(db)}",
        )
        db.add(organization)
        db.flush()
    row = AnthropicBatchJob(
        batch_id=claim_batch_id,
        organization_id=int(organization.id),
        feature="cv_parse",
        model="claude-haiku-4-5",
        request_count=1,
        status=status,
        context=_claim_context(
            claim_batch_id=claim_batch_id,
            attempt_id=attempt_id,
            application_id=application_id,
            organization_id=int(organization.id),
            state=state,
            provider_batch_id=provider_batch_id,
        ),
    )
    db.add(row)
    db.flush()
    return row


def test_poll_recovers_known_accepted_batch_after_anchor_finalize_failure(db):
    claim_batch_id = "claim:cv_parse:known-accepted"
    provider_batch_id = "msgbatch_known_accepted"
    attempt_id = "attempt-known-accepted"
    claim = _add_claim(
        db,
        claim_batch_id=claim_batch_id,
        attempt_id=attempt_id,
        application_id=42,
        state="provider_attempt_started",
        status="submitting",
    )
    claim_id = int(claim.id)
    organization_id = int(claim.organization_id)
    db.commit()

    # Anthropic returned a real id, then the local synthetic-id rename failed.
    with (
        patch.object(submission, "SessionLocal", TestingSessionLocal),
        patch.object(
            submission,
            "_finalize_submission_claim",
            side_effect=RuntimeError("injected local anchor write failure"),
        ),
        pytest.raises(RuntimeError, match="injected local anchor"),
    ):
        submission.record_batch_submission(
            batch_id=provider_batch_id,
            feature="cv_parse",
            organization_id=organization_id,
            by_custom_id={"cvparse-42": {"entity_id": "application:42"}},
            requests=[{"params": {"model": "claude-haiku-4-5"}}],
            claim_batch_id=claim_batch_id,
            claim_attempt_id=attempt_id,
        )

    db.expire_all()
    blocked = db.get(AnthropicBatchJob, claim_id)
    assert blocked.batch_id == claim_batch_id
    assert blocked.status == "submission_ambiguous"
    assert (
        blocked.context["_submission_claim"]["state"]
        == "provider_accepted_anchor_finalize_failed"
    )
    assert (
        blocked.context["_submission_claim"]["provider_batch_id"]
        == provider_batch_id
    )
    assert in_flight_application_ids(db) == {42}
    db.rollback()

    class PollOnlyBatchAPI:
        create_calls = 0
        retrieve_calls = 0
        results_calls = 0

        def create(self, **_kwargs):
            self.create_calls += 1
            raise AssertionError("known-accepted recovery must never resubmit")

        def retrieve(self, requested_batch_id):
            assert requested_batch_id == provider_batch_id
            self.retrieve_calls += 1
            return SimpleNamespace(processing_status="ended")

        def results(self, requested_batch_id):
            assert requested_batch_id == provider_batch_id
            self.results_calls += 1
            with TestingSessionLocal() as meter_db:
                row = (
                    meter_db.query(AnthropicBatchJob)
                    .filter_by(batch_id=provider_batch_id)
                    .one()
                )
                row.metered_at = datetime.now(timezone.utc)
                row.metered_count = 1
                row.status = "ended"
                meter_db.commit()
            return iter((SimpleNamespace(custom_id="cvparse-42"),))

    batches = PollOnlyBatchAPI()
    client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
    applied_summary = {
        "applied": 1,
        "requeued": 0,
        "skipped": 0,
        "stale_skipped": 0,
    }
    with (
        patch.object(submission, "SessionLocal", TestingSessionLocal),
        patch.object(recovery, "SessionLocal", TestingSessionLocal),
        patch("app.platform.database.SessionLocal", TestingSessionLocal),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=client,
        ),
        patch(
            "app.cv_parsing.batch.apply_batch_results",
            return_value=applied_summary,
        ) as apply_results,
    ):
        first_poll = poll_cv_parse_batches.run()
        second_recovery = submission.recover_known_accepted_batch_submissions(
            feature="cv_parse"
        )
        second_poll = poll_cv_parse_batches.run()

    assert first_poll == {
        "status": "ok",
        "open": 1,
        "polled": [
            {"batch_id": provider_batch_id, "status": "ended", **applied_summary}
        ],
    }
    assert second_recovery == {
        "recovered": 0,
        "already_owned": 0,
        "collisions": 0,
        "errors": 0,
    }
    assert second_poll == {"status": "ok", "open": 0}
    assert batches.create_calls == 0
    assert batches.retrieve_calls == 1
    assert batches.results_calls == 1
    apply_results.assert_called_once()

    db.expire_all()
    recovered = db.get(AnthropicBatchJob, claim_id)
    assert recovered.batch_id == provider_batch_id
    assert recovered.status == "results_applied"
    assert recovered.metered_at is not None
    assert recovered.metered_count == 1
    assert recovered.context["_submission_claim"]["state"] == "submitted"
    assert recovered.context["_submission_claim"]["anchor_recovered_at"]
    assert recovered.context["_result_application"]["summary"] == applied_summary
    assert db.query(AnthropicBatchJob).count() == 1


def test_recovery_refuses_foreign_provider_batch_id_collision(db):
    provider_batch_id = "msgbatch_foreign_collision"
    source = _add_claim(
        db,
        claim_batch_id="claim:cv_parse:collision-source",
        attempt_id="attempt-source",
        application_id=51,
        state="provider_accepted_anchor_finalize_failed",
        status="submission_ambiguous",
        provider_batch_id=provider_batch_id,
    )
    foreign = _add_claim(
        db,
        claim_batch_id=provider_batch_id,
        attempt_id="attempt-foreign",
        application_id=99,
        state="submitted",
        status="results_applied",
        provider_batch_id=provider_batch_id,
    )
    source_id = int(source.id)
    foreign_id = int(foreign.id)
    foreign_context = foreign.context
    db.commit()

    with patch.object(recovery, "SessionLocal", TestingSessionLocal):
        result = submission.recover_known_accepted_batch_submissions(
            feature="cv_parse"
        )
        repeated = submission.recover_known_accepted_batch_submissions(
            feature="cv_parse"
        )

    assert result == {
        "recovered": 0,
        "already_owned": 0,
        "collisions": 1,
        "errors": 0,
    }
    assert repeated == {
        "recovered": 0,
        "already_owned": 0,
        "collisions": 1,
        "errors": 0,
    }
    db.expire_all()
    blocked = db.get(AnthropicBatchJob, source_id)
    untouched = db.get(AnthropicBatchJob, foreign_id)
    assert blocked.batch_id == "claim:cv_parse:collision-source"
    assert blocked.status == "submission_ambiguous"
    assert (
        blocked.context["_submission_claim"]["state"]
        == "provider_accepted_anchor_finalize_failed"
    )
    assert untouched.batch_id == provider_batch_id
    assert untouched.context == foreign_context


def test_recovery_refuses_distinct_matching_anchor_and_ignores_ambiguous_claim(db):
    provider_batch_id = "msgbatch_exact_owner"
    source = _add_claim(
        db,
        claim_batch_id="claim:cv_parse:exact-owner",
        attempt_id="attempt-exact-owner",
        application_id=61,
        state="provider_accepted_anchor_finalize_failed",
        status="submission_ambiguous",
        provider_batch_id=provider_batch_id,
    )
    source_id = int(source.id)
    owner_context = _claim_context(
        claim_batch_id=source.batch_id,
        attempt_id="attempt-exact-owner",
        application_id=61,
        organization_id=int(source.organization_id),
        state="submitted",
        provider_batch_id=provider_batch_id,
    )
    owner = AnthropicBatchJob(
        batch_id=provider_batch_id,
        organization_id=source.organization_id,
        feature=source.feature,
        model=source.model,
        request_count=source.request_count,
        status="submitted",
        context=owner_context,
    )
    db.add(owner)
    ambiguous = _add_claim(
        db,
        claim_batch_id="claim:cv_parse:truly-ambiguous",
        attempt_id="attempt-unknown",
        application_id=62,
        state="provider_outcome_ambiguous",
        status="submission_ambiguous",
    )
    owner_id = int(owner.id)
    ambiguous_id = int(ambiguous.id)
    db.commit()

    with patch.object(recovery, "SessionLocal", TestingSessionLocal):
        result = submission.recover_known_accepted_batch_submissions(
            feature="cv_parse"
        )

    assert result == {
        "recovered": 0,
        "already_owned": 0,
        "collisions": 1,
        "errors": 0,
    }
    db.expire_all()
    blocked = db.get(AnthropicBatchJob, source_id)
    exact_owner = db.get(AnthropicBatchJob, owner_id)
    still_ambiguous = db.get(AnthropicBatchJob, ambiguous_id)
    assert blocked.status == "submission_ambiguous"
    assert (
        blocked.context["_submission_claim"]["state"]
        == "provider_accepted_anchor_finalize_failed"
    )
    assert exact_owner.batch_id == provider_batch_id
    assert exact_owner.status == "submitted"
    assert still_ambiguous.status == "submission_ambiguous"
    assert (
        still_ambiguous.context["_submission_claim"]["state"]
        == "provider_outcome_ambiguous"
    )


def test_submission_finalizer_rejects_changed_state_org_feature_or_context(db):
    claim_batch_id = "claim:cv_parse:finalizer-ownership"
    attempt_id = "attempt-finalizer-ownership"
    row = _add_claim(
        db,
        claim_batch_id=claim_batch_id,
        attempt_id=attempt_id,
        application_id=71,
        state="provider_attempt_started",
        status="submitting",
    )
    row_id = int(row.id)
    organization_id = int(row.organization_id)
    by_custom_id = {
        key: value
        for key, value in row.context.items()
        if not key.startswith("_")
    }
    requests = [
        {
            "custom_id": "cvparse-71",
            "params": {"model": "claude-haiku-4-5"},
        }
    ]
    db.commit()

    with patch.object(submission, "SessionLocal", TestingSessionLocal):
        with pytest.raises(
            submission.BatchSubmissionAnchorError,
            match="caller ownership",
        ):
            submission._finalize_submission_claim(
                claim_batch_id=claim_batch_id,
                claim_attempt_id=attempt_id,
                batch_id="msgbatch_wrong_org",
                feature="cv_parse",
                organization_id=organization_id + 1,
                by_custom_id=by_custom_id,
                requests=requests,
            )
        with pytest.raises(
            submission.BatchSubmissionAnchorError,
            match="caller ownership",
        ):
            submission._finalize_submission_claim(
                claim_batch_id=claim_batch_id,
                claim_attempt_id=attempt_id,
                batch_id="msgbatch_wrong_feature",
                feature="other",
                organization_id=organization_id,
                by_custom_id=by_custom_id,
                requests=requests,
            )
        changed_context = {
            "cvparse-71": {
                **by_custom_id["cvparse-71"],
                "organization_id": organization_id + 1,
            }
        }
        with pytest.raises(
            submission.BatchSubmissionAnchorError,
            match="attribution",
        ):
            submission._finalize_submission_claim(
                claim_batch_id=claim_batch_id,
                claim_attempt_id=attempt_id,
                batch_id="msgbatch_wrong_context",
                feature="cv_parse",
                organization_id=organization_id,
                by_custom_id=changed_context,
                requests=requests,
            )

    db.expire_all()
    unchanged = db.get(AnthropicBatchJob, row_id)
    assert unchanged.batch_id == claim_batch_id
    assert unchanged.status == "submitting"
    assert (
        unchanged.context["_submission_claim"]["state"]
        == "provider_attempt_started"
    )

    changed_context = dict(unchanged.context)
    changed_claim = dict(changed_context["_submission_claim"])
    changed_claim["state"] = "claimed"
    changed_context["_submission_claim"] = changed_claim
    unchanged.context = changed_context
    db.commit()
    with (
        patch.object(submission, "SessionLocal", TestingSessionLocal),
        pytest.raises(
            submission.BatchSubmissionAnchorError,
            match="claim ownership changed",
        ),
    ):
        submission._finalize_submission_claim(
            claim_batch_id=claim_batch_id,
            claim_attempt_id=attempt_id,
            batch_id="msgbatch_wrong_state",
            feature="cv_parse",
            organization_id=organization_id,
            by_custom_id=by_custom_id,
            requests=requests,
        )

    db.expire_all()
    still_blocked = db.get(AnthropicBatchJob, row_id)
    assert still_blocked.batch_id == claim_batch_id
    assert still_blocked.status == "submitting"
    assert still_blocked.context["_submission_claim"]["state"] == "claimed"


def test_recovery_rejects_corrupt_tenant_context(db):
    claim_batch_id = "claim:cv_parse:corrupt-tenant"
    provider_batch_id = "msgbatch_corrupt_tenant"
    row = _add_claim(
        db,
        claim_batch_id=claim_batch_id,
        attempt_id="attempt-corrupt-tenant",
        application_id=72,
        state="provider_accepted_anchor_finalize_failed",
        status="submission_ambiguous",
        provider_batch_id=provider_batch_id,
    )
    row_id = int(row.id)
    context = dict(row.context)
    per_context = dict(context["cvparse-72"])
    per_context["organization_id"] = int(row.organization_id) + 1
    context["cvparse-72"] = per_context
    row.context = context
    db.commit()

    with patch.object(recovery, "SessionLocal", TestingSessionLocal):
        result = submission.recover_known_accepted_batch_submissions(
            feature="cv_parse"
        )

    assert result == {
        "recovered": 0,
        "already_owned": 0,
        "collisions": 0,
        "errors": 0,
    }
    db.expire_all()
    blocked = db.get(AnthropicBatchJob, row_id)
    assert blocked.batch_id == claim_batch_id
    assert blocked.status == "submission_ambiguous"
    assert (
        blocked.context["_submission_claim"]["state"]
        == "provider_accepted_anchor_finalize_failed"
    )
