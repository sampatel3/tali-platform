from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.components.assessments.repository import bind_candidate_session
from app.domains.assessments_runtime import candidate_proof
from app.domains.assessments_runtime import candidate_runtime_routes as runtime
from app.models.assessment import (
    Assessment,
    AssessmentStatus,
    CandidateAssessmentProofNonce,
)
from app.models.task import Task
from tests.candidate_proof_helpers import (
    CandidateProofSigner,
    signed_candidate_headers,
)
from tests.conftest import TestingSessionLocal


SESSION_KEY = "candidate-browser-session-key-0123456789"


@pytest.fixture(autouse=True)
def _proof_session_uses_test_database(monkeypatch):
    monkeypatch.setattr(candidate_proof.database_platform, "SessionLocal", TestingSessionLocal)


def _headers(raw: dict[str, str]) -> candidate_proof.CandidateProofHeaders:
    return candidate_proof.headers_from_values(
        key_id=raw.get("X-Assessment-Key-Id"),
        timestamp=raw.get("X-Assessment-Proof-Timestamp"),
        nonce=raw.get("X-Assessment-Proof-Nonce"),
        signature=raw.get("X-Assessment-Proof"),
    )


def _assessment(db, *, token: str = "proof-token", status=AssessmentStatus.PENDING) -> Assessment:
    task = Task(
        name="Candidate proof",
        task_key=f"candidate-proof-{token}",
        repo_structure={"files": {"src/main.py": "print('ok')"}},
        duration_minutes=30,
    )
    db.add(task)
    db.flush()
    assessment = Assessment(
        task_id=task.id,
        token=token,
        status=status,
        duration_minutes=30,
        started_at=datetime.now(timezone.utc) if status == AssessmentStatus.IN_PROGRESS else None,
    )
    db.add(assessment)
    db.commit()
    return assessment


def _bind_start(db, assessment: Assessment, signer: CandidateProofSigner):
    path = f"/api/v1/assessments/token/{assessment.token}/start"
    raw_body = signer.start_body(session_key=SESSION_KEY)
    raw_headers = signer.headers(method="POST", path_and_query=path, raw_body=raw_body)
    admission = candidate_proof.verify_and_consume_candidate_start_proof(
        token=assessment.token,
        session_key=SESSION_KEY,
        candidate_key_id=signer.key_id,
        candidate_public_jwk=signer.public_jwk,
        headers=_headers(raw_headers),
        method="POST",
        path_and_query=path,
        raw_body=raw_body,
    )
    persisted = db.query(Assessment).filter(Assessment.id == admission.assessment_id).one()
    bind_candidate_session(persisted, SESSION_KEY)
    candidate_proof.bind_candidate_proof_key(
        persisted,
        candidate_key_id=signer.key_id,
        candidate_public_jwk=admission.normalized_public_jwk,
    )
    db.commit()
    db.expire_all()
    return admission, raw_headers


def test_canonical_proof_binds_raw_query_and_body_hash() -> None:
    canonical = candidate_proof.canonical_candidate_proof(
        method="post",
        path_and_query="/api/v1/assessments/7/repo-file?path=src%2Fmain.py&mode=a",
        raw_body=b'{"content":"x"}',
        timestamp="1770000000",
        nonce="abcdefghijklmnop",
    ).decode("utf-8")
    assert canonical == (
        "v1\nPOST\n/api/v1/assessments/7/repo-file?path=src%2Fmain.py&mode=a\n"
        "ee2b252d1cd491425942090e06507c7337b5279df43af31ab718b1b1b5da8708\n"
        "1770000000\nabcdefghijklmnop"
    )


def test_request_target_does_not_duplicate_query_from_raw_path() -> None:
    path = "/api/v1/assessments/7/repo-file"
    query = "path=src%2Fmain.py&mode=a"
    request = SimpleNamespace(
        scope={
            "raw_path": f"{path}?{query}".encode("ascii"),
            "query_string": query.encode("ascii"),
        },
        url=SimpleNamespace(path=path),
    )

    assert candidate_proof.request_path_and_query(request) == f"{path}?{query}"


def test_start_atomically_binds_public_key_session_and_nonce(db) -> None:
    assessment = _assessment(db)
    signer = CandidateProofSigner()

    admission, raw_headers = _bind_start(db, assessment, signer)

    assert admission.assessment_id == assessment.id
    persisted = db.query(Assessment).filter(Assessment.id == assessment.id).one()
    assert persisted.candidate_session_hash != SESSION_KEY
    assert persisted.candidate_proof_key_id == signer.key_id
    assert persisted.candidate_proof_public_jwk == signer.public_jwk
    assert persisted.candidate_proof_key_bound_at is not None
    nonce = (
        db.query(CandidateAssessmentProofNonce)
        .filter(CandidateAssessmentProofNonce.assessment_id == assessment.id)
        .one()
    )
    assert nonce.nonce == raw_headers["X-Assessment-Proof-Nonce"]


