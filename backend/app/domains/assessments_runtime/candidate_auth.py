from __future__ import annotations

import logging
from collections.abc import Generator

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from ...components.assessments.repository import (
    claim_runtime_operation,
    get_active_assessment,
    release_runtime_operation,
    validate_assessment_token,
    validate_candidate_session,
)
from ...components.assessments.service import enforce_active_or_timeout, enforce_not_paused
from ...models.assessment import Assessment
from ...platform.database import get_db

from .candidate_proof import (
    PROOF_KEY_ID_HEADER,
    PROOF_NONCE_HEADER,
    PROOF_SIGNATURE_HEADER,
    PROOF_TIMESTAMP_HEADER,
    headers_from_values,
    request_path_and_query,
    verify_and_consume_candidate_runtime_proof,
)


logger = logging.getLogger(__name__)


def validate_runtime_candidate_session(
    assessment: Assessment,
    session_key: str | None,
) -> None:
    """Require the browser-bound secret on every live assessment."""
    if not session_key:
        raise HTTPException(status_code=403, detail="Invalid candidate session")
    validate_candidate_session(assessment, session_key)


async def require_candidate_request_proof(
    assessment_id: int,
    request: Request,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    x_assessment_session: str | None = Header(
        None,
        description="Live candidate browser session key",
    ),
    x_assessment_key_id: str | None = Header(None, alias=PROOF_KEY_ID_HEADER),
    x_assessment_proof_timestamp: str | None = Header(None, alias=PROOF_TIMESTAMP_HEADER),
    x_assessment_proof_nonce: str | None = Header(None, alias=PROOF_NONCE_HEADER),
    x_assessment_proof: str | None = Header(None, alias=PROOF_SIGNATURE_HEADER),
) -> None:
    """Verify and durably consume a signed live-runtime request."""
    if not x_assessment_session:
        raise HTTPException(status_code=403, detail="Invalid candidate session")
    headers = headers_from_values(
        key_id=x_assessment_key_id,
        timestamp=x_assessment_proof_timestamp,
        nonce=x_assessment_proof_nonce,
        signature=x_assessment_proof,
    )
    verify_and_consume_candidate_runtime_proof(
        assessment_id=assessment_id,
        assessment_token=x_assessment_token,
        candidate_session_key=x_assessment_session,
        headers=headers,
        method=request.method,
        path_and_query=request_path_and_query(request),
        raw_body=await request.body(),
    )


def candidate_runtime_operation(kind: str):
    """Build a yield dependency that leases one candidate workspace mutation."""

    def dependency(
        assessment_id: int,
        x_assessment_token: str = Header(..., description="Assessment access token"),
        x_assessment_session: str | None = Header(
            None,
            description="Live candidate browser session key",
        ),
        db: Session = Depends(get_db),
        _request_proof: None = Depends(require_candidate_request_proof),
    ) -> Generator[str, None, None]:
        assessment = get_active_assessment(assessment_id, db)
        validate_assessment_token(assessment, x_assessment_token)
        validate_runtime_candidate_session(assessment, x_assessment_session)
        enforce_active_or_timeout(assessment, db)
        enforce_not_paused(assessment)
        operation_id = claim_runtime_operation(assessment, db, kind=kind)
        try:
            yield operation_id
        finally:
            try:
                release_runtime_operation(assessment.id, db, operation_id)
            except Exception:
                db.rollback()
                logger.exception(
                    "Failed to release candidate runtime operation assessment_id=%s kind=%s",
                    assessment.id,
                    kind,
                )

    dependency.__name__ = f"claim_{kind}_runtime_operation"
    return dependency
