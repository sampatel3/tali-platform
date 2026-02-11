from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ...core.database import get_db
from ...core.security import get_current_user
from ...models.candidate import Candidate
from ...models.user import User
from ...schemas.candidate import CandidateCreate, CandidateResponse, CandidateUpdate, DocumentUploadResponse
from ...services.document_service import process_document_upload

router = APIRouter(prefix="/candidates", tags=["Candidates"])


def _candidate_to_response(c: Candidate) -> dict:
    """Serialize a candidate, adding text previews for documents."""
    data = CandidateResponse.model_validate(c).model_dump()
    data["cv_text_preview"] = (c.cv_text[:500] + "...") if c.cv_text and len(c.cv_text) > 500 else c.cv_text
    data["job_spec_text_preview"] = (c.job_spec_text[:500] + "...") if c.job_spec_text and len(c.job_spec_text) > 500 else c.job_spec_text
    return data


@router.get("/")
def list_candidates(
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Candidate).filter(Candidate.organization_id == current_user.organization_id)
    if q:
        like = f"%{q}%"
        query = query.filter((Candidate.full_name.ilike(like)) | (Candidate.email.ilike(like)))
    total = query.count()
    items = query.order_by(Candidate.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "items": [_candidate_to_response(c) for c in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/", response_model=CandidateResponse, status_code=status.HTTP_201_CREATED)
def create_candidate(
    data: CandidateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = db.query(Candidate).filter(
        Candidate.organization_id == current_user.organization_id,
        Candidate.email == data.email,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Candidate email already exists")
    candidate = Candidate(
        organization_id=current_user.organization_id,
        email=data.email,
        full_name=data.full_name,
        position=data.position,
    )
    db.add(candidate)
    try:
        db.commit()
        db.refresh(candidate)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create candidate")
    return candidate


@router.get("/{candidate_id}")
def get_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.organization_id == current_user.organization_id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return _candidate_to_response(candidate)


@router.patch("/{candidate_id}")
def update_candidate(
    candidate_id: int,
    data: CandidateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.organization_id == current_user.organization_id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(candidate, k, v)
    try:
        db.commit()
        db.refresh(candidate)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update candidate")
    return _candidate_to_response(candidate)


@router.delete("/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.organization_id == current_user.organization_id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    try:
        db.delete(candidate)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete candidate")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Document uploads
# ---------------------------------------------------------------------------

def _get_candidate_for_org(candidate_id: int, org_id: int, db: Session) -> Candidate:
    candidate = db.query(Candidate).filter(
        Candidate.id == candidate_id,
        Candidate.organization_id == org_id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate


@router.post("/{candidate_id}/upload-cv", response_model=DocumentUploadResponse)
def upload_candidate_cv(
    candidate_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a CV for a candidate. Extracts text for matching."""
    candidate = _get_candidate_for_org(candidate_id, current_user.organization_id, db)

    result = process_document_upload(
        upload=file,
        entity_id=candidate_id,
        doc_type="cv",
        allowed_extensions={"pdf", "docx"},
    )

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    candidate.cv_file_url = result["file_url"]
    candidate.cv_filename = result["filename"]
    candidate.cv_text = result["extracted_text"]
    candidate.cv_uploaded_at = now

    try:
        db.commit()
        db.refresh(candidate)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to store CV")

    return DocumentUploadResponse(
        candidate_id=candidate.id,
        doc_type="cv",
        filename=result["filename"],
        text_preview=result["text_preview"],
        uploaded_at=now,
    )


@router.post("/{candidate_id}/upload-job-spec", response_model=DocumentUploadResponse)
def upload_candidate_job_spec(
    candidate_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a job specification for a candidate. Extracts text for matching."""
    candidate = _get_candidate_for_org(candidate_id, current_user.organization_id, db)

    result = process_document_upload(
        upload=file,
        entity_id=candidate_id,
        doc_type="job_spec",
        allowed_extensions={"pdf", "docx", "txt"},
    )

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    candidate.job_spec_file_url = result["file_url"]
    candidate.job_spec_filename = result["filename"]
    candidate.job_spec_text = result["extracted_text"]
    candidate.job_spec_uploaded_at = now

    try:
        db.commit()
        db.refresh(candidate)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to store job specification")

    return DocumentUploadResponse(
        candidate_id=candidate.id,
        doc_type="job_spec",
        filename=result["filename"],
        text_preview=result["text_preview"],
        uploaded_at=now,
    )
