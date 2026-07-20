"""In-app API-key management (the Developers settings surface).

Workspace-owner-only and org-scoped. Mint / list / revoke keys. The plaintext
secret is returned exactly once, on create.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...deps import require_org_owner
from ...models.api_key import API_KEY_SCOPES, ApiKey
from ...models.user import User
from ...platform.database import get_db
from ...services.api_key_service import mint_api_key

router = APIRouter(prefix="/api-keys", tags=["API keys"])


class CreateApiKeyPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    scopes: Optional[list[str]] = Field(
        default=None,
        description="Subset of the scope vocabulary; omit for read-only defaults.",
    )
    is_test: bool = False
    expires_at: Optional[datetime] = None


class ApiKeyResponse(BaseModel):
    id: int
    name: str
    prefix: str
    is_test: bool
    scopes: list[str]
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class CreatedApiKeyResponse(ApiKeyResponse):
    # The plaintext secret — present ONLY in the create response.
    secret: str


class ApiKeyListResponse(BaseModel):
    keys: list[ApiKeyResponse]
    available_scopes: list[str]


def _serialize(key: ApiKey) -> dict:
    return {
        "id": key.id,
        "name": key.name,
        "prefix": key.prefix,
        "is_test": key.is_test,
        "scopes": key.scopes or [],
        "last_used_at": key.last_used_at,
        "expires_at": key.expires_at,
        "revoked_at": key.revoked_at,
        "created_at": key.created_at,
    }


@router.post("", response_model=CreatedApiKeyResponse)
def create_api_key(
    payload: CreateApiKeyPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    try:
        minted = mint_api_key(
            db,
            organization_id=current_user.organization_id,
            name=payload.name,
            scopes=payload.scopes,
            is_test=payload.is_test,
            expires_at=payload.expires_at,
            created_by_user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    data = _serialize(minted.api_key)
    data["secret"] = minted.secret
    return data


@router.get("", response_model=ApiKeyListResponse)
def list_api_keys(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    keys = (
        db.query(ApiKey)
        .filter(ApiKey.organization_id == current_user.organization_id)
        .order_by(ApiKey.created_at.desc())
        .all()
    )
    return {
        "keys": [_serialize(k) for k in keys],
        "available_scopes": sorted(API_KEY_SCOPES),
    }


@router.delete("/{key_id}", response_model=ApiKeyResponse)
def revoke_api_key(
    key_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    key = (
        db.query(ApiKey)
        .filter(
            ApiKey.id == key_id,
            ApiKey.organization_id == current_user.organization_id,
        )
        .first()
    )
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(key)
    return _serialize(key)
