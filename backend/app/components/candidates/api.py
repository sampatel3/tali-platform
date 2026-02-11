from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ...platform.database import get_db
from ...platform.security import get_current_user
from ...models.candidate import Candidate
from ...models.user import User
from ...schemas.candidate import CandidateCreate, CandidateResponse, CandidateUpdate

router = APIRouter(prefix="/candidates", tags=["Candidates"])


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
        "items": [CandidateResponse.model_validate(c).model_dump() for c in items],
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


@router.get("/{candidate_id}", response_model=CandidateResponse)
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
    return candidate


@router.patch("/{candidate_id}", response_model=CandidateResponse)
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
    return candidate


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
