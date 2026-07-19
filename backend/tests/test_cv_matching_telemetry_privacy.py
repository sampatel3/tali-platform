from __future__ import annotations

import json
import logging
import time
from types import SimpleNamespace

from app.cv_matching import telemetry
from app.cv_matching.schemas import ScoringStatus


def _context(**overrides):
    values = {
        "trace_id": "trace-safe-123",
        "cv_hash": "deadbeef01234567",
        "jd_hash": "feedface01234567",
        "started_at": time.monotonic() - 0.01,
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 20,
        "cache_creation_tokens": 10,
        "retry_count": 1,
        "validation_failures": 0,
        "cache_hit": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_fallback_trace_reaches_root_once_without_raw_payload(monkeypatch, caplog):
    secret = "candidate@example.test bearer-private-cv-and-jd"
    monkeypatch.setattr(telemetry, "_trace_log_path", lambda: "")
    caplog.set_level(logging.INFO, logger="taali.cv_match.trace")

    telemetry.emit_trace(
        _context(trace_id=secret, cv_hash=secret, jd_hash=secret, raw_cv=secret),
        final_status=ScoringStatus.OK,
    )

    records = [
        record for record in caplog.records
        if record.name == "taali.cv_match.trace" and record.levelno == logging.INFO
    ]
    assert telemetry.trace_logger.propagate is True
    assert telemetry.trace_logger.handlers == []
    assert len(records) == 1
    assert secret not in caplog.text
    assert records[0].getMessage().count("CV match trace") == 1
    row = telemetry.recent_traces(1)[0]
    assert row["trace_id"] == "unknown"
    assert row["cv_hash"] == "unknown"
    assert row["jd_hash"] == "unknown"


def test_configured_file_suppresses_duplicate_root_trace(monkeypatch, tmp_path, caplog):
    path = tmp_path / "cv-match-traces.jsonl"
    monkeypatch.setattr(telemetry, "_trace_log_path", lambda: str(path))
    caplog.set_level(logging.INFO, logger="taali.cv_match.trace")

    telemetry.emit_trace(_context(), final_status=ScoringStatus.DEFERRED)

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["trace_id"] == "trace-safe-123"
    assert not [
        record for record in caplog.records
        if record.name == "taali.cv_match.trace" and record.levelno == logging.INFO
    ]
