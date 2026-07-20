from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from app.components.assessments.repository import bind_candidate_session
from app.components.assessments.repository import utcnow


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass
class CandidateProofSigner:
    private_key: ec.EllipticCurvePrivateKey = field(default_factory=lambda: ec.generate_private_key(ec.SECP256R1()))

    @property
    def public_jwk(self) -> dict[str, str]:
        numbers = self.private_key.public_key().public_numbers()
        return {
            "kty": "EC",
            "crv": "P-256",
            "x": _b64url(numbers.x.to_bytes(32, "big")),
            "y": _b64url(numbers.y.to_bytes(32, "big")),
        }

    @property
    def key_id(self) -> str:
        jwk = self.public_jwk
        material = f"{jwk['crv']}.{jwk['x']}.{jwk['y']}".encode("utf-8")
        return _b64url(hashlib.sha256(material).digest())

    def headers(
        self,
        *,
        method: str,
        path_and_query: str,
        raw_body: bytes = b"",
        timestamp: int | None = None,
        nonce: str | None = None,
    ) -> dict[str, str]:
        timestamp_text = str(int(time.time()) if timestamp is None else int(timestamp))
        nonce_text = nonce or secrets.token_urlsafe(24)
        body_sha256 = hashlib.sha256(raw_body).hexdigest()
        canonical = (
            f"v1\n{method.upper()}\n{path_and_query}\n{body_sha256}\n"
            f"{timestamp_text}\n{nonce_text}"
        ).encode("utf-8")
        der = self.private_key.sign(canonical, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return {
            "X-Assessment-Key-Id": self.key_id,
            "X-Assessment-Proof-Timestamp": timestamp_text,
            "X-Assessment-Proof-Nonce": nonce_text,
            "X-Assessment-Proof": _b64url(raw_signature),
        }

    def start_body(self, *, session_key: str, **extra: Any) -> bytes:
        payload = {
            **extra,
            "candidate_session_key": session_key,
            "candidate_proof_key_id": self.key_id,
            "candidate_proof_public_jwk": self.public_jwk,
        }
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    def bind_assessment(self, assessment: Any, *, session_key: str) -> None:
        bind_candidate_session(assessment, session_key)
        assessment.candidate_proof_key_id = self.key_id
        assessment.candidate_proof_public_jwk = self.public_jwk
        assessment.candidate_proof_key_bound_at = utcnow()


def compact_json_body(payload: Any) -> bytes:
    """Match httpx's compact UTF-8 JSON request encoding."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def signed_candidate_headers(
    signer: CandidateProofSigner,
    *,
    token: str,
    session_key: str,
    method: str,
    path_and_query: str,
    raw_body: bytes = b"",
) -> dict[str, str]:
    return {
        "X-Assessment-Token": token,
        "X-Assessment-Session": session_key,
        **signer.headers(
            method=method,
            path_and_query=path_and_query,
            raw_body=raw_body,
        ),
    }
