"""Transaction boundaries for Anthropic batch polling tasks."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.cv_parsing.batch import in_flight_application_ids
from app.models.anthropic_batch_job import AnthropicBatchJob
from app.tasks.anthropic_batch_tasks import poll_cv_parse_batches
from tests.conftest import TestingSessionLocal


def test_batch_poll_releases_sql_transaction_before_provider_retrieve(db):
    db.add(
        AnthropicBatchJob(
            batch_id=f"msgbatch-boundary-{id(db)}",
            feature="cv_parse",
            request_count=1,
            status="submitted",
            context={"cvparse-1": {"origin": "workable_autonomous"}},
        )
    )
    db.commit()
    worker_db = Session(bind=db.get_bind())

    def retrieve(batch_id):
        assert batch_id.startswith("msgbatch-boundary-")
        assert worker_db.in_transaction() is False
        return SimpleNamespace(processing_status="in_progress")

    client = SimpleNamespace(
        messages=SimpleNamespace(
            batches=SimpleNamespace(retrieve=retrieve),
        )
    )
    with (
        patch("app.platform.database.SessionLocal", return_value=worker_db),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=client,
        ),
    ):
        result = poll_cv_parse_batches.run()

    assert result["status"] == "ok"
    assert result["open"] == 1
    assert result["polled"][0]["status"] == "in_progress"


def test_ended_metered_batch_recovers_after_crash_without_resubmission(db):
    batch_id = f"msgbatch-crash-window-{id(db)}"
    db.add(
        AnthropicBatchJob(
            batch_id=batch_id,
            feature="cv_parse",
            request_count=1,
            status="submitted",
            context={"cvparse-42": {"origin": "workable_autonomous"}},
        )
    )
    db.commit()

    class MeteringBatchAPI:
        retrieve_calls = 0
        results_calls = 0

        def retrieve(self, requested_batch_id):
            assert requested_batch_id == batch_id
            self.retrieve_calls += 1
            return SimpleNamespace(processing_status="ended")

        def results(self, requested_batch_id):
            assert requested_batch_id == batch_id
            self.results_calls += 1
            # Match MeteredAnthropicClient's independent durable boundary:
            # spend is latched and the row is ended before local result apply.
            with TestingSessionLocal() as meter_db:
                row = meter_db.query(AnthropicBatchJob).filter_by(batch_id=batch_id).one()
                if row.metered_at is None:
                    row.metered_at = datetime.now(timezone.utc)
                    row.metered_count = 1
                    row.status = "ended"
                    meter_db.commit()
            return iter((SimpleNamespace(custom_id="cvparse-42"),))

    batches = MeteringBatchAPI()
    client = SimpleNamespace(messages=SimpleNamespace(batches=batches))

    def crash_before_apply(*_args, **_kwargs):
        raise SystemExit("simulated worker crash after durable metering")

    with (
        patch("app.platform.database.SessionLocal", TestingSessionLocal),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=client,
        ),
        patch(
            "app.cv_parsing.batch.apply_batch_results",
            side_effect=crash_before_apply,
        ),
        pytest.raises(SystemExit, match="simulated worker crash"),
    ):
        poll_cv_parse_batches.run()

    db.expire_all()
    stranded = db.query(AnthropicBatchJob).filter_by(batch_id=batch_id).one()
    assert stranded.status == "ended"
    assert stranded.metered_count == 1
    assert "_result_application" not in stranded.context
    assert in_flight_application_ids(db) == {42}
    db.rollback()

    applied_summary = {
        "applied": 1,
        "requeued": 0,
        "skipped": 0,
        "stale_skipped": 0,
    }
    with (
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
        recovered = poll_cv_parse_batches.run()

    assert recovered == {
        "status": "ok",
        "open": 1,
        "polled": [{"batch_id": batch_id, "status": "ended", **applied_summary}],
    }
    apply_results.assert_called_once()

    db.expire_all()
    completed = db.query(AnthropicBatchJob).filter_by(batch_id=batch_id).one()
    assert completed.status == "results_applied"
    assert completed.metered_count == 1
    assert completed.context["_result_application"]["summary"] == applied_summary
    assert batches.retrieve_calls == 2
    assert batches.results_calls == 2
    db.rollback()

    with (
        patch("app.platform.database.SessionLocal", TestingSessionLocal),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=client,
        ),
        patch(
            "app.cv_parsing.batch.apply_batch_results",
            side_effect=AssertionError("completed results must not be reapplied"),
        ),
    ):
        already_complete = poll_cv_parse_batches.run()

    assert already_complete == {"status": "ok", "open": 0}
    assert batches.retrieve_calls == 2
    assert batches.results_calls == 2


def test_batch_poll_waits_for_durable_metering_before_applying_results(db):
    batch_id = f"msgbatch-metering-retry-{id(db)}"
    db.add(
        AnthropicBatchJob(
            batch_id=batch_id,
            feature="cv_parse",
            request_count=1,
            status="submitted",
            context={"cvparse-43": {"origin": "workable_autonomous"}},
        )
    )
    db.commit()

    class RetryMeteringBatchAPI:
        results_calls = 0

        def retrieve(self, requested_batch_id):
            assert requested_batch_id == batch_id
            return SimpleNamespace(processing_status="ended")

        def results(self, requested_batch_id):
            assert requested_batch_id == batch_id
            self.results_calls += 1
            if self.results_calls == 2:
                with TestingSessionLocal() as meter_db:
                    row = (
                        meter_db.query(AnthropicBatchJob)
                        .filter_by(batch_id=batch_id)
                        .one()
                    )
                    row.metered_at = datetime.now(timezone.utc)
                    row.metered_count = 1
                    row.status = "ended"
                    meter_db.commit()
            # The real wrapper also returns entries when its metering helper
            # swallows a write failure on the first pass.
            return iter((SimpleNamespace(custom_id="cvparse-43"),))

    batches = RetryMeteringBatchAPI()
    client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
    applied_summary = {
        "applied": 1,
        "requeued": 0,
        "skipped": 0,
        "stale_skipped": 0,
    }

    with (
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

        assert first_poll == {
            "status": "ok",
            "open": 1,
            "polled": [{"batch_id": batch_id, "status": "metering_pending"}],
        }
        apply_results.assert_not_called()
        db.expire_all()
        pending = db.query(AnthropicBatchJob).filter_by(batch_id=batch_id).one()
        assert pending.status == "submitted"
        assert pending.metered_at is None
        assert "_result_application" not in pending.context
        db.rollback()

        second_poll = poll_cv_parse_batches.run()

    assert second_poll == {
        "status": "ok",
        "open": 1,
        "polled": [{"batch_id": batch_id, "status": "ended", **applied_summary}],
    }
    apply_results.assert_called_once()
    db.expire_all()
    completed = db.query(AnthropicBatchJob).filter_by(batch_id=batch_id).one()
    assert completed.status == "results_applied"
    assert completed.metered_count == 1
    assert completed.context["_result_application"]["summary"] == applied_summary