def test_database_rejects_partial_candidate_proof_binding(db) -> None:
    assessment = _assessment(db, token="partial-proof-binding-token")
    assessment.candidate_proof_key_id = "K" * 43

    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_invalid_start_signature_binds_nothing_and_consumes_nothing(db) -> None:
    assessment = _assessment(db)
    signer = CandidateProofSigner()
    path = f"/api/v1/assessments/token/{assessment.token}/start"
    raw_body = signer.start_body(session_key=SESSION_KEY)
    signed_for_different_body = signer.headers(
        method="POST",
        path_and_query=path,
        raw_body=b'{"different":true}',
    )

    with pytest.raises(HTTPException) as rejected:
        candidate_proof.verify_and_consume_candidate_start_proof(
            token=assessment.token,
            session_key=SESSION_KEY,
            candidate_key_id=signer.key_id,
            candidate_public_jwk=signer.public_jwk,
            headers=_headers(signed_for_different_body),
            method="POST",
            path_and_query=path,
            raw_body=raw_body,
        )
    assert rejected.value.status_code == 403
    db.expire_all()
    persisted = db.query(Assessment).filter(Assessment.id == assessment.id).one()
    assert persisted.candidate_session_hash is None
    assert persisted.candidate_proof_key_id is None
    assert db.query(CandidateAssessmentProofNonce).count() == 0


def test_runtime_proof_is_one_use_and_survives_unrelated_rollback(db) -> None:
    assessment = _assessment(db)
    signer = CandidateProofSigner()
    _bind_start(db, assessment, signer)
    assessment.status = AssessmentStatus.IN_PROGRESS
    assessment.started_at = datetime.now(timezone.utc)
    db.commit()

    path = f"/api/v1/assessments/{assessment.id}/repo-file?path=src%2Fmain.py"
    raw_headers = signer.headers(method="GET", path_and_query=path)
    kwargs = {
        "assessment_id": assessment.id,
        "assessment_token": assessment.token,
        "candidate_session_key": SESSION_KEY,
        "headers": _headers(raw_headers),
        "method": "GET",
        "path_and_query": path,
        "raw_body": b"",
    }
    candidate_proof.verify_and_consume_candidate_runtime_proof(**kwargs)

    assessment.tab_switch_count = 999
    db.rollback()
    db.expire_all()
    assert (
        db.query(CandidateAssessmentProofNonce)
        .filter(
            CandidateAssessmentProofNonce.assessment_id == assessment.id,
            CandidateAssessmentProofNonce.nonce == raw_headers["X-Assessment-Proof-Nonce"],
        )
        .count()
        == 1
    )
    with pytest.raises(HTTPException) as replayed:
        candidate_proof.verify_and_consume_candidate_runtime_proof(**kwargs)
    assert replayed.value.status_code == 409


def test_stale_runtime_proof_fails_closed_without_consuming_nonce(db) -> None:
    assessment = _assessment(db)
    signer = CandidateProofSigner()
    _bind_start(db, assessment, signer)
    assessment.status = AssessmentStatus.IN_PROGRESS
    assessment.started_at = datetime.now(timezone.utc)
    db.commit()

    path = f"/api/v1/assessments/{assessment.id}/submit"
    raw_body = json.dumps({"final_code": "done"}, separators=(",", ":")).encode()
    raw_headers = signer.headers(
        method="POST",
        path_and_query=path,
        raw_body=raw_body,
        timestamp=int(time.time()) - candidate_proof.PROOF_CLOCK_SKEW_SECONDS - 1,
    )
    with pytest.raises(HTTPException) as stale:
        candidate_proof.verify_and_consume_candidate_runtime_proof(
            assessment_id=assessment.id,
            assessment_token=assessment.token,
            candidate_session_key=SESSION_KEY,
            headers=_headers(raw_headers),
            method="POST",
            path_and_query=path,
            raw_body=raw_body,
        )
    assert stale.value.status_code == 403
    assert "expired" in str(stale.value.detail).lower()
    assert (
        db.query(CandidateAssessmentProofNonce)
        .filter(CandidateAssessmentProofNonce.nonce == raw_headers["X-Assessment-Proof-Nonce"])
        .count()
        == 0
    )


