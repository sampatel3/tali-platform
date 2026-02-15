from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ...platform.database import get_db
from ...deps import get_current_user
from ...models.user import User
from ...models.organization import Organization
from ...schemas.organization import (
    OrgResponse,
    OrgUpdate,
    WorkableConfigBase,
    WorkableConnect,
)
from ...platform.config import settings
from .access_policy import normalize_allowed_domains

router = APIRouter(prefix="/organizations", tags=["Organizations"])


def _default_workable_config() -> dict:
    return WorkableConfigBase().model_dump()


def _resolved_workable_config(org: Organization) -> dict:
    raw = org.workable_config if isinstance(org.workable_config, dict) else {}
    return WorkableConfigBase(**{**_default_workable_config(), **raw}).model_dump()


def _merge_workable_config(org: Organization, incoming: OrgUpdate) -> dict:
    base = _resolved_workable_config(org)
    if incoming.workable_config is None:
        return base
    updates = incoming.workable_config.model_dump(exclude_none=True)
    return WorkableConfigBase(**{**base, **updates}).model_dump()


@router.get("/me", response_model=OrgResponse)
def get_my_org(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.organization_id:
        raise HTTPException(status_code=404, detail="No organization associated")
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    org.allowed_email_domains = normalize_allowed_domains(getattr(org, "allowed_email_domains", None))
    org.workable_config = _resolved_workable_config(org)
    return org


@router.patch("/me", response_model=OrgResponse)
def update_my_org(
    data: OrgUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if data.name is not None:
        org.name = data.name
    org.workable_config = _merge_workable_config(org, data)
    if data.allowed_email_domains is not None:
        org.allowed_email_domains = normalize_allowed_domains(data.allowed_email_domains)
    if data.sso_enforced is not None:
        org.sso_enforced = data.sso_enforced
    if data.saml_enabled is not None:
        org.saml_enabled = data.saml_enabled
    if data.saml_metadata_url is not None:
        metadata_url = (data.saml_metadata_url or "").strip()
        org.saml_metadata_url = metadata_url or None
    if org.saml_enabled and not org.saml_metadata_url:
        raise HTTPException(status_code=400, detail="saml_metadata_url is required when saml_enabled is true")
    try:
        db.commit()
        db.refresh(org)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update organization")
    org.allowed_email_domains = normalize_allowed_domains(getattr(org, "allowed_email_domains", None))
    org.workable_config = _resolved_workable_config(org)
    return org


@router.get("/workable/authorize-url")
def get_workable_authorize_url(current_user: User = Depends(get_current_user)):
    """Return the Workable OAuth authorize URL for the frontend to redirect to."""
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    if not settings.WORKABLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Workable integration is not configured")
    redirect_uri = f"{settings.FRONTEND_URL}/settings/workable/callback"
    scope = "r_jobs r_candidates w_candidates"
    url = (
        "https://www.workable.com/oauth/authorize"
        f"?client_id={settings.WORKABLE_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        "&resource=user"
        "&response_type=code"
        f"&scope={scope.replace(' ', '+')}"
    )
    return {"url": url}


@router.post("/workable/connect")
def connect_workable(
    data: WorkableConnect,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Exchange Workable OAuth code for access token."""
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    import httpx

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Exchange code for token
    try:
        resp = httpx.post(
            "https://www.workable.com/oauth/token",
            data={
                "client_id": settings.WORKABLE_CLIENT_ID,
                "client_secret": settings.WORKABLE_CLIENT_SECRET,
                "code": data.code,
                "grant_type": "authorization_code",
                "redirect_uri": f"{settings.FRONTEND_URL}/settings/workable/callback",
            },
        )
        resp.raise_for_status()
        token_data = resp.json()
    except Exception as e:
        import logging as _logging
        _logging.getLogger("taali.organizations").exception("Workable OAuth failed")
        raise HTTPException(status_code=400, detail="Workable OAuth failed. Please try again.")

    org.workable_access_token = token_data.get("access_token")
    org.workable_refresh_token = token_data.get("refresh_token")
    org.workable_subdomain = token_data.get("subdomain", "")
    org.workable_connected = True
    org.workable_config = _resolved_workable_config(org)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to store Workable connection")

    return {"success": True, "subdomain": org.workable_subdomain}
