"""Pure helpers for shaping an Organization row into the OrgResponse payload.

Extracted from organization_routes.py so the route module stays under the
architectural file-size gate. Each helper resolves a JSON sub-config (with
its Pydantic defaults filled in) or merges an OrgUpdate patch into the
existing config.
"""
from __future__ import annotations

from ...models.organization import Organization
from ...platform.config import settings
from ...schemas.organization import (
    AgentDefaults,
    AiToolingConfig,
    FirefliesConfig,
    NotificationPreferences,
    OrgResponse,
    OrgUpdate,
    ScoringPolicy,
    WorkableConfigBase,
    WorkspaceSettings,
)
from .access_policy import SAML_SSO_AVAILABLE, TWO_FACTOR_AUTH_AVAILABLE


def default_workable_config() -> dict:
    return WorkableConfigBase().model_dump()


def resolved_workable_config(org: Organization) -> dict:
    raw = org.workable_config if isinstance(org.workable_config, dict) else {}
    resolved = WorkableConfigBase(**{**default_workable_config(), **raw}).model_dump()
    # Migration 150 populated this flag for existing workspaces. New/missing
    # state uses the schema's explicit false default so writes always fail closed.
    resolved["workable_writeback"] = bool(raw.get("workable_writeback", False))
    return resolved


def resolved_fireflies_config(org: Organization) -> FirefliesConfig:
    owner_email = (getattr(org, "fireflies_owner_email", None) or "").strip() or None
    invite_email = (getattr(org, "fireflies_invite_email", None) or "").strip() or None
    has_api_key = bool((getattr(org, "fireflies_api_key_encrypted", None) or "").strip())
    webhook_secret_configured = bool((getattr(org, "fireflies_webhook_secret", None) or "").strip())
    return FirefliesConfig(
        connected=bool(has_api_key and owner_email),
        has_api_key=has_api_key,
        webhook_secret_configured=webhook_secret_configured,
        owner_email=owner_email,
        invite_email=invite_email,
        single_account_mode=bool(getattr(org, "fireflies_single_account_mode", True)),
        webhook_url=(
            f"{settings.BACKEND_URL.rstrip('/')}/api/v1/webhooks/fireflies/{org.id}"
        ),
    )


def resolved_workspace_settings(org: Organization) -> dict:
    raw = org.workspace_settings if isinstance(org.workspace_settings, dict) else {}
    known = WorkspaceSettings(**{**WorkspaceSettings().model_dump(), **raw}).model_dump()
    # Preserve operator-only keys (e.g. decision_policy_auto_apply,
    # decision_policy_autoresearch) that aren't part of the user-facing settings
    # schema. The GET/PATCH handlers persist this dict back onto the org, so
    # round-tripping it through the strict schema alone would silently drop these
    # flags on the next settings read or save. The API response builder
    # (``org_response_payload``) re-applies the strict schema, so extras never
    # leak into the response.
    extras = {k: v for k, v in raw.items() if k not in known}
    return {**known, **extras}


def resolved_scoring_policy(org: Organization) -> dict:
    raw = org.scoring_policy if isinstance(org.scoring_policy, dict) else {}
    return ScoringPolicy(**{**ScoringPolicy().model_dump(), **raw}).model_dump()


def resolved_ai_tooling_config(org: Organization) -> dict:
    raw = org.ai_tooling_config if isinstance(org.ai_tooling_config, dict) else {}
    normalized = dict(raw)
    raw_defaults = normalized.get("agent_defaults")
    if isinstance(raw_defaults, dict):
        defaults = dict(raw_defaults)
        # Historical Settings clients stored 0 to mean "not configured".
        # A role now always needs a positive hard cap, so resolve that legacy
        # sentinel to the same $50 platform default used by Turn on.
        try:
            invalid_budget = int(defaults.get("budget_cents") or 0) <= 0
        except (TypeError, ValueError):
            invalid_budget = True
        if invalid_budget:
            defaults["budget_cents"] = 5_000
        normalized["agent_defaults"] = defaults
    return AiToolingConfig(
        **{**AiToolingConfig().model_dump(), **normalized}
    ).model_dump()


def resolved_notification_preferences(org: Organization) -> dict:
    raw = org.notification_preferences if isinstance(org.notification_preferences, dict) else {}
    return NotificationPreferences(**{**NotificationPreferences().model_dump(), **raw}).model_dump()


def merge_workable_config(org: Organization, incoming: OrgUpdate) -> dict:
    base = resolved_workable_config(org)
    if incoming.workable_config is None:
        return base
    updates = incoming.workable_config.model_dump(exclude_none=True)
    return WorkableConfigBase(**{**base, **updates}).model_dump()