def test_valid_request_prunes_only_expired_nonces_for_same_assessment(db) -> None:
    assessment = _assessment(db)
    other = _assessment(db, token="other-proof-token")
    signer = CandidateProofSigner()
    _bind_start(db, assessment, signer)
    assessment.status = AssessmentStatus.IN_PROGRESS
    assessment.started_at = datetime.now(timezone.utc)
    db.add_all(
        [
            CandidateAssessmentProofNonce(
                assessment_id=assessment.id,
                nonce="expirednonceforassessment",
                key_id=signer.key_id,
                proof_timestamp=1,
            ),
            CandidateAssessmentProofNonce(
                assessment_id=other.id,
                nonce="expirednonceforotherrow",
                key_id=signer.key_id,
                proof_timestamp=1,
            ),
        ]
    )
    db.commit()

    now = int(time.time())
    path = f"/api/v1/assessments/{assessment.id}/runtime-event"
    raw_body = b'{"event_type":"runtime_loaded"}'
    raw_headers = signer.headers(
        method="POST",
        path_and_query=path,
        raw_body=raw_body,
        timestamp=now,
    )
    candidate_proof.verify_and_consume_candidate_runtime_proof(
        assessment_id=assessment.id,
        assessment_token=assessment.token,
        candidate_session_key=SESSION_KEY,
        headers=_headers(raw_headers),
        method="POST",
        path_and_query=path,
        raw_body=raw_body,
        now=now,
    )

    db.expire_all()
    assert (
        db.query(CandidateAssessmentProofNonce)
        .filter(
            CandidateAssessmentProofNonce.assessment_id == assessment.id,
            CandidateAssessmentProofNonce.nonce == "expirednonceforassessment",
        )
        .count()
        == 0
    )
    assert (
        db.query(CandidateAssessmentProofNonce)
        .filter(
            CandidateAssessmentProofNonce.assessment_id == other.id,
            CandidateAssessmentProofNonce.nonce == "expirednonceforotherrow",
        )
        .count()
        == 1
    )


def test_start_route_requires_and_accepts_proof_over_exact_raw_json(client, db, monkeypatch) -> None:
    assessment = _assessment(db, token="proof-route-token")
    signer = CandidateProofSigner()
    path = f"/api/v1/assessments/token/{assessment.token}/start"
    raw_body = signer.start_body(session_key=SESSION_KEY)

    def fake_start(row, session):
        session.commit()
        return {
            "assessment_id": row.id,
            "task": {"repo_structure": {"files": {"src/main.py": "secret"}}},
            "time_remaining": 1800,
        }

    monkeypatch.setattr(runtime, "start_or_resume_assessment", fake_start)
    missing = client.post(path, content=raw_body, headers={"Content-Type": "application/json"})
    assert missing.status_code == 403

    proof_headers = signer.headers(method="POST", path_and_query=path, raw_body=raw_body)
    accepted = client.post(
        path,
        content=raw_body,
        headers={"Content-Type": "application/json", **proof_headers},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["task"]["repo_structure"] == {"files": {"src/main.py": ""}}


def test_live_runtime_route_requires_and_accepts_bound_request_proof(client, db) -> None:
    assessment = _assessment(db, token="proof-runtime-route-token", status=AssessmentStatus.IN_PROGRESS)
    signer = CandidateProofSigner()
    signer.bind_assessment(assessment, session_key=SESSION_KEY)
    db.commit()
    path = f"/api/v1/assessments/{assessment.id}/runtime-event"
    raw_body = b'{"event_type":"runtime_loaded"}'

    missing = client.post(
        path,
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Assessment-Token": assessment.token,
            "X-Assessment-Session": SESSION_KEY,
        },
    )
    assert missing.status_code == 403

    headers = signed_candidate_headers(
        signer,
        token=assessment.token,
        session_key=SESSION_KEY,
        method="POST",
        path_and_query=path,
        raw_body=raw_body,
    )
    accepted = client.post(
        path,
        content=raw_body,
        headers={"Content-Type": "application/json", **headers},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json() == {"recorded": True}


def test_failed_first_start_rolls_back_bindings_but_keeps_nonce_consumed(
    client, db, monkeypatch,
) -> None:
    assessment = _assessment(db, token="proof-failed-start-token")
    signer = CandidateProofSigner()
    path = f"/api/v1/assessments/token/{assessment.token}/start"
    raw_body = signer.start_body(session_key=SESSION_KEY)
    proof_headers = signer.headers(method="POST", path_and_query=path, raw_body=raw_body)

    def fail_start(_row, session):
        session.rollback()
        raise HTTPException(status_code=503, detail="workspace provisioning failed")

    monkeypatch.setattr(runtime, "start_or_resume_assessment", fail_start)
    response = client.post(
        path,
        content=raw_body,
        headers={"Content-Type": "application/json", **proof_headers},
    )
    assert response.status_code == 503

    db.expire_all()
    persisted = db.query(Assessment).filter(Assessment.id == assessment.id).one()
    assert persisted.status == AssessmentStatus.PENDING
    assert persisted.candidate_session_hash is None
    assert persisted.candidate_session_bound_at is None
    assert persisted.candidate_proof_key_id is None
    assert persisted.candidate_proof_public_jwk is None
    assert persisted.candidate_proof_key_bound_at is None
    assert (
        db.query(CandidateAssessmentProofNonce)
        .filter(
            CandidateAssessmentProofNonce.assessment_id == assessment.id,
            CandidateAssessmentProofNonce.nonce == proof_headers["X-Assessment-Proof-Nonce"],
        )
        .count()
        == 1
    )
