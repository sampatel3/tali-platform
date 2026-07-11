import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.orm import Session
from ...components.integrations.workable.service import WorkableService
from ...platform.database import get_db
from ...deps import get_current_user
from ...models.user import User
from ...models.organization import Organization
from ...schemas.organization import (
    OrgResponse,
    OrgUpdate,
    WorkableConfigBase,
    WorkableConnect,
    WorkableTokenConnect,
)
from ...platform.config import settings
from ...platform.secrets import encrypt_text
from .access_policy import normalize_allowed_domains
from .organization_serialization import (
    merge_ai_tooling_config,
    merge_notification_preferences,
    merge_scoring_policy,
    merge_workable_config,
    merge_workspace_settings,
    org_response_payload,
    resolved_ai_tooling_config,
    resolved_notification_preferences,
    resolved_scoring_policy,
    resolved_workable_config,
    resolved_workspace_settings,
)

router = APIRouter(prefix="/organizations", tags=["Organizations"])
_SUBDOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_ALLOWED_WORKABLE_SCOPES = ("r_jobs", "r_candidates", "w_candidates")
_EMAIL_ADAPTER = TypeAdapter(EmailStr)

# Access-policy fields only workspace owners may change — these control who can
# join the workspace and how they authenticate. Everything else on the org
# PATCH (agent defaults, notifications, integrations) stays open to members.
_OWNER_ONLY_ORG_FIELDS = {
    "allowed_email_domains",
    "sso_enforced",
    "saml_enabled",
    "saml_metadata_url",
    "two_factor_required",
}


def _normalized_optional_email(value: str | None, *, field_name: str) -> str | None:
    raw = (value or "").strip().lower()
    if not raw:
        return None
    try:
        return str(_EMAIL_ADAPTER.validate_python(raw))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid {field_name}") from exc


def _is_workable_oauth_configured() -> bool:
    placeholders = {
        "",
        "skip",
        "changeme",
        "your-workable-client-id",
        "your-workable-client-secret",
    }
    client_id = (settings.WORKABLE_CLIENT_ID or "").strip()
    client_secret = (settings.WORKABLE_CLIENT_SECRET or "").strip()
    return client_id.lower() not in placeholders and client_secret.lower() not in placeholders


def _normalized_workable_subdomain(value: str) -> str:
    subdomain = (value or "").strip().lower()
    if subdomain.endswith(".workable.com"):
        subdomain = subdomain[: -len(".workable.com")]
    return subdomain


def _workable_oauth_scope(org: Organization) -> str:
    config = resolved_workable_config(org)
    if bool(config.get("workable_writeback")):
        return "r_jobs r_candidates w_candidates"
    return "r_jobs r_candidates"


def _parsed_scope_tokens(raw_scopes: str | None) -> list[str] | None:
    if raw_scopes is None:
        return None
    tokens = [token.strip() for token in re.split(r"[,\s]+", raw_scopes) if token.strip()]
    if not tokens:
        raise HTTPException(status_code=400, detail="No scopes provided")

    normalized: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token not in _ALLOWED_WORKABLE_SCOPES:
            raise HTTPException(status_code=400, detail=f"Unsupported scope: {token}")
        if token not in seen:
            normalized.append(token)
            seen.add(token)

    required_missing = {"r_jobs", "r_candidates"} - set(normalized)
    if required_missing:
        raise HTTPException(status_code=400, detail="Scopes must include r_jobs and r_candidates")
    return normalized


def _scope_tokens_for_storage(raw_scopes: str | None, *, fallback: list[str]) -> list[str]:
    if raw_scopes is None:
        return list(fallback)
    tokens = [token.strip() for token in re.split(r"[,\s]+", raw_scopes) if token.strip()]
    normalized: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token not in _ALLOWED_WORKABLE_SCOPES or token in seen:
            continue
        normalized.append(token)
        seen.add(token)
    return normalized or list(fallback)


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
    if getattr(org, "default_assessment_duration_minutes", None) is None:
        org.default_assessment_duration_minutes = 30
    org.allowed_email_domains = normalize_allowed_domains(getattr(org, "allowed_email_domains", None))
    org.workable_config = resolved_workable_config(org)
    org.workspace_settings = resolved_workspace_settings(org)
    org.scoring_policy = resolved_scoring_policy(org)
    org.ai_tooling_config = resolved_ai_tooling_config(org)
    org.notification_preferences = resolved_notification_preferences(org)
    return org_response_payload(org)