def merge_workspace_settings(org: Organization, incoming: OrgUpdate) -> dict:
    base = resolved_workspace_settings(org)  # includes preserved operator-only keys
    if incoming.workspace_settings is None:
        return base
    updates = incoming.workspace_settings.model_dump(exclude_none=True)
    merged = {**base, **updates}
    known = WorkspaceSettings(**{**WorkspaceSettings().model_dump(), **merged}).model_dump()
    extras = {k: v for k, v in merged.items() if k not in known}
    return {**known, **extras}


def merge_scoring_policy(org: Organization, incoming: OrgUpdate) -> dict:
    base = resolved_scoring_policy(org)
    if incoming.scoring_policy is None:
        return base
    updates = incoming.scoring_policy.model_dump(exclude_none=True)
    return ScoringPolicy(**{**base, **updates}).model_dump()


def merge_ai_tooling_config(org: Organization, incoming: OrgUpdate) -> dict:
    base = resolved_ai_tooling_config(org)
    if incoming.ai_tooling_config is None:
        return base
    updates = incoming.ai_tooling_config.model_dump(exclude_none=True)
    incoming_defaults = updates.pop("agent_defaults", None)
    if incoming_defaults is not None:
        existing_defaults = base.get("agent_defaults")
        if not isinstance(existing_defaults, dict):
            existing_defaults = AgentDefaults().model_dump()
        updates["agent_defaults"] = AgentDefaults(
            **{**existing_defaults, **incoming_defaults}
        ).model_dump()
    return AiToolingConfig(**{**base, **updates}).model_dump()


def merge_notification_preferences(org: Organization, incoming: OrgUpdate) -> dict:
    base = resolved_notification_preferences(org)
    if incoming.notification_preferences is None:
        return base
    updates = incoming.notification_preferences.model_dump(exclude_none=True)
    return NotificationPreferences(**{**base, **updates}).model_dump()


def resolved_workable_mode(org: Organization) -> str:
    """Map the write-back flag + granted_scopes onto the UI-facing two-way /
    read-only label introduced in the settings redesign."""
    config = resolved_workable_config(org)
    granted = config.get("granted_scopes") or []
    if bool(config.get("workable_writeback")) and "w_candidates" in granted:
        return "two_way"
    return "read_only"


def resolve_active_ats(org: Organization) -> str:
    """Which ATS the org is actively on, mirroring ``resolve_ats_provider``.

    Kept in lock-step with ``components/integrations/resolver.py`` so the UI's
    "Active ATS" label can never disagree with what the resolver actually
    dispatches reads/writes to. Precedence: Workable wins; Bullhorn only when
    ``BULLHORN_ENABLED`` and the org is Bullhorn-connected; otherwise standalone.
    The resolver additionally requires a ``db`` session for the Bullhorn arm —
    that's a provider-construction need, not a connection condition, so it isn't
    mirrored here.
    """
    if org.workable_connected and org.workable_access_token and org.workable_subdomain:
        return "workable"
    if (
        settings.BULLHORN_ENABLED
        and getattr(org, "bullhorn_connected", False)
        and getattr(org, "bullhorn_client_id", None)
        and getattr(org, "bullhorn_refresh_token", None)
        and getattr(org, "bullhorn_username", None)
    ):
        return "bullhorn"
    return "standalone"


def org_response_payload(org: Organization) -> OrgResponse:
    response = OrgResponse.model_validate(org)
    response.workable_config = WorkableConfigBase(**resolved_workable_config(org))
    response.fireflies_config = resolved_fireflies_config(org)
    response.workspace_settings = WorkspaceSettings(**resolved_workspace_settings(org))
    response.scoring_policy = ScoringPolicy(**resolved_scoring_policy(org))
    response.ai_tooling_config = AiToolingConfig(**resolved_ai_tooling_config(org))
    response.notification_preferences = NotificationPreferences(**resolved_notification_preferences(org))
    response.workable_mode = resolved_workable_mode(org)
    # Platform-level Bullhorn gate — not a column; the FE reads it to decide
    # whether to show the Bullhorn settings section (off in every environment
    # until the integration is enabled).
    response.bullhorn_enabled = bool(settings.BULLHORN_ENABLED)
    # Per-org ATS posture + the resolver-derived active ATS, for the unified
    # Integrations settings surface.
    response.sync_mode = getattr(org, "sync_mode", None) or "standalone"
    response.active_ats = resolve_active_ats(org)
    response.has_billing_account = bool(getattr(org, "stripe_customer_id", None))
    response.sso_available = SAML_SSO_AVAILABLE
    response.two_factor_available = TWO_FACTOR_AUTH_AVAILABLE
    if not SAML_SSO_AVAILABLE:
        response.sso_enforced = False
        response.saml_enabled = False
        response.saml_metadata_url = None
    if not TWO_FACTOR_AUTH_AVAILABLE:
        response.two_factor_required = False
    return response
