"""Proof-of-possession for candidate assessment runtime requests.

The invite token and browser session secret identify an assessment, but both
are ordinary bearer strings. Live runtime requests additionally carry an
ECDSA P-256 signature made by a browser-generated, non-extractable WebCrypto
key. A one-use nonce is committed through an independent database session so
an endpoint rollback cannot make a signed request replayable.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi import HTTPException, Request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ...components.assessments.repository import (
    bind_candidate_session,
    validate_assessment_token,
    validate_candidate_session,
)
from ...models.assessment import (
    Assessment,
    AssessmentStatus,
    CandidateAssessmentProofNonce,
)
from ...platform import database as database_platform


PROOF_VERSION = "v1"
PROOF_CLOCK_SKEW_SECONDS = 120
PROOF_NONCE_RETENTION_SECONDS = 600
PROOF_KEY_ID_HEADER = "X-Assessment-Key-Id"
PROOF_TIMESTAMP_HEADER = "X-Assessment-Proof-Timestamp"
PROOF_NONCE_HEADER = "X-Assessment-Proof-Nonce"
PROOF_SIGNATURE_HEADER = "X-Assessment-Proof"

_KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_TIMESTAMP_RE = re.compile(r"^(?:0|[1-9][0-9]{0,11})$")
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class CandidateProofHeaders:
    key_id: str
    timestamp: str
    nonce: str
    signature: str


@dataclass(frozen=True)
class CandidateStartProofAdmission:
    assessment_id: int
    normalized_public_jwk: dict[str, str]


def _proof_error(*, status_code: int = 403, detail: str = "Invalid candidate request proof") -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def _base64url_decode(value: object, *, expected_length: int | None = None) -> bytes:
    encoded = str(value or "")
    if not encoded or not _B64URL_RE.fullmatch(encoded):
        raise _proof_error()
    try:
        decoded = base64.urlsafe_b64decode(encoded + ("=" * (-len(encoded) % 4)))
    except (ValueError, TypeError) as exc:
        raise _proof_error() from exc
    if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != encoded:
        raise _proof_error()
    if expected_length is not None and len(decoded) != expected_length:
        raise _proof_error()
    return decoded


def normalize_public_jwk(raw_jwk: object) -> dict[str, str]:
    """Validate and return the public-only canonical P-256 JWK."""
    if not isinstance(raw_jwk, Mapping):
        raise _proof_error(status_code=422, detail="candidate_proof_public_jwk is required")
    if "d" in raw_jwk:
        raise _proof_error()
    if raw_jwk.get("kty") != "EC" or raw_jwk.get("crv") != "P-256":
        raise _proof_error()
    if raw_jwk.get("alg") not in (None, "ES256"):
        raise _proof_error()
    x = str(raw_jwk.get("x") or "")
    y = str(raw_jwk.get("y") or "")
    x_bytes = _base64url_decode(x, expected_length=32)
    y_bytes = _base64url_decode(y, expected_length=32)
    try:
        ec.EllipticCurvePublicNumbers(
            int.from_bytes(x_bytes, "big"),
            int.from_bytes(y_bytes, "big"),
            ec.SECP256R1(),
        ).public_key()
    except ValueError as exc:
        raise _proof_error() from exc
    return {"kty": "EC", "crv": "P-256", "x": x, "y": y}


def derive_candidate_proof_key_id(public_jwk: Mapping[str, str]) -> str:
    """Derive the browser/backend key id without relying on JSON key order."""
    material = f"{public_jwk['crv']}.{public_jwk['x']}.{public_jwk['y']}".encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(material).digest()).rstrip(b"=").decode("ascii")


def canonical_candidate_proof(
    *,
    method: str,
    path_and_query: str,
    raw_body: bytes,
    timestamp: str,
    nonce: str,
) -> bytes:
    """Return the exact bytes signed by the candidate browser.

    Format (no trailing newline)::

        v1\nMETHOD\n/path?raw=query\nsha256(raw-body)-hex\nunix-seconds\nnonce
    """
    body_sha256 = hashlib.sha256(raw_body).hexdigest()
    return (
        f"{PROOF_VERSION}\n{str(method).upper()}\n{path_and_query}\n"
        f"{body_sha256}\n{timestamp}\n{nonce}"
    ).encode("utf-8")


def request_path_and_query(request: Request) -> str:
    """Preserve the on-wire encoded path and raw query order for signing."""
    raw_path = request.scope.get("raw_path")
    if not isinstance(raw_path, bytes):
        raw_path = str(request.url.path).encode("ascii")
    else:
        # ASGI defines ``raw_path`` without the query string, but some in-
        # process transports expose the complete raw target here as well as in
        # ``query_string``.  Always take the path component from ``raw_path``
        # and the query component from the dedicated scope field so the
        # canonical target is neither decoded nor duplicated.
        raw_path = raw_path.split(b"?", 1)[0]
    raw_query = request.scope.get("query_string", b"")
    try:
        path = raw_path.decode("ascii")
        query = raw_query.decode("ascii") if isinstance(raw_query, bytes) else str(raw_query)
    except UnicodeDecodeError as exc:
        raise _proof_error() from exc
    return f"{path}?{query}" if query else path


def _validated_timestamp_and_nonce(
    headers: CandidateProofHeaders,
    *,
    now: int | None = None,
) -> int:
    if not _KEY_ID_RE.fullmatch(str(headers.key_id or "")):
        raise _proof_error()
    if not _TIMESTAMP_RE.fullmatch(str(headers.timestamp or "")):
        raise _proof_error()
    if not _NONCE_RE.fullmatch(str(headers.nonce or "")):
        raise _proof_error()
    timestamp = int(headers.timestamp)
    current = int(time.time()) if now is None else int(now)
    if abs(current - timestamp) > PROOF_CLOCK_SKEW_SECONDS:
        raise _proof_error(detail="Candidate request proof has expired")
    return timestamp


def _public_key(public_jwk: Mapping[str, str]) -> ec.EllipticCurvePublicKey:
    x = int.from_bytes(_base64url_decode(public_jwk["x"], expected_length=32), "big")
    y = int.from_bytes(_base64url_decode(public_jwk["y"], expected_length=32), "big")
    try:
        return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    except ValueError as exc:
        raise _proof_error() from exc


def verify_candidate_proof_signature(
    *,
    public_jwk: Mapping[str, str],
    headers: CandidateProofHeaders,
    method: str,
    path_and_query: str,
    raw_body: bytes,
    now: int | None = None,
) -> int:
    timestamp = _validated_timestamp_and_nonce(headers, now=now)
    raw_signature = _base64url_decode(headers.signature, expected_length=64)
    r = int.from_bytes(raw_signature[:32], "big")
    s = int.from_bytes(raw_signature[32:], "big")
    if r == 0 or s == 0:
        raise _proof_error()
    canonical = canonical_candidate_proof(
        method=method,
        path_and_query=path_and_query,
        raw_body=raw_body,
        timestamp=headers.timestamp,
        nonce=headers.nonce,
    )
    try:
        _public_key(public_jwk).verify(
            encode_dss_signature(r, s),
            canonical,
            ec.ECDSA(hashes.SHA256()),
        )
    except InvalidSignature as exc:
        raise _proof_error() from exc
    return timestamp


def _bound_public_jwk(assessment: Assessment) -> dict[str, str]:
    key_id = getattr(assessment, "candidate_proof_key_id", None)
    public_jwk = getattr(assessment, "candidate_proof_public_jwk", None)
    bound_at = getattr(assessment, "candidate_proof_key_bound_at", None)
    if not key_id or not public_jwk or not bound_at:
        raise _proof_error(detail="Candidate proof key is not bound")
    normalized = normalize_public_jwk(public_jwk)
    if not secrets.compare_digest(str(key_id), derive_candidate_proof_key_id(normalized)):
        raise _proof_error()
    return normalized


def bind_candidate_proof_key(
    assessment: Assessment,
    *,
    candidate_key_id: str,
    candidate_public_jwk: object,
) -> bool:
    """Bind one public key on the caller's assessment transaction.

    Keeping this mutation in the same transaction as workspace provisioning
    preserves atomic start: provisioning failure rolls back the browser
    bindings, while the independently committed proof nonce remains consumed.
    """
    normalized_jwk = normalize_public_jwk(candidate_public_jwk)
    derived_key_id = derive_candidate_proof_key_id(normalized_jwk)
    if not candidate_key_id or not secrets.compare_digest(candidate_key_id, derived_key_id):
        raise _proof_error()
    existing_key_id = str(getattr(assessment, "candidate_proof_key_id", None) or "")
    existing_jwk = getattr(assessment, "candidate_proof_public_jwk", None)
    existing_bound_at = getattr(assessment, "candidate_proof_key_bound_at", None)
    if existing_key_id or existing_jwk or existing_bound_at:
        if not existing_key_id or not existing_jwk or not existing_bound_at:
            raise _proof_error(status_code=503, detail="Candidate proof binding is invalid")
        stored_jwk = normalize_public_jwk(existing_jwk)
        if not secrets.compare_digest(existing_key_id, candidate_key_id) or stored_jwk != normalized_jwk:
            raise HTTPException(
                status_code=409,
                detail="Assessment is already active in another browser session",
            )
        return False
    assessment.candidate_proof_key_id = candidate_key_id
    assessment.candidate_proof_public_jwk = normalized_jwk
    assessment.candidate_proof_key_bound_at = datetime.now(timezone.utc)
    return True


def _consume_nonce(
    *,
    db: Any,
    assessment_id: int,
    headers: CandidateProofHeaders,
    proof_timestamp: int,
    retention_reference_time: int,
) -> None:
    # A proof older than the clock-skew window cannot become valid again. Keep
    # a much wider replay window, then prune per assessment in the same atomic
    # transaction as the new nonce insert so retained assessments do not grow
    # this table without bound.
    db.query(CandidateAssessmentProofNonce).filter(
        CandidateAssessmentProofNonce.assessment_id == int(assessment_id),
        CandidateAssessmentProofNonce.proof_timestamp
        < int(retention_reference_time) - PROOF_NONCE_RETENTION_SECONDS,
    ).delete(synchronize_session=False)
    db.add(
        CandidateAssessmentProofNonce(
            assessment_id=int(assessment_id),
            nonce=headers.nonce,
            key_id=headers.key_id,
            proof_timestamp=proof_timestamp,
        )
    )
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _proof_error(
            status_code=409,
            detail="Candidate request proof has already been used",
        ) from exc


def verify_and_consume_candidate_start_proof(
    *,
    token: str,
    session_key: str,
    candidate_key_id: str,
    candidate_public_jwk: object,
    headers: CandidateProofHeaders,
    method: str,
    path_and_query: str,
    raw_body: bytes,
    now: int | None = None,
) -> CandidateStartProofAdmission:
    """Verify start and durably consume its nonce before provisioning.

    This transaction deliberately does not persist the browser session or key;
    the route binds both on its workspace-start transaction so a failed first
    provisioning attempt still leaves the assessment pending and unbound.
    """
    normalized_jwk = normalize_public_jwk(candidate_public_jwk)
    derived_key_id = derive_candidate_proof_key_id(normalized_jwk)
    if not candidate_key_id or not secrets.compare_digest(candidate_key_id, derived_key_id):
        raise _proof_error()
    if not secrets.compare_digest(headers.key_id, candidate_key_id):
        raise _proof_error()
    proof_timestamp = verify_candidate_proof_signature(
        public_jwk=normalized_jwk,
        headers=headers,
        method=method,
        path_and_query=path_and_query,
        raw_body=raw_body,
        now=now,
    )

    with database_platform.SessionLocal() as proof_db:
        try:
            assessment = (
                proof_db.query(Assessment)
                .filter(Assessment.token == token)
                .first()
            )
            if not assessment:
                raise HTTPException(status_code=404, detail="Invalid assessment token")
            if bool(getattr(assessment, "is_voided", False)):
                raise HTTPException(status_code=400, detail="assessment_voided")
            if assessment.status not in {
                AssessmentStatus.PENDING,
                AssessmentStatus.IN_PROGRESS,
            }:
                raise HTTPException(status_code=400, detail="Assessment already submitted")

            existing_key_id = str(getattr(assessment, "candidate_proof_key_id", None) or "")
            existing_jwk = getattr(assessment, "candidate_proof_public_jwk", None)
            existing_bound_at = getattr(assessment, "candidate_proof_key_bound_at", None)
            if existing_key_id or existing_jwk or existing_bound_at:
                if not existing_key_id or not existing_jwk or not existing_bound_at:
                    raise _proof_error(status_code=503, detail="Candidate proof binding is invalid")
                stored_jwk = normalize_public_jwk(existing_jwk)
                if not secrets.compare_digest(existing_key_id, candidate_key_id) or stored_jwk != normalized_jwk:
                    raise HTTPException(
                        status_code=409,
                        detail="Assessment is already active in another browser session",
                    )
            if getattr(assessment, "candidate_session_hash", None):
                # Preserve the start contract's explicit "another browser"
                # conflict while keeping first binding on the main start
                # transaction (this call cannot mutate an already-bound row).
                bind_candidate_session(assessment, session_key)
            _consume_nonce(
                db=proof_db,
                assessment_id=assessment.id,
                headers=headers,
                proof_timestamp=proof_timestamp,
                retention_reference_time=int(time.time()) if now is None else int(now),
            )
            proof_db.commit()
            return CandidateStartProofAdmission(
                assessment_id=int(assessment.id),
                normalized_public_jwk=normalized_jwk,
            )
        except HTTPException:
            proof_db.rollback()
            raise
        except SQLAlchemyError as exc:
            proof_db.rollback()
            raise _proof_error(
                status_code=503,
                detail="Candidate request proof could not be recorded",
            ) from exc


def verify_and_consume_candidate_runtime_proof(
    *,
    assessment_id: int,
    assessment_token: str,
    candidate_session_key: str,
    headers: CandidateProofHeaders,
    method: str,
    path_and_query: str,
    raw_body: bytes,
    now: int | None = None,
) -> None:
    """Verify a bound key and durably consume this request's nonce."""
    with database_platform.SessionLocal() as proof_db:
        try:
            assessment = proof_db.query(Assessment).filter(Assessment.id == int(assessment_id)).first()
            if not assessment or bool(getattr(assessment, "is_voided", False)):
                raise HTTPException(status_code=404, detail="Active assessment not found")
            validate_assessment_token(assessment, assessment_token)
            validate_candidate_session(assessment, candidate_session_key)
            public_jwk = _bound_public_jwk(assessment)
            if not secrets.compare_digest(str(assessment.candidate_proof_key_id), headers.key_id):
                raise _proof_error()
            proof_timestamp = verify_candidate_proof_signature(
                public_jwk=public_jwk,
                headers=headers,
                method=method,
                path_and_query=path_and_query,
                raw_body=raw_body,
                now=now,
            )
            _consume_nonce(
                db=proof_db,
                assessment_id=assessment.id,
                headers=headers,
                proof_timestamp=proof_timestamp,
                retention_reference_time=int(time.time()) if now is None else int(now),
            )
            proof_db.commit()
        except HTTPException:
            proof_db.rollback()
            raise
        except SQLAlchemyError as exc:
            proof_db.rollback()
            raise _proof_error(
                status_code=503,
                detail="Candidate request proof could not be recorded",
            ) from exc


def headers_from_values(
    *,
    key_id: str | None,
    timestamp: str | None,
    nonce: str | None,
    signature: str | None,
) -> CandidateProofHeaders:
    if not key_id or not timestamp or not nonce or not signature:
        raise _proof_error(detail="Candidate request proof is required")
    return CandidateProofHeaders(
        key_id=str(key_id),
        timestamp=str(timestamp),
        nonce=str(nonce),
        signature=str(signature),
    )