@router.patch("/me", response_model=OrgResponse)
def update_my_org(
    data: OrgUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    touched_owner_fields = _OWNER_ONLY_ORG_FIELDS & data.model_fields_set
    if touched_owner_fields and getattr(current_user, "role", None) != "owner":
        raise HTTPException(
            status_code=403,
            detail="Only a workspace owner can change access settings",
        )
    if data.name is not None:
        org.name = data.name
    org.workable_config = merge_workable_config(org, data)
    org.workspace_settings = merge_workspace_settings(org, data)
    org.scoring_policy = merge_scoring_policy(org, data)
    org.ai_tooling_config = merge_ai_tooling_config(org, data)
    org.notification_preferences = merge_notification_preferences(org, data)
    if data.fireflies_config is not None:
        fireflies_updates = data.fireflies_config.model_dump(exclude_unset=True)
        if "api_key" in fireflies_updates:
            api_key = (fireflies_updates.get("api_key") or "").strip()
            org.fireflies_api_key_encrypted = encrypt_text(api_key, settings.SECRET_KEY) if api_key else None
        if "webhook_secret" in fireflies_updates:
            webhook_secret = (fireflies_updates.get("webhook_secret") or "").strip()
            org.fireflies_webhook_secret = webhook_secret or None
        if "owner_email" in fireflies_updates:
            org.fireflies_owner_email = _normalized_optional_email(
                fireflies_updates.get("owner_email"),
                field_name="fireflies owner email",
            )
        if "invite_email" in fireflies_updates:
            org.fireflies_invite_email = _normalized_optional_email(
                fireflies_updates.get("invite_email"),
                field_name="fireflies invite email",
            )
        if "single_account_mode" in fireflies_updates and fireflies_updates.get("single_account_mode") is not None:
            org.fireflies_single_account_mode = bool(fireflies_updates.get("single_account_mode"))
    if data.allowed_email_domains is not None:
        org.allowed_email_domains = normalize_allowed_domains(data.allowed_email_domains)
    if data.sso_enforced is not None:
        org.sso_enforced = data.sso_enforced
    if data.saml_enabled is not None:
        org.saml_enabled = data.saml_enabled
    if data.saml_metadata_url is not None:
        metadata_url = (data.saml_metadata_url or "").strip()
        org.saml_metadata_url = metadata_url or None
    if data.two_factor_required is not None:
        org.two_factor_required = bool(data.two_factor_required)
    if data.default_assessment_duration_minutes is not None:
        org.default_assessment_duration_minutes = int(data.default_assessment_duration_minutes)
    if data.invite_email_template is not None:
        template = (data.invite_email_template or "").strip()
        org.invite_email_template = template or None
    if data.default_role_budget_cents is not None:
        org.default_role_budget_cents = max(0, int(data.default_role_budget_cents))
    # The org default reject threshold is the policy every role that hasn't
    # set its own override inherits. Snapshot it before mutating so we can
    # tell afterwards whether it actually moved and, if so, prompt the agent
    # to revisit those roles (below).
    _default_threshold_before = getattr(org, "default_score_threshold", None)
    _default_threshold_changed = False
    if data.default_score_threshold is not None:
        org.default_score_threshold = max(0, min(100, int(data.default_score_threshold)))
        _default_threshold_changed = (
            org.default_score_threshold != _default_threshold_before
        )
    if "monthly_spend_cap_cents" in data.model_fields_set:
        # Explicit null clears the cap (NULL = no cap); a number sets it.
        # Absent field leaves the existing cap untouched.
        org.monthly_spend_cap_cents = (
            None
            if data.monthly_spend_cap_cents is None
            else max(0, int(data.monthly_spend_cap_cents))
        )
    if org.saml_enabled and not org.saml_metadata_url:
        raise HTTPException(status_code=400, detail="saml_metadata_url is required when saml_enabled is true")
    try:
        db.commit()
        db.refresh(org)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update organization")
    # Changing the org default threshold changes the policy the agent enforces
    # on every role that inherits it (no per-role score_threshold override, in
    # manual mode). Re-apply the deterministic decisions for each such role
    # right away — retract advances now below the cutoff, then emit the matching
    # reject cards — instead of waiting up to 30 min for the next cohort tick.
    # Agent-off / paused / auto_reject roles are skipped by the helpers. This is
    # pure DB (no LLM); failures never fail the settings save — the beat sweep
    # reconciles regardless.
    if _default_threshold_changed:
        try:
            from ...models.role import Role
            from ...services.pre_screen_decision_emitter import (
                reconcile_pre_screen_reject_decisions,
                retract_advances_below_threshold,
            )

            new_threshold = float(org.default_score_threshold)
            impacted_roles = (
                db.query(Role)
                .filter(
                    Role.organization_id == int(org.id),
                    Role.deleted_at.is_(None),
                    Role.agentic_mode_enabled.is_(True),
                    Role.agent_paused_at.is_(None),
                    Role.score_threshold.is_(None),
                    (Role.auto_reject_threshold_mode == "manual")
                    | (Role.auto_reject_threshold_mode.is_(None)),
                )
                .all()
            )
            for impacted in impacted_roles:
                retract_advances_below_threshold(
                    db,
                    role=impacted,
                    organization_id=int(org.id),
                    threshold=new_threshold,
                )
                reconcile_pre_screen_reject_decisions(
                    db,
                    role=impacted,
                    organization_id=int(org.id),
                    threshold=new_threshold,
                )
            db.commit()
        except Exception:
            import logging as _logging
            _logging.getLogger("taali.organizations").exception(
                "Failed to re-apply org threshold policy for org_id=%s", org.id
            )
            db.rollback()
    if getattr(org, "default_assessment_duration_minutes", None) is None:
        org.default_assessment_duration_minutes = 30
    org.allowed_email_domains = normalize_allowed_domains(getattr(org, "allowed_email_domains", None))
    org.workspace_settings = resolved_workspace_settings(org)
    org.scoring_policy = resolved_scoring_policy(org)
    org.ai_tooling_config = resolved_ai_tooling_config(org)
    org.notification_preferences = resolved_notification_preferences(org)
    return org_response_payload(org)


@router.get("/workable/authorize-url")
def get_workable_authorize_url(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    scopes: str | None = Query(default=None, description="Comma or space separated scopes"),
):
    """Return the Workable OAuth authorize URL for the frontend to redirect to."""
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    if not _is_workable_oauth_configured():
        raise HTTPException(
            status_code=503,
            detail="Workable OAuth is not configured. Set WORKABLE_CLIENT_ID and WORKABLE_CLIENT_SECRET.",
        )
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    redirect_uri = f"{settings.FRONTEND_URL}/settings/workable/callback"
    default_scopes = _workable_oauth_scope(org).split()
    scope_tokens = _parsed_scope_tokens(scopes) if scopes is not None else default_scopes
    scope = " ".join(scope_tokens)
    url = (
        "https://www.workable.com/oauth/authorize"
        f"?client_id={settings.WORKABLE_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        "&resource=user"
        "&response_type=code"
        f"&scope={scope.replace(' ', '+')}"
    )
    return {
        "url": url,
        "scope": scope,
        "scope_tokens": scope_tokens,
        "redirect_uri": redirect_uri,
    }


@router.post("/workable/connect")
def connect_workable(
    data: WorkableConnect,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Exchange Workable OAuth code for access token."""
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")
    if not _is_workable_oauth_configured():
        raise HTTPException(
            status_code=503,
            detail="Workable OAuth is not configured. Set WORKABLE_CLIENT_ID and WORKABLE_CLIENT_SECRET.",
        )
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

    config = resolved_workable_config(org)
    scope_tokens = _scope_tokens_for_storage(
        token_data.get("scope"),
        fallback=_workable_oauth_scope(org).split(),
    )
    config["workflow_mode"] = "workable_hybrid"
    config["sync_model"] = "scheduled_pull_only"
    config["sync_scope"] = "open_jobs_active_candidates"
    config["granted_scopes"] = scope_tokens
    config["workable_writeback"] = "w_candidates" in scope_tokens

    org.workable_access_token = token_data.get("access_token")
    org.workable_refresh_token = token_data.get("refresh_token")
    org.workable_subdomain = token_data.get("subdomain", "")
    org.workable_connected = True
    org.workable_config = WorkableConfigBase(**config).model_dump()
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to store Workable connection")

    return {"success": True, "subdomain": org.workable_subdomain}


@router.post("/workable/connect-token")
def connect_workable_token(
    data: WorkableTokenConnect,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Connect Workable directly via access token + subdomain (read-only default)."""
    if settings.MVP_DISABLE_WORKABLE:
        raise HTTPException(status_code=503, detail="Workable integration is disabled for MVP")

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    subdomain = _normalized_workable_subdomain(data.subdomain)
    if not _SUBDOMAIN_RE.match(subdomain):
        raise HTTPException(status_code=400, detail="Invalid Workable subdomain")

    access_token = (data.access_token or "").strip()
    if len(access_token) < 20:
        raise HTTPException(status_code=400, detail="Invalid Workable access token")

    try:
        WorkableService(access_token=access_token, subdomain=subdomain).verify_access()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Unable to verify Workable token/subdomain. Check token scopes and subdomain.",
        )

    config = resolved_workable_config(org)
    config["workflow_mode"] = "workable_hybrid"
    config["sync_model"] = "scheduled_pull_only"
    config["sync_scope"] = "open_jobs_active_candidates"
    config["granted_scopes"] = ["r_jobs", "r_candidates"] + ([] if data.read_only else ["w_candidates"])
    config["workable_writeback"] = not data.read_only

    org.workable_access_token = access_token
    org.workable_refresh_token = None
    org.workable_subdomain = subdomain
    org.workable_connected = True
    org.workable_config = WorkableConfigBase(**config).model_dump()
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to store Workable connection")

    return {
        "success": True,
        "subdomain": subdomain,
        "mode": "api_token",
        "read_only": bool(data.read_only),
    }
