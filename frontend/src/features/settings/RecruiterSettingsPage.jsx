import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CreditCard,
  KeyRound,
  Mail,
} from 'lucide-react';
import { useLocation, useNavigate } from 'react-router-dom';

import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { aedToUsd, formatAed } from '../../lib/currency';
import { organizations as orgsApi, billing as billingApi, team as teamApi } from '../../shared/api';
import {
  Button,
  Panel,
  Sheet,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';
import { CardSkeleton } from '../../shared/ui/Skeletons';
import {
  SyncPulse,
  WorkableLogo,
  formatRelativeDateTime,
} from '../../shared/ui/RecruiterDesignPrimitives';

const WORKABLE_SCOPE_OPTIONS = [
  { id: 'r_jobs', label: 'r_jobs', description: 'Read jobs and roles from Workable.' },
  { id: 'r_candidates', label: 'r_candidates', description: 'Read candidate profiles and stages.' },
  { id: 'w_candidates', label: 'w_candidates', description: 'Write candidate stage activity for invites, disqualify actions, and notes.' },
];

const WORKABLE_REQUIRED_SCOPES = ['r_jobs', 'r_candidates'];
const DEFAULT_INVITE_TEMPLATE = 'Hi {{candidate_name}}, your TAALI assessment is ready: {{assessment_link}}';
const DEFAULT_WORKSPACE_SETTINGS = {
  candidate_facing_brand: '',
  primary_domain: '',
  locale: 'English (US)',
};
const DEFAULT_SCORING_POLICY = {
  prompt_quality: true,
  error_recovery: true,
  independence: true,
  context_utilization: true,
  design_thinking: true,
  time_to_first_signal: false,
};
const DEFAULT_AI_TOOLING_CONFIG = {
  claude_enabled: true,
  cursor_inline_enabled: false,
  no_ai_baseline_enabled: true,
  claude_credit_per_candidate_usd: 12,
  session_timeout_minutes: 60,
};
const DEFAULT_NOTIFICATION_PREFERENCES = {
  candidate_updates: true,
  daily_digest: true,
  panel_reminders: true,
  sync_failures: true,
};
const DEFAULT_FIRELIES_FORM = {
  apiKey: '',
  webhookSecret: '',
  ownerEmail: '',
  inviteEmail: '',
  singleAccountMode: true,
};
const SECTION_ALIASES = {
  '': 'org',
  org: 'org',
  organization: 'org',
  workable: 'workable',
  billing: 'billing',
  team: 'members',
  members: 'members',
  roles: 'roles',
  enterprise: 'sso',
  sso: 'sso',
  scoring: 'scoring',
  ai: 'ai',
  preferences: 'api',
  api: 'api',
  notifications: 'notifications',
};
const buildWorkableScopeSelection = (scopes = []) => {
  const granted = new Set(
    (Array.isArray(scopes) ? scopes : [])
      .map((scope) => String(scope || '').trim())
      .filter(Boolean)
  );
  return {
    r_jobs: granted.has('r_jobs'),
    r_candidates: granted.has('r_candidates'),
    w_candidates: granted.has('w_candidates'),
  };
};

const normalizeWorkableError = (input) => {
  const raw = (input || '').toString();
  const lower = raw.toLowerCase();
  if (lower.includes('deploy') || lower.includes('migration') || lower.includes('endpoint not available') || lower.includes('railway')) {
    return 'This feature is temporarily unavailable. Please try again later or contact support.';
  }
  if (lower.includes('not configured')) {
    return 'Workable integration is not yet set up for this account. Please contact support to enable it.';
  }
  if (lower.includes('disabled for mvp')) {
    return 'Workable integration is not available on your current plan. Contact support to upgrade.';
  }
  if (lower.includes('oauth failed')) {
    return 'Workable OAuth failed. Verify callback URL and scopes in your Workable app, then try again.';
  }
  return raw || 'Workable connection failed.';
};

const workableMemberLabel = (member) => (
  member?.name
  || member?.full_name
  || member?.email
  || member?.id
  || 'Workable member'
);

const workableReasonLabel = (reason) => (
  reason?.name
  || reason?.title
  || reason?.label
  || reason?.id
  || 'Disqualification reason'
);

const workableStageLabel = (stage) => (
  stage?.name
  || stage?.title
  || stage?.slug
  || stage?.id
  || ''
);

const getErrorMessage = (error, fallback) => (
  error?.response?.data?.detail
  || error?.message
  || fallback
);

const initialsFor = (value) => {
  const letters = String(value || '')
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part[0])
    .join('');
  return (letters.slice(0, 2) || 'U').toUpperCase();
};

const canonicalSection = (raw) => SECTION_ALIASES[String(raw || '').trim().toLowerCase()] || 'org';

const SectionPanel = ({ id, title, subtitle, children, tone = '' }) => (
  <section id={id} className={`settings-panel ${tone}`.trim()}>
    <h2>
      {title}
      <em>.</em>
    </h2>
    <p className="sub">{subtitle}</p>
    {children}
  </section>
);

const ToggleCard = ({ title, description, checked, onChange, badge = null }) => (
  <div className="settings-toggle-card">
    <div>
      <h4>{title}</h4>
      <p>{description}</p>
    </div>
    <div className="settings-toggle-card-action">
      {badge}
      <button
        type="button"
        className={`sw ${checked ? 'on' : ''}`}
        aria-label={title}
        aria-pressed={checked}
        onClick={() => onChange(!checked)}
      />
    </div>
  </div>
);

const SettingsNavLink = ({ active, label, onClick }) => (
  <button
    type="button"
    className={`settings-side-link ${active ? 'active' : ''}`}
    onClick={onClick}
  >
    {label}
  </button>
);

const toAedWithUsdLabel = (rawValue, fallbackAmount = null, options = {}) => {
  const numeric = typeof rawValue === 'number'
    ? rawValue
    : typeof rawValue === 'string'
      ? Number(rawValue.replace(/[^\d.-]/g, ''))
      : fallbackAmount;
  const safe = Number.isFinite(Number(numeric)) ? Number(numeric) : 0;
  const usd = Number(aedToUsd(safe)).toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: options.maximumFractionDigits ?? 0,
  });
  return `${formatAed(safe, options)} (~$${usd} USD)`;
};

export const SettingsPage = ({ onNavigate, NavComponent = null, ConnectWorkableButton }) => {
  const { user } = useAuth();
  const { showToast } = useToast();
  const location = useLocation();
  const navigate = useNavigate();
  const sectionRefs = useRef({});
  const workableSyncPollRef = useRef(null);

  const pathSegment = location.pathname.replace(/^\/settings\/?/, '').split('/')[0];
  const activeSection = canonicalSection(pathSegment);

  const [orgData, setOrgData] = useState(null);
  const [orgLoading, setOrgLoading] = useState(true);
  const [workspaceForm, setWorkspaceForm] = useState(DEFAULT_WORKSPACE_SETTINGS);
  const [workspaceSaving, setWorkspaceSaving] = useState(false);
  const [scoringPolicyForm, setScoringPolicyForm] = useState(DEFAULT_SCORING_POLICY);
  const [scoringSaving, setScoringSaving] = useState(false);
  const [aiToolingForm, setAiToolingForm] = useState({
    ...DEFAULT_AI_TOOLING_CONFIG,
    defaultAssessmentMinutes: 30,
  });
  const [aiSaving, setAiSaving] = useState(false);
  const [notificationPreferencesForm, setNotificationPreferencesForm] = useState(DEFAULT_NOTIFICATION_PREFERENCES);
  const [notificationsSaving, setNotificationsSaving] = useState(false);
  const [accessForm, setAccessForm] = useState({
    allowedEmailDomains: '',
    candidateFeedbackEnabled: true,
  });
  const [accessSaving, setAccessSaving] = useState(false);
  const [ssoForm, setSsoForm] = useState({
    ssoEnforced: false,
    samlEnabled: false,
    samlMetadataUrl: '',
  });
  const [ssoSaving, setSsoSaving] = useState(false);
  const [teamMembers, setTeamMembers] = useState([]);
  const [inviteName, setInviteName] = useState('');
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteLoading, setInviteLoading] = useState(false);
  const [billingUsage, setBillingUsage] = useState(null);
  const [billingCosts, setBillingCosts] = useState(null);
  const [billingCredits, setBillingCredits] = useState(null);
  const [billingLoading, setBillingLoading] = useState(false);
  const [checkoutLoading, setCheckoutLoading] = useState(false);
  const [emailTemplatePreview, setEmailTemplatePreview] = useState(DEFAULT_INVITE_TEMPLATE);
  const [apiSaving, setApiSaving] = useState(false);
  const [defaultAdditionalRequirements, setDefaultAdditionalRequirements] = useState('');
  const [defaultRequirementsSaving, setDefaultRequirementsSaving] = useState(false);
  const [firefliesForm, setFirefliesForm] = useState(DEFAULT_FIRELIES_FORM);
  const [firefliesSaving, setFirefliesSaving] = useState(false);
  const [firefliesHasApiKey, setFirefliesHasApiKey] = useState(false);
  const [firefliesWebhookSecretConfigured, setFirefliesWebhookSecretConfigured] = useState(false);
  const [firefliesClearApiKey, setFirefliesClearApiKey] = useState(false);
  const [firefliesClearWebhookSecret, setFirefliesClearWebhookSecret] = useState(false);

  const [workableSaving, setWorkableSaving] = useState(false);
  const [workableSyncLoading, setWorkableSyncLoading] = useState(false);
  const [workableSyncInProgress, setWorkableSyncInProgress] = useState(false);
  const [workableActiveRunId, setWorkableActiveRunId] = useState(null);
  const [workableSyncCancelLoading, setWorkableSyncCancelLoading] = useState(false);
  const [workableJobsLoading, setWorkableJobsLoading] = useState(false);
  const [workableJobsError, setWorkableJobsError] = useState('');
  const [workableJobs, setWorkableJobs] = useState([]);
  const [workableJobSearch, setWorkableJobSearch] = useState('');
  const [workableSelectedJobShortcodes, setWorkableSelectedJobShortcodes] = useState([]);
  const [workableMembersLoading, setWorkableMembersLoading] = useState(false);
  const [workableMembers, setWorkableMembers] = useState([]);
  const [workableReasonsLoading, setWorkableReasonsLoading] = useState(false);
  const [workableReasons, setWorkableReasons] = useState([]);
  const [workableStagesLoading, setWorkableStagesLoading] = useState(false);
  const [workableStages, setWorkableStages] = useState([]);
  const [workableDrawerOpen, setWorkableDrawerOpen] = useState(false);
  const [workableConnectMode, setWorkableConnectMode] = useState('oauth');
  const [workableOAuthLoading, setWorkableOAuthLoading] = useState(false);
  const [workableTokenSaving, setWorkableTokenSaving] = useState(false);
  const [workableConnectError, setWorkableConnectError] = useState('');
  const [workableSelectedScopes, setWorkableSelectedScopes] = useState({
    r_jobs: true,
    r_candidates: true,
    w_candidates: false,
  });
  const [workableTokenForm, setWorkableTokenForm] = useState({
    subdomain: '',
    accessToken: '',
  });
  const [workableForm, setWorkableForm] = useState({
    emailMode: 'manual_taali',
    defaultSyncMode: 'full',
    syncIntervalMinutes: 30,
    inviteStageName: '',
    autoRejectEnabled: false,
    autoRejectThreshold100: '',
    workableActorMemberId: '',
    workableDisqualifyReasonId: '',
    autoRejectNoteTemplate: '',
  });
  const [clearWorkableModalOpen, setClearWorkableModalOpen] = useState(false);
  const [clearWorkableLoading, setClearWorkableLoading] = useState(false);

  const selectedWorkableScopes = WORKABLE_SCOPE_OPTIONS
    .filter((scope) => workableSelectedScopes[scope.id])
    .map((scope) => scope.id);
  const missingRequiredWorkableScopes = WORKABLE_REQUIRED_SCOPES.filter((scope) => !selectedWorkableScopes.includes(scope));

  const loadOrg = useCallback(async () => {
    setOrgLoading(true);
    try {
      const res = await orgsApi.get();
      setOrgData(res?.data || null);
    } catch (error) {
      setOrgData(null);
      showToast(getErrorMessage(error, 'Failed to load workspace settings.'), 'error');
    } finally {
      setOrgLoading(false);
    }
  }, [showToast]);

  const loadBilling = useCallback(async () => {
    setBillingLoading(true);
    try {
      const [usageRes, costsRes, creditsRes] = await Promise.all([billingApi.usage(), billingApi.costs(), billingApi.credits()]);
      setBillingUsage(usageRes?.data || null);
      setBillingCosts(costsRes?.data || null);
      setBillingCredits(creditsRes?.data || null);
    } catch {
      setBillingUsage(null);
      setBillingCosts(null);
      setBillingCredits(null);
    } finally {
      setBillingLoading(false);
    }
  }, []);

  const loadTeam = useCallback(async () => {
    try {
      const res = await teamApi.list();
      setTeamMembers(Array.isArray(res?.data) ? res.data : []);
    } catch {
      setTeamMembers([]);
    }
  }, []);

  const fetchWorkableSyncStatus = useCallback(async (runIdOverride = null) => {
    try {
      const runId = runIdOverride != null ? runIdOverride : workableActiveRunId;
      const res = await orgsApi.getWorkableSyncStatus(runId);
      const data = res?.data || {};
      setWorkableActiveRunId(data.run_id ?? null);
      setWorkableSyncInProgress(Boolean(data.sync_in_progress));
      setOrgData((prev) => ({
        ...(prev || {}),
        active_claude_model: data.active_claude_model ?? prev?.active_claude_model,
        active_claude_scoring_model: data.active_claude_scoring_model ?? prev?.active_claude_scoring_model,
        workable_last_sync_at: data.workable_last_sync_at ?? prev?.workable_last_sync_at,
        workable_last_sync_status: data.workable_last_sync_status ?? prev?.workable_last_sync_status,
        workable_last_sync_summary: data.workable_last_sync_summary ?? prev?.workable_last_sync_summary,
        workable_sync_progress: data.workable_sync_progress ?? prev?.workable_sync_progress,
      }));
      return data;
    } catch {
      return {};
    }
  }, [workableActiveRunId]);

  const loadWorkableSyncJobs = useCallback(async () => {
    if (!orgData?.workable_connected) {
      setWorkableJobs([]);
      setWorkableSelectedJobShortcodes([]);
      setWorkableJobsError('');
      return;
    }
    setWorkableJobsLoading(true);
    setWorkableJobsError('');
    try {
      const res = await orgsApi.getWorkableSyncJobs();
      const jobs = Array.isArray(res?.data?.jobs) ? res.data.jobs : [];
      setWorkableJobs(jobs);
      const identifiers = jobs
        .map((job) => String(job?.shortcode || job?.id || '').trim())
        .filter(Boolean);
      setWorkableSelectedJobShortcodes((prev) => {
        const kept = prev.filter((item) => identifiers.includes(item));
        return kept.length > 0 ? kept : identifiers;
      });
    } catch (error) {
      setWorkableJobsError(getErrorMessage(error, 'Failed to load Workable roles.'));
    } finally {
      setWorkableJobsLoading(false);
    }
  }, [orgData?.workable_connected]);

  const loadWorkableLookups = useCallback(async () => {
    if (!orgData?.workable_connected) {
      setWorkableMembers([]);
      setWorkableReasons([]);
      setWorkableStages([]);
      return;
    }
    setWorkableMembersLoading(true);
    setWorkableReasonsLoading(true);
    setWorkableStagesLoading(true);
    try {
      const [membersRes, reasonsRes, stagesRes] = await Promise.all([
        orgsApi.getWorkableMembers(),
        orgsApi.getWorkableDisqualificationReasons(),
        orgsApi.getWorkableStages(),
      ]);
      setWorkableMembers(Array.isArray(membersRes?.data?.members) ? membersRes.data.members : []);
      setWorkableReasons(Array.isArray(reasonsRes?.data?.disqualification_reasons) ? reasonsRes.data.disqualification_reasons : []);
      setWorkableStages(Array.isArray(stagesRes?.data?.stages) ? stagesRes.data.stages : []);
    } catch {
      setWorkableMembers([]);
      setWorkableReasons([]);
      setWorkableStages([]);
    } finally {
      setWorkableMembersLoading(false);
      setWorkableReasonsLoading(false);
      setWorkableStagesLoading(false);
    }
  }, [orgData?.workable_connected]);

  useEffect(() => {
    void loadOrg();
  }, [loadOrg]);

  useEffect(() => {
    if (activeSection === 'billing') {
      void loadBilling();
    }
    if (activeSection === 'members' || activeSection === 'roles') {
      void loadTeam();
    }
    if (activeSection === 'workable') {
      void fetchWorkableSyncStatus();
      void loadWorkableSyncJobs();
      void loadWorkableLookups();
    }
  }, [activeSection, fetchWorkableSyncStatus, loadBilling, loadTeam, loadWorkableLookups, loadWorkableSyncJobs]);

  useEffect(() => {
    if (!orgData) return;
    const workspaceSettings = {
      ...DEFAULT_WORKSPACE_SETTINGS,
      ...(orgData.workspace_settings || {}),
    };
    const inferredPrimaryDomain = workspaceSettings.primary_domain
      || orgData.allowed_email_domains?.[0]
      || (user?.email || '').split('@')[1]
      || '';
    setWorkspaceForm({
      candidate_facing_brand: workspaceSettings.candidate_facing_brand || '',
      primary_domain: inferredPrimaryDomain,
      locale: workspaceSettings.locale || DEFAULT_WORKSPACE_SETTINGS.locale,
    });
    setScoringPolicyForm({
      ...DEFAULT_SCORING_POLICY,
      ...(orgData.scoring_policy || {}),
    });
    const nextAiTooling = {
      ...DEFAULT_AI_TOOLING_CONFIG,
      ...(orgData.ai_tooling_config || {}),
      defaultAssessmentMinutes: Number(orgData.default_assessment_duration_minutes || 30),
    };
    setAiToolingForm(nextAiTooling);
    setNotificationPreferencesForm({
      ...DEFAULT_NOTIFICATION_PREFERENCES,
      ...(orgData.notification_preferences || {}),
    });
    setAccessForm({
      allowedEmailDomains: Array.isArray(orgData.allowed_email_domains) ? orgData.allowed_email_domains.join(', ') : '',
      candidateFeedbackEnabled: orgData.candidate_feedback_enabled !== false,
    });
    setSsoForm({
      ssoEnforced: Boolean(orgData.sso_enforced),
      samlEnabled: Boolean(orgData.saml_enabled),
      samlMetadataUrl: orgData.saml_metadata_url || '',
    });
    setEmailTemplatePreview(
      String(orgData.invite_email_template || '').trim() || DEFAULT_INVITE_TEMPLATE
    );
    setDefaultAdditionalRequirements(String(orgData.default_additional_requirements || ''));
    const firefliesConfig = orgData.fireflies_config || {};
    setFirefliesForm({
      apiKey: '',
      webhookSecret: '',
      ownerEmail: firefliesConfig.owner_email || '',
      inviteEmail: firefliesConfig.invite_email || '',
      singleAccountMode: firefliesConfig.single_account_mode !== false,
    });
    setFirefliesHasApiKey(Boolean(firefliesConfig.has_api_key));
    setFirefliesWebhookSecretConfigured(Boolean(firefliesConfig.webhook_secret_configured));
    setFirefliesClearApiKey(false);
    setFirefliesClearWebhookSecret(false);
    const workableConfig = orgData.workable_config || {};
    const grantedScopes = Array.isArray(workableConfig.granted_scopes) ? workableConfig.granted_scopes : [];
    setWorkableForm({
      emailMode: workableConfig.email_mode || 'manual_taali',
      defaultSyncMode: workableConfig.default_sync_mode || 'full',
      syncIntervalMinutes: Number(workableConfig.sync_interval_minutes || 30),
      inviteStageName: workableConfig.invite_stage_name || '',
      autoRejectEnabled: Boolean(workableConfig.auto_reject_enabled),
      autoRejectThreshold100: workableConfig.auto_reject_threshold_100 ?? '',
      workableActorMemberId: workableConfig.workable_actor_member_id || '',
      workableDisqualifyReasonId: workableConfig.workable_disqualify_reason_id || '',
      autoRejectNoteTemplate: workableConfig.auto_reject_note_template || '',
    });
    setWorkableSelectedScopes(
      grantedScopes.length > 0
        ? buildWorkableScopeSelection(grantedScopes)
        : {
          r_jobs: true,
          r_candidates: true,
          w_candidates: Boolean(workableConfig.auto_reject_enabled) || workableConfig.email_mode === 'workable_preferred_fallback_manual',
        }
    );
    setWorkableTokenForm((prev) => ({
      ...prev,
      subdomain: prev.subdomain || orgData.workable_subdomain || '',
    }));
  }, [orgData, user?.email]);

  useEffect(() => {
    if (!workableSyncInProgress) {
      if (workableSyncPollRef.current) {
        clearTimeout(workableSyncPollRef.current.firstDelay);
        clearInterval(workableSyncPollRef.current.interval);
        workableSyncPollRef.current = null;
      }
      return;
    }
    const poll = async () => {
      const data = await fetchWorkableSyncStatus(workableActiveRunId);
      if (!data.sync_in_progress) {
        setWorkableActiveRunId(null);
      }
    };
    const firstDelay = setTimeout(poll, 1500);
    const interval = setInterval(poll, 2500);
    workableSyncPollRef.current = { firstDelay, interval };
    return () => {
      if (workableSyncPollRef.current) {
        clearTimeout(workableSyncPollRef.current.firstDelay);
        clearInterval(workableSyncPollRef.current.interval);
        workableSyncPollRef.current = null;
      }
    };
  }, [fetchWorkableSyncStatus, workableActiveRunId, workableSyncInProgress]);

  useEffect(() => {
    if (orgLoading) return;
    const targetSection = canonicalSection(pathSegment);
    const target = sectionRefs.current[targetSection];
    if (!target) return;
    const timer = window.setTimeout(() => {
      if (typeof target.scrollIntoView === 'function') {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [orgLoading, pathSegment]);

  const handleSaveWorkspace = async () => {
    setWorkspaceSaving(true);
    try {
      const res = await orgsApi.update({
        name: String(orgData?.name || '').trim() || user?.organization?.name || 'Workspace',
        workspace_settings: {
          candidate_facing_brand: String(workspaceForm.candidate_facing_brand || '').trim() || null,
          primary_domain: String(workspaceForm.primary_domain || '').trim() || null,
          locale: String(workspaceForm.locale || DEFAULT_WORKSPACE_SETTINGS.locale).trim(),
        },
      });
      setOrgData(res?.data || null);
      showToast('Organization settings saved.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save organization settings.'), 'error');
    } finally {
      setWorkspaceSaving(false);
    }
  };

  const handleSaveScoringPolicy = async () => {
    setScoringSaving(true);
    try {
      const res = await orgsApi.update({ scoring_policy: scoringPolicyForm });
      setOrgData(res?.data || null);
      showToast('Scoring policy saved.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save scoring policy.'), 'error');
    } finally {
      setScoringSaving(false);
    }
  };

  const handleSaveAiTooling = async () => {
    setAiSaving(true);
    try {
      const res = await orgsApi.update({
        ai_tooling_config: {
          claude_enabled: Boolean(aiToolingForm.claude_enabled),
          cursor_inline_enabled: Boolean(aiToolingForm.cursor_inline_enabled),
          no_ai_baseline_enabled: Boolean(aiToolingForm.no_ai_baseline_enabled),
          claude_credit_per_candidate_usd: Number(aiToolingForm.claude_credit_per_candidate_usd || 0),
          session_timeout_minutes: Number(aiToolingForm.session_timeout_minutes || 60),
        },
        default_assessment_duration_minutes: Math.max(15, Math.min(180, Number(aiToolingForm.defaultAssessmentMinutes || 30))),
      });
      setOrgData(res?.data || null);
      showToast('AI tooling settings saved.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save AI tooling settings.'), 'error');
    } finally {
      setAiSaving(false);
    }
  };

  const handleSaveAccess = async () => {
    setAccessSaving(true);
    const domains = String(accessForm.allowedEmailDomains || '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);
    try {
      const res = await orgsApi.update({
        allowed_email_domains: domains,
        candidate_feedback_enabled: Boolean(accessForm.candidateFeedbackEnabled),
      });
      setOrgData(res?.data || null);
      showToast('Roles and access settings saved.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save roles and access settings.'), 'error');
    } finally {
      setAccessSaving(false);
    }
  };

  const handleSaveSso = async () => {
    setSsoSaving(true);
    try {
      const res = await orgsApi.update({
        sso_enforced: Boolean(ssoForm.ssoEnforced),
        saml_enabled: Boolean(ssoForm.samlEnabled),
        saml_metadata_url: String(ssoForm.samlMetadataUrl || '').trim() || null,
      });
      setOrgData(res?.data || null);
      showToast('SSO / SAML settings saved.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save SSO / SAML settings.'), 'error');
    } finally {
      setSsoSaving(false);
    }
  };

  const handleSaveNotifications = async () => {
    setNotificationsSaving(true);
    try {
      const res = await orgsApi.update({
        notification_preferences: notificationPreferencesForm,
      });
      setOrgData(res?.data || null);
      showToast('Notification preferences saved.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save notification preferences.'), 'error');
    } finally {
      setNotificationsSaving(false);
    }
  };

  const handleSaveApiKeys = async () => {
    setApiSaving(true);
    const payload = {
      invite_email_template: String(emailTemplatePreview || '').trim() || null,
    };
    try {
      const res = await orgsApi.update(payload);
      setOrgData((prev) => ({ ...(prev || {}), ...(res?.data || {}) }));
      showToast('API key settings saved.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save API key settings.'), 'error');
    } finally {
      setApiSaving(false);
    }
  };

  const handleSaveDefaultRequirements = async () => {
    setDefaultRequirementsSaving(true);
    const payload = {
      default_additional_requirements:
        String(defaultAdditionalRequirements || '').trim() || null,
    };
    try {
      const res = await orgsApi.update(payload);
      setOrgData((prev) => ({ ...(prev || {}), ...(res?.data || {}) }));
      showToast('Default scoring criteria saved.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save default scoring criteria.'), 'error');
    } finally {
      setDefaultRequirementsSaving(false);
    }
  };

  const handleSaveFireflies = async () => {
    setFirefliesSaving(true);
    try {
      const apiKey = String(firefliesForm.apiKey || '').trim();
      const webhookSecret = String(firefliesForm.webhookSecret || '').trim();
      const firefliesPayload = {
        owner_email: String(firefliesForm.ownerEmail || '').trim() || null,
        invite_email: String(firefliesForm.inviteEmail || '').trim() || null,
        single_account_mode: Boolean(firefliesForm.singleAccountMode),
      };
      if (firefliesClearApiKey) {
        firefliesPayload.api_key = '';
      } else if (apiKey) {
        firefliesPayload.api_key = apiKey;
      }
      if (firefliesClearWebhookSecret) {
        firefliesPayload.webhook_secret = '';
      } else if (webhookSecret) {
        firefliesPayload.webhook_secret = webhookSecret;
      }
      const res = await orgsApi.update({ fireflies_config: firefliesPayload });
      setOrgData(res?.data || null);
      setFirefliesForm((prev) => ({ ...prev, apiKey: '', webhookSecret: '' }));
      setFirefliesClearApiKey(false);
      setFirefliesClearWebhookSecret(false);
      showToast('Fireflies settings saved.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save Fireflies settings.'), 'error');
    } finally {
      setFirefliesSaving(false);
    }
  };

  const handleInvite = async (event) => {
    event.preventDefault();
    if (!inviteName.trim() || !inviteEmail.trim()) return;
    setInviteLoading(true);
    try {
      const res = await teamApi.invite({
        email: inviteEmail.trim(),
        full_name: inviteName.trim(),
      });
      setTeamMembers((prev) => [...prev, res?.data].filter(Boolean));
      setInviteName('');
      setInviteEmail('');
      showToast('Invite sent.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to invite team member.'), 'error');
    } finally {
      setInviteLoading(false);
    }
  };

  const handleAddCredits = async (packId) => {
    const base = `${window.location.origin}/settings/billing`;
    setCheckoutLoading(true);
    try {
      const res = await billingApi.createCheckoutSession({
        success_url: `${base}?payment=success`,
        cancel_url: base,
        pack_id: packId,
      });
      if (res?.data?.url) {
        window.location.href = res.data.url;
        return;
      }
    } catch (error) {
      showToast(getErrorMessage(error, 'Checkout could not be started.'), 'error');
    } finally {
      setCheckoutLoading(false);
    }
  };

  const handleClearWorkableData = async () => {
    setClearWorkableLoading(true);
    try {
      const res = await orgsApi.clearWorkableData();
      const data = res?.data || {};
      showToast(
        `Removed ${data.roles_soft_deleted ?? 0} roles, ${data.applications_soft_deleted ?? 0} applications, ${data.candidates_soft_deleted ?? 0} candidates.`,
        'success'
      );
      setClearWorkableModalOpen(false);
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to clear Workable data.'), 'error');
    } finally {
      setClearWorkableLoading(false);
    }
  };

  const toggleWorkableScope = (scopeId) => {
    setWorkableSelectedScopes((prev) => ({
      ...prev,
      [scopeId]: !prev[scopeId],
    }));
  };

  const handleConnectWorkableOAuth = async () => {
    if (missingRequiredWorkableScopes.length > 0) {
      setWorkableConnectError('OAuth requires at least r_jobs and r_candidates scopes.');
      return;
    }
    setWorkableOAuthLoading(true);
    setWorkableConnectError('');
    try {
      const hasWriteScope = selectedWorkableScopes.includes('w_candidates');
      await orgsApi.update({
        workable_config: {
          email_mode: hasWriteScope ? 'workable_preferred_fallback_manual' : 'manual_taali',
          default_sync_mode: 'full',
        },
      });
      const res = await orgsApi.getWorkableAuthorizeUrl({ scopes: selectedWorkableScopes });
      if (res?.data?.url) {
        window.location.href = res.data.url;
        return;
      }
      setWorkableConnectError('Could not get Workable authorization URL.');
    } catch (error) {
      setWorkableConnectError(normalizeWorkableError(getErrorMessage(error, 'Workable authorization failed.')));
    } finally {
      setWorkableOAuthLoading(false);
    }
  };

  const handleConnectWorkableToken = async (event) => {
    event.preventDefault();
    const subdomain = workableTokenForm.subdomain.trim();
    const accessToken = workableTokenForm.accessToken.trim();
    if (!subdomain || !accessToken) {
      setWorkableConnectError('Enter Workable subdomain and API access token.');
      return;
    }
    if (missingRequiredWorkableScopes.length > 0) {
      setWorkableConnectError('Token connect requires at least r_jobs and r_candidates scopes.');
      return;
    }
    const readOnly = !selectedWorkableScopes.includes('w_candidates');
    setWorkableTokenSaving(true);
    setWorkableConnectError('');
    try {
      const res = await orgsApi.connectWorkableToken({
        subdomain,
        access_token: accessToken,
        read_only: readOnly,
      });
      setOrgData((prev) => ({
        ...(prev || {}),
        workable_connected: true,
        workable_subdomain: res?.data?.subdomain || subdomain,
        workable_config: {
          ...((prev && prev.workable_config) || {}),
          workflow_mode: 'workable_hybrid',
          email_mode: readOnly ? 'manual_taali' : 'workable_preferred_fallback_manual',
          sync_model: 'scheduled_pull_only',
          sync_scope: 'open_jobs_active_candidates',
          default_sync_mode: 'full',
          granted_scopes: selectedWorkableScopes,
        },
      }));
      setWorkableTokenForm((prev) => ({ ...prev, accessToken: '' }));
      setWorkableDrawerOpen(false);
      showToast(readOnly ? 'Workable connected in read-only mode.' : 'Workable connected with candidate write-back.', 'success');
    } catch (error) {
      setWorkableConnectError(normalizeWorkableError(getErrorMessage(error, 'Workable token connection failed.')));
    } finally {
      setWorkableTokenSaving(false);
    }
  };

  const handleSaveWorkable = async () => {
    const emailMode = workableForm.emailMode || 'manual_taali';
    const defaultSyncMode = workableForm.defaultSyncMode || 'full';
    const inviteStageName = String(workableForm.inviteStageName || '').trim();
    const autoRejectEnabled = Boolean(workableForm.autoRejectEnabled);
    const hasWriteScope = selectedWorkableScopes.includes('w_candidates');
    const workableActorMemberId = String(workableForm.workableActorMemberId || '').trim();
    const workableDisqualifyReasonId = String(workableForm.workableDisqualifyReasonId || '').trim();
    const autoRejectNoteTemplate = String(workableForm.autoRejectNoteTemplate || '').trim();
    const parsedThreshold = workableForm.autoRejectThreshold100 === '' ? null : Number(workableForm.autoRejectThreshold100);

    if ((emailMode === 'workable_preferred_fallback_manual' || autoRejectEnabled) && !hasWriteScope) {
      showToast('Reconnect Workable with `w_candidates` scope to enable Workable invite, reject, and reopen actions.', 'error');
      return;
    }
    if (emailMode === 'workable_preferred_fallback_manual' && !inviteStageName) {
      showToast('Enter the exact Workable stage name for automated invite mode.', 'error');
      return;
    }
    if (autoRejectEnabled && !Number.isFinite(parsedThreshold)) {
      showToast('Set an auto-reject threshold between 0 and 100.', 'error');
      return;
    }
    if (hasWriteScope && !workableActorMemberId) {
      showToast('Choose the Workable member account that should perform Workable invite, reject, and reopen actions.', 'error');
      return;
    }

    setWorkableSaving(true);
    try {
      const res = await orgsApi.update({
        workable_config: {
          email_mode: emailMode,
          sync_model: 'scheduled_pull_only',
          sync_scope: 'open_jobs_active_candidates',
          score_precedence: 'workable_first',
          default_sync_mode: defaultSyncMode,
          sync_interval_minutes: Number(workableForm.syncIntervalMinutes || 30),
          invite_stage_name: emailMode === 'workable_preferred_fallback_manual' ? inviteStageName : '',
          auto_reject_enabled: autoRejectEnabled,
          auto_reject_threshold_100: autoRejectEnabled && Number.isFinite(parsedThreshold)
            ? Math.max(0, Math.min(100, Math.round(parsedThreshold)))
            : null,
          workable_actor_member_id: workableActorMemberId || null,
          workable_disqualify_reason_id: workableDisqualifyReasonId || null,
          auto_reject_note_template: autoRejectNoteTemplate || null,
        },
      });
      setOrgData(res?.data || null);
      showToast('Workable sync settings saved.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save Workable settings.'), 'error');
    } finally {
      setWorkableSaving(false);
    }
  };

  const handleSyncWorkable = async () => {
    setWorkableSyncLoading(true);
    try {
      const availableIdentifiers = workableJobs
        .map((job) => String(job?.shortcode || job?.id || '').trim())
        .filter(Boolean);
      const selectedIdentifiers = workableSelectedJobShortcodes.filter((id) => availableIdentifiers.includes(id));
      if (availableIdentifiers.length > 0 && selectedIdentifiers.length === 0) {
        showToast('Select at least one Workable role to sync.', 'info');
        return;
      }
      const syncMode = workableForm.defaultSyncMode || 'full';
      const res = await orgsApi.syncWorkable({
        mode: syncMode,
        job_shortcodes: selectedIdentifiers,
      });
      const payload = res?.data || {};
      const runId = payload?.run_id ?? null;
      setWorkableActiveRunId(runId);
      setWorkableSyncInProgress(true);
      if (payload?.status === 'already_running') {
        showToast("A sync is already running in the background. We'll reattach to it here.", 'info');
        void fetchWorkableSyncStatus(runId);
        return;
      }
      showToast(`${syncMode === 'metadata' ? 'Metadata sync' : 'Full sync'} started.`, 'info');
      void fetchWorkableSyncStatus(runId);
    } catch (error) {
      const status = error?.response?.status;
      if (status === 409) {
        showToast("A sync is already running in the background. We'll notify you when it's done.", 'info');
        void fetchWorkableSyncStatus();
      } else {
        setWorkableSyncInProgress(false);
        showToast(getErrorMessage(error, 'Workable sync failed.'), 'error');
      }
    } finally {
      setWorkableSyncLoading(false);
    }
  };

  const handleCancelWorkableSync = async () => {
    setWorkableSyncCancelLoading(true);
    try {
      await orgsApi.cancelWorkableSync(workableActiveRunId);
      showToast('Cancel requested. Sync will stop shortly.', 'info');
      void fetchWorkableSyncStatus(workableActiveRunId);
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to cancel sync.'), 'error');
    } finally {
      setWorkableSyncCancelLoading(false);
    }
  };

  const filteredWorkableSyncJobs = useMemo(() => {
    const search = String(workableJobSearch || '').trim().toLowerCase();
    if (!search) return workableJobs;
    return workableJobs.filter((job) => {
      const identifier = String(job?.shortcode || job?.id || '').toLowerCase();
      const title = String(job?.title || '').toLowerCase();
      return identifier.includes(search) || title.includes(search);
    });
  }, [workableJobSearch, workableJobs]);

  const selectedRoleSetForSync = useMemo(() => new Set(workableSelectedJobShortcodes), [workableSelectedJobShortcodes]);
  const workableConnected = Boolean(orgData?.workable_connected);
  const workableConfig = orgData?.workable_config || {};
  const workableHealth = orgData?.workable_last_sync_status === 'failed'
    ? 'error'
    : orgData?.workable_last_sync_at
      ? 'healthy'
      : 'stale';
  const nextWorkablePull = orgData?.workable_last_sync_at && workableConfig.sync_interval_minutes
    ? new Date(new Date(orgData.workable_last_sync_at).getTime() + Number(workableConfig.sync_interval_minutes || 30) * 60000)
    : null;
  const lastSyncSummary = orgData?.workable_last_sync_summary || {};
  const creditsBalance = Number(billingCredits?.credits_balance ?? orgData?.credits_balance ?? 0);
  const packCatalog = billingCredits?.packs || {
    starter_5: { label: 'Starter (5 credits)', credits: 5 },
    growth_10: { label: 'Growth (10 credits)', credits: 10 },
    scale_20: { label: 'Scale (20 credits)', credits: 20 },
  };
  const usageHistory = billingUsage?.usage || [];
  const monthlyCost = Number(billingUsage?.total_cost || 0);
  const spendSummary = billingCosts?.summary || {};
  const thresholdConfig = billingCosts?.thresholds || {};
  const thresholdStatus = billingCosts?.threshold_status || {};

  const navigateToSection = (sectionId) => {
    const next = canonicalSection(sectionId);
    navigate(next === 'org' ? '/settings' : `/settings/${next}`);
  };

  const renderLoadingState = (
    <div className="settings-loading">
      <CardSkeleton lines={4} />
      <CardSkeleton lines={6} />
      <CardSkeleton lines={5} />
    </div>
  );

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="settings" onNavigate={onNavigate} /> : null}
      <div className="page">
        <div className="page-head">
          <div className="tally-bg" />
          <div>
            <div className="kicker">04 · RECRUITER WORKSPACE</div>
            <h1>
              Settings<em>.</em>
            </h1>
            <p className="sub">Workspace, scoring policy, integrations, and access.</p>
          </div>
        </div>

        {orgLoading ? renderLoadingState : (
          <div className="settings-layout">
            <aside className="settings-side">
              <div className="settings-side-group-label">Workspace</div>
              <SettingsNavLink active={activeSection === 'org'} label="Organization" onClick={() => navigateToSection('org')} />
              <SettingsNavLink active={activeSection === 'scoring'} label="Scoring policy" onClick={() => navigateToSection('scoring')} />
              <SettingsNavLink active={activeSection === 'ai'} label="AI tooling" onClick={() => navigateToSection('ai')} />

              <div className="settings-side-group-label">People</div>
              <SettingsNavLink active={activeSection === 'members'} label="Members" onClick={() => navigateToSection('members')} />
              <SettingsNavLink active={activeSection === 'roles'} label="Roles & access" onClick={() => navigateToSection('roles')} />

              <div className="settings-side-group-label">Connected</div>
              <SettingsNavLink active={activeSection === 'workable'} label="Workable" onClick={() => navigateToSection('workable')} />
              <SettingsNavLink active={activeSection === 'sso'} label="SSO / SAML" onClick={() => navigateToSection('sso')} />
              <SettingsNavLink active={activeSection === 'api'} label="API keys" onClick={() => navigateToSection('api')} />

              <div className="settings-side-group-label">Account</div>
              <SettingsNavLink active={activeSection === 'billing'} label="Billing" onClick={() => navigateToSection('billing')} />
              <SettingsNavLink active={activeSection === 'notifications'} label="Notifications" onClick={() => navigateToSection('notifications')} />
            </aside>

            <main className="settings-main">
              <div ref={(node) => { sectionRefs.current.org = node; }}>
                <SectionPanel
                  id="org"
                  title="Organization"
                  subtitle="How your workspace shows up to candidates and panel members."
                >
                  <div className="row-form">
                    <label className="field">
                      <span className="k">Workspace name</span>
                      <input
                        value={orgData?.name || ''}
                        onChange={(event) => setOrgData((prev) => ({ ...(prev || {}), name: event.target.value }))}
                      />
                    </label>
                    <label className="field">
                      <span className="k">Primary domain</span>
                      <input
                        value={workspaceForm.primary_domain}
                        onChange={(event) => setWorkspaceForm((prev) => ({ ...prev, primary_domain: event.target.value }))}
                      />
                    </label>
                    <label className="field">
                      <span className="k">Candidate-facing brand</span>
                      <input
                        value={workspaceForm.candidate_facing_brand}
                        onChange={(event) => setWorkspaceForm((prev) => ({ ...prev, candidate_facing_brand: event.target.value }))}
                      />
                    </label>
                    <label className="field">
                      <span className="k">Locale</span>
                      <input
                        value={workspaceForm.locale}
                        onChange={(event) => setWorkspaceForm((prev) => ({ ...prev, locale: event.target.value }))}
                      />
                    </label>
                  </div>
                  <div className="settings-save-row">
                    <div className="settings-inline-note">Workspace settings apply to new recruiter-facing report surfaces immediately.</div>
                    <button type="button" className="btn btn-purple btn-sm" onClick={handleSaveWorkspace} disabled={workspaceSaving}>
                      {workspaceSaving ? 'Saving...' : 'Save organization'}
                    </button>
                  </div>
                </SectionPanel>
              </div>

              <div ref={(node) => { sectionRefs.current.scoring = node; }}>
                <SectionPanel
                  id="scoring"
                  title="Scoring policy"
                  subtitle="Default CV scoring criteria and assessment scoring dimensions."
                >
                  <div className="settings-subgrid">
                    <div className="settings-subcard">
                      <div className="settings-subcard-head">
                        <div>
                          <h3>Default scoring criteria</h3>
                          <p>
                            Auto-applied to every newly imported or created role. Recruiters can override per role from the role pipeline page.
                          </p>
                        </div>
                      </div>
                      <label className="field">
                        <span className="k">Criteria (used by AI when scoring CVs)</span>
                        <textarea
                          rows={8}
                          value={defaultAdditionalRequirements}
                          onChange={(event) => setDefaultAdditionalRequirements(event.target.value)}
                          placeholder={`One requirement per line. Prefix with the priority so the AI weighs it correctly.

Examples:
Must have: 5+ years building data pipelines on AWS
Preferred: Banking or fintech background
Nice to have: AWS Solutions Architect certification
Constraint: Based in UAE (no remote)
Disqualifying: No experience with regulated financial data`}
                          maxLength={12000}
                        />
                      </label>
                      <div className="settings-save-row">
                        <div className="settings-inline-note">
                          Existing roles keep their current criteria. New roles inherit this list when imported from Workable or created manually.
                        </div>
                        <button
                          type="button"
                          className="btn btn-purple btn-sm"
                          onClick={handleSaveDefaultRequirements}
                          disabled={defaultRequirementsSaving}
                        >
                          {defaultRequirementsSaving ? 'Saving...' : 'Save default criteria'}
                        </button>
                      </div>
                    </div>
                  </div>

                  <div className="settings-toggle-list settings-top-gap">
                    <ToggleCard
                      title="Prompt quality"
                      description="Reward scoped, single-decision prompts. Penalize vague requests."
                      checked={scoringPolicyForm.prompt_quality}
                      onChange={(value) => setScoringPolicyForm((prev) => ({ ...prev, prompt_quality: value }))}
                    />
                    <ToggleCard
                      title="Error recovery"
                      description="Credit candidates who flag or reject incorrect AI output."
                      checked={scoringPolicyForm.error_recovery}
                      onChange={(value) => setScoringPolicyForm((prev) => ({ ...prev, error_recovery: value }))}
                    />
                    <ToggleCard
                      title="Independence"
                      description="Measure which parts of the final code were written by the human vs. the model."
                      checked={scoringPolicyForm.independence}
                      onChange={(value) => setScoringPolicyForm((prev) => ({ ...prev, independence: value }))}
                    />
                    <ToggleCard
                      title="Context utilization"
                      description="Track how AI suggestions are reviewed before being accepted."
                      checked={scoringPolicyForm.context_utilization}
                      onChange={(value) => setScoringPolicyForm((prev) => ({ ...prev, context_utilization: value }))}
                    />
                    <ToggleCard
                      title="Design thinking"
                      description="Credit decisions that connect the fix to its blast radius across the system."
                      checked={scoringPolicyForm.design_thinking}
                      onChange={(value) => setScoringPolicyForm((prev) => ({ ...prev, design_thinking: value }))}
                    />
                    <ToggleCard
                      title="Time-to-first-signal"
                      description="Include this in the composite score when you want stronger leveling signal."
                      checked={scoringPolicyForm.time_to_first_signal}
                      onChange={(value) => setScoringPolicyForm((prev) => ({ ...prev, time_to_first_signal: value }))}
                    />
                  </div>
                  <div className="settings-save-row">
                    <div className="settings-inline-note">Changes apply to new assessments after today.</div>
                    <button type="button" className="btn btn-purple btn-sm" onClick={handleSaveScoringPolicy} disabled={scoringSaving}>
                      {scoringSaving ? 'Saving...' : 'Save scoring policy'}
                    </button>
                  </div>
                </SectionPanel>
              </div>

              <div ref={(node) => { sectionRefs.current.ai = node; }}>
                <SectionPanel
                  id="ai"
                  title="AI tooling"
                  subtitle="What the candidate runtime provides during an assessment."
                >
                  <div className="settings-toggle-list">
                    <ToggleCard
                      title="Claude CLI + Chat"
                      description="Full IDE plus terminal access. Default for all assessments."
                      checked={aiToolingForm.claude_enabled}
                      onChange={(value) => setAiToolingForm((prev) => ({ ...prev, claude_enabled: value }))}
                      badge={<span className="chip purple">ENABLED</span>}
                    />
                    <ToggleCard
                      title="Cursor / Copilot inline"
                      description="Autocomplete only, without a chat pane. Useful for pure-craft roles."
                      checked={aiToolingForm.cursor_inline_enabled}
                      onChange={(value) => setAiToolingForm((prev) => ({ ...prev, cursor_inline_enabled: value }))}
                    />
                    <ToggleCard
                      title="No-AI baseline"
                      description="Run the same task without AI to calibrate collaboration lift per role."
                      checked={aiToolingForm.no_ai_baseline_enabled}
                      onChange={(value) => setAiToolingForm((prev) => ({ ...prev, no_ai_baseline_enabled: value }))}
                    />
                  </div>
                  <div className="row-form settings-top-gap">
                    <label className="field">
                      <span className="k">Claude credit per candidate</span>
                      <input
                        type="number"
                        min={0}
                        step="0.5"
                        value={aiToolingForm.claude_credit_per_candidate_usd}
                        onChange={(event) => setAiToolingForm((prev) => ({ ...prev, claude_credit_per_candidate_usd: event.target.value }))}
                      />
                    </label>
                    <label className="field">
                      <span className="k">Hard session timeout (minutes)</span>
                      <input
                        type="number"
                        min={15}
                        max={240}
                        value={aiToolingForm.session_timeout_minutes}
                        onChange={(event) => setAiToolingForm((prev) => ({ ...prev, session_timeout_minutes: event.target.value }))}
                      />
                    </label>
                    <label className="field">
                      <span className="k">Default assessment duration (minutes)</span>
                      <input
                        type="number"
                        min={15}
                        max={180}
                        value={aiToolingForm.defaultAssessmentMinutes}
                        onChange={(event) => setAiToolingForm((prev) => ({ ...prev, defaultAssessmentMinutes: event.target.value }))}
                      />
                    </label>
                  </div>
                  <div className="settings-save-row">
                    <div className="settings-inline-note">Credit caps are per candidate, per assessment.</div>
                    <button type="button" className="btn btn-purple btn-sm" onClick={handleSaveAiTooling} disabled={aiSaving}>
                      {aiSaving ? 'Saving...' : 'Save AI tooling'}
                    </button>
                  </div>
                </SectionPanel>
              </div>

              <div ref={(node) => { sectionRefs.current.members = node; }}>
                <SectionPanel
                  id="members"
                  title="Members"
                  subtitle={`${teamMembers.length} people in this workspace.`}
                >
                  <form className="settings-invite-form" onSubmit={handleInvite}>
                    <label className="field">
                      <span className="k">Full name</span>
                      <input value={inviteName} onChange={(event) => setInviteName(event.target.value)} placeholder="Alex Weston" />
                    </label>
                    <label className="field">
                      <span className="k">Email</span>
                      <input value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} placeholder="alex@company.com" />
                    </label>
                    <div className="settings-member-actions">
                      <button type="submit" className="btn btn-purple btn-sm" disabled={inviteLoading}>
                        {inviteLoading ? 'Inviting...' : '+ Invite member'}
                      </button>
                    </div>
                  </form>
                  <div className="members">
                    {teamMembers.map((member) => {
                      const isSelf = member?.email === user?.email;
                      return (
                        <div key={member.id} className="mb">
                          <div className="av">{initialsFor(member.full_name || member.email)}</div>
                          <div className="who">
                            <b>{member.full_name || member.email}</b>
                            <div>{isSelf ? 'owner · you' : (member.is_email_verified ? 'member' : 'invited')}</div>
                          </div>
                          <span className="chip">{isSelf ? 'admin' : 'member'}</span>
                          <button type="button" className="btn btn-outline btn-sm" disabled>
                            Manage
                          </button>
                        </div>
                      );
                    })}
                    {teamMembers.length === 0 ? (
                      <div className="settings-empty-state">
                        No team members yet.
                      </div>
                    ) : null}
                  </div>
                </SectionPanel>
              </div>

              <div ref={(node) => { sectionRefs.current.roles = node; }}>
                <SectionPanel
                  id="roles"
                  title="Roles & access"
                  subtitle="Control who can join this workspace and which recruiter surfaces are enabled."
                >
                  <div className="settings-toggle-list">
                    <ToggleCard
                      title="Candidate feedback reports"
                      description="Allow candidate-facing post-assessment feedback reports."
                      checked={accessForm.candidateFeedbackEnabled}
                      onChange={(value) => setAccessForm((prev) => ({ ...prev, candidateFeedbackEnabled: value }))}
                    />
                  </div>
                  <div className="row-form settings-top-gap">
                    <label className="field">
                      <span className="k">Allowed email domains (comma separated)</span>
                      <input
                        value={accessForm.allowedEmailDomains}
                        onChange={(event) => setAccessForm((prev) => ({ ...prev, allowedEmailDomains: event.target.value }))}
                        placeholder="company.com, subsidiary.org"
                      />
                    </label>
                    <div className="settings-summary-card">
                      <div className="settings-summary-label">Current access model</div>
                      <div className="settings-summary-value">{teamMembers.length || 0} members</div>
                      <div className="settings-summary-note">
                        {accessForm.allowedEmailDomains.trim()
                          ? `Invites limited to ${accessForm.allowedEmailDomains}.`
                          : 'Invites are currently open to any verified domain.'}
                      </div>
                    </div>
                  </div>
                  <div className="settings-save-row">
                    <div className="settings-inline-note">Team invites respect the allowed domain list immediately.</div>
                    <button type="button" className="btn btn-purple btn-sm" onClick={handleSaveAccess} disabled={accessSaving}>
                      {accessSaving ? 'Saving...' : 'Save access settings'}
                    </button>
                  </div>
                </SectionPanel>
              </div>

              <div ref={(node) => { sectionRefs.current.workable = node; }}>
                <SectionPanel
                  id="workable"
                  title="Workable integration"
                  subtitle="Pull jobs and candidates from Workable, then write invite and outcome actions back."
                >
                  <div className="wk-status">
                    <WorkableLogo size={44} />
                    <div>
                      <h4>{workableConnected ? `${orgData?.workable_subdomain || 'workspace'}.workable.com` : 'Workable not connected'}</h4>
                      <div className="meta">
                        <span>
                          <SyncPulse status={workableHealth} />
                          <span>{workableConnected ? ' Healthy' : ' Waiting for connection'}</span>
                        </span>
                        <span>Last sync: <b>{orgData?.workable_last_sync_at ? formatRelativeDateTime(orgData.workable_last_sync_at) : 'Never'}</b></span>
                        <span>Token: <b>{selectedWorkableScopes.includes('w_candidates') ? 'write-back' : 'read-only'}</b></span>
                        <span>Next pull: <b>{nextWorkablePull ? formatRelativeDateTime(nextWorkablePull.toISOString()) : 'Not scheduled'}</b></span>
                      </div>
                    </div>
                    <div className="settings-inline-actions">
                      {!workableConnected ? (
                        ConnectWorkableButton ? (
                          <ConnectWorkableButton onClick={() => setWorkableDrawerOpen(true)} />
                        ) : (
                          <button type="button" className="btn btn-purple btn-sm" onClick={() => setWorkableDrawerOpen(true)}>
                            Connect Workable
                          </button>
                        )
                      ) : (
                        <>
                          <button type="button" className="btn btn-outline btn-sm" onClick={() => setWorkableDrawerOpen(true)}>
                            Manage
                          </button>
                          <button
                            type="button"
                            className="btn btn-purple btn-sm"
                            onClick={handleSyncWorkable}
                            disabled={workableSyncLoading || workableSyncInProgress}
                          >
                            {workableSyncLoading || workableSyncInProgress ? 'Syncing...' : 'Sync now'}
                          </button>
                        </>
                      )}
                    </div>
                  </div>

                  <div className="wk-summary">
                    <div className="settings-inline-actions space-between">
                      <div className="mono-label">Last sync</div>
                      <button type="button" className="settings-link-button" onClick={() => void loadWorkableSyncJobs()}>
                        Refresh roles
                      </button>
                    </div>
                    <div className="wk-summary-row">
                      <div className="stat">
                        <div className="n">{Number(lastSyncSummary.jobs_seen || workableJobs.length || 0)}</div>
                        <div className="l">Open jobs</div>
                      </div>
                      <div className="stat">
                        <div className="n">{Number(lastSyncSummary.active_candidates || lastSyncSummary.candidates_seen || 0)}</div>
                        <div className="l">Active candidates</div>
                      </div>
                      <div className="stat">
                        <div className="n">{Number(lastSyncSummary.candidates_upserted || 0)}</div>
                        <div className="l">New since last sync</div>
                      </div>
                      <div className="stat">
                        <div className="n">{Array.isArray(lastSyncSummary.errors) ? lastSyncSummary.errors.length : 0}</div>
                        <div className="l">Errors</div>
                      </div>
                    </div>
                  </div>

                  {workableSyncInProgress ? (
                    <div className="settings-banner warning">
                      <div className="settings-banner-icon"><Spinner size={16} /></div>
                      <div>
                        <div className="settings-banner-title">Sync running in the background</div>
                        <div className="settings-banner-copy">
                          {orgData?.workable_sync_progress?.current_step
                            ? `Current step: ${String(orgData.workable_sync_progress.current_step).replace(/_/g, ' ')}.`
                            : 'We will keep this strip updated while the sync runs.'}
                        </div>
                      </div>
                      <button type="button" className="btn btn-outline btn-sm" onClick={handleCancelWorkableSync} disabled={workableSyncCancelLoading}>
                        {workableSyncCancelLoading ? 'Stopping...' : 'Stop sync'}
                      </button>
                    </div>
                  ) : null}

                  <div className="wk-grid settings-top-gap">
                    <div className={`wk-mode-card ${workableForm.emailMode === 'workable_preferred_fallback_manual' ? 'selected' : ''}`}>
                      <div>
                        <h5>Workable hybrid</h5>
                        <p>Taali invites, scores, and writes candidate activity back as private notes or stage actions.</p>
                      </div>
                      <button
                        type="button"
                        className={`sw ${workableForm.emailMode === 'workable_preferred_fallback_manual' ? 'on' : ''}`}
                        aria-label="Workable hybrid"
                        onClick={() => setWorkableForm((prev) => ({ ...prev, emailMode: 'workable_preferred_fallback_manual' }))}
                      />
                    </div>
                    <div className={`wk-mode-card ${workableForm.emailMode === 'manual_taali' ? 'selected' : ''}`}>
                      <div>
                        <h5>Manual</h5>
                        <p>Workable stays read-only while Taali manages invites and review locally.</p>
                      </div>
                      <button
                        type="button"
                        className={`sw ${workableForm.emailMode === 'manual_taali' ? 'on' : ''}`}
                        aria-label="Manual"
                        onClick={() => setWorkableForm((prev) => ({ ...prev, emailMode: 'manual_taali', inviteStageName: '' }))}
                      />
                    </div>
                  </div>

                  <div className="row-form settings-top-gap">
                    <label className="field">
                      <span className="k">Default sync mode</span>
                      <select
                        value={workableForm.defaultSyncMode}
                        onChange={(event) => setWorkableForm((prev) => ({ ...prev, defaultSyncMode: event.target.value }))}
                      >
                        <option value="full">Full sync</option>
                        <option value="metadata">Metadata sync</option>
                      </select>
                    </label>
                    <label className="field">
                      <span className="k">Sync interval (minutes)</span>
                      <input
                        type="number"
                        min={5}
                        max={1440}
                        value={workableForm.syncIntervalMinutes}
                        onChange={(event) => setWorkableForm((prev) => ({ ...prev, syncIntervalMinutes: event.target.value }))}
                      />
                    </label>
                    <label className="field">
                      <span className="k">Invite stage name</span>
                      <input
                        list="workable-stage-options"
                        value={workableForm.inviteStageName}
                        onChange={(event) => setWorkableForm((prev) => ({ ...prev, inviteStageName: event.target.value }))}
                        placeholder="Assessment invited"
                      />
                    </label>
                    <label className="field">
                      <span className="k">Workable actor member</span>
                      <select
                        value={workableForm.workableActorMemberId}
                        onChange={(event) => setWorkableForm((prev) => ({ ...prev, workableActorMemberId: event.target.value }))}
                      >
                        <option value="">{workableMembersLoading ? 'Loading members...' : 'Select member'}</option>
                        {workableMembers.map((member) => {
                          const memberId = String(member?.id || member?.member_id || '').trim();
                          if (!memberId) return null;
                          return <option key={memberId} value={memberId}>{workableMemberLabel(member)}</option>;
                        })}
                      </select>
                    </label>
                    <label className="field">
                      <span className="k">Default disqualification reason</span>
                      <select
                        value={workableForm.workableDisqualifyReasonId}
                        onChange={(event) => setWorkableForm((prev) => ({ ...prev, workableDisqualifyReasonId: event.target.value }))}
                      >
                        <option value="">{workableReasonsLoading ? 'Loading reasons...' : 'Optional reason'}</option>
                        {workableReasons.map((reason) => {
                          const reasonId = String(reason?.id || reason?.reason_id || '').trim();
                          if (!reasonId) return null;
                          return <option key={reasonId} value={reasonId}>{workableReasonLabel(reason)}</option>;
                        })}
                      </select>
                    </label>
                    <label className="field">
                      <span className="k">Auto-reject threshold (0-100)</span>
                      <input
                        type="number"
                        min={0}
                        max={100}
                        value={workableForm.autoRejectThreshold100}
                        onChange={(event) => setWorkableForm((prev) => ({ ...prev, autoRejectThreshold100: event.target.value }))}
                      />
                    </label>
                  </div>

                  <div className="settings-toggle-list settings-top-gap">
                    <ToggleCard
                      title="Enable Workable auto-reject"
                      description="Candidates below the threshold are disqualified in Workable during full sync or re-score."
                      checked={Boolean(workableForm.autoRejectEnabled)}
                      onChange={(value) => setWorkableForm((prev) => ({ ...prev, autoRejectEnabled: value }))}
                    />
                  </div>

                  <label className="field settings-top-gap">
                    <span className="k">Reject note template</span>
                    <textarea
                      rows={4}
                      value={workableForm.autoRejectNoteTemplate}
                      onChange={(event) => setWorkableForm((prev) => ({ ...prev, autoRejectNoteTemplate: event.target.value }))}
                      placeholder="Auto-rejected by Taali. Pre-screen {{pre_screen_score}}/100 below threshold {{threshold}}."
                    />
                  </label>

                  <div className="settings-scope-list settings-top-gap">
                    {WORKABLE_SCOPE_OPTIONS.map((scope) => (
                      <label key={scope.id} className="settings-scope-item">
                        <input
                          type="checkbox"
                          checked={workableSelectedScopes[scope.id]}
                          onChange={() => toggleWorkableScope(scope.id)}
                        />
                        <span>
                          <b>{scope.label}</b>
                          <small>{scope.description}</small>
                        </span>
                      </label>
                    ))}
                  </div>

                  <div className="settings-role-picker settings-top-gap">
                    <div className="settings-role-picker-header">
                      <div>
                        <div className="settings-summary-label">Roles to import</div>
                        <div className="settings-summary-note">
                          {workableSelectedJobShortcodes.length}/{workableJobs.length} selected
                        </div>
                      </div>
                      <div className="settings-inline-actions">
                        <button
                          type="button"
                          className="btn btn-outline btn-sm"
                          onClick={() => setWorkableSelectedJobShortcodes(workableJobs.map((job) => String(job?.shortcode || job?.id || '').trim()).filter(Boolean))}
                          disabled={workableJobsLoading || workableJobs.length === 0}
                        >
                          Select all
                        </button>
                        <button
                          type="button"
                          className="btn btn-outline btn-sm"
                          onClick={() => setWorkableSelectedJobShortcodes([])}
                          disabled={workableJobsLoading || workableSelectedJobShortcodes.length === 0}
                        >
                          Clear
                        </button>
                      </div>
                    </div>
                    <input
                      className="settings-search-input"
                      value={workableJobSearch}
                      onChange={(event) => setWorkableJobSearch(event.target.value)}
                      placeholder="Search role name or shortcode"
                    />
                    {workableJobsError ? <div className="settings-error-copy">{workableJobsError}</div> : null}
                    <div className="settings-role-picker-list">
                      {workableJobsLoading ? (
                        <div className="settings-empty-state">Loading Workable roles...</div>
                      ) : filteredWorkableSyncJobs.length === 0 ? (
                        <div className="settings-empty-state">No roles match your search.</div>
                      ) : filteredWorkableSyncJobs.map((job) => {
                        const identifier = String(job?.shortcode || job?.id || '').trim();
                        if (!identifier) return null;
                        return (
                          <label key={identifier} className="settings-scope-item">
                            <input
                              type="checkbox"
                              checked={selectedRoleSetForSync.has(identifier)}
                              onChange={() => setWorkableSelectedJobShortcodes((prev) => (
                                prev.includes(identifier)
                                  ? prev.filter((item) => item !== identifier)
                                  : [...prev, identifier]
                              ))}
                            />
                            <span>
                              <b>{job?.title || identifier}</b>
                              <small>{identifier}</small>
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  </div>

                  <div className="settings-save-row">
                    <div className="settings-inline-note">Workable-first mode uses pre-screen score for ranking and write-back.</div>
                    <button type="button" className="btn btn-purple btn-sm" onClick={handleSaveWorkable} disabled={workableSaving}>
                      {workableSaving ? 'Saving...' : 'Save Workable Settings'}
                    </button>
                  </div>

                  <div className="settings-danger-card">
                    <div>
                      <div className="settings-danger-title">Remove all Workable data</div>
                      <div className="settings-danger-copy">
                        This deletes all roles, candidates, and applications imported from Workable.
                      </div>
                    </div>
                    <button type="button" className="btn btn-outline btn-sm danger" onClick={() => setClearWorkableModalOpen(true)}>
                      Remove data
                    </button>
                  </div>

                  <datalist id="workable-stage-options">
                    {workableStages.map((stage, index) => {
                      const label = workableStageLabel(stage);
                      return label ? <option key={`${label}-${index}`} value={label} /> : null;
                    })}
                  </datalist>
                </SectionPanel>
              </div>

              <div ref={(node) => { sectionRefs.current.sso = node; }}>
                <SectionPanel
                  id="sso"
                  title="SSO / SAML"
                  subtitle="Identity provider enforcement and metadata configuration."
                >
                  <div className="settings-toggle-list">
                    <ToggleCard
                      title="Enforce SSO"
                      description="Block password login and team invites. Provision access through your identity provider."
                      checked={ssoForm.ssoEnforced}
                      onChange={(value) => setSsoForm((prev) => ({ ...prev, ssoEnforced: value }))}
                    />
                    <ToggleCard
                      title="Enable SAML metadata"
                      description="Store SAML metadata so this workspace can be connected to an IdP."
                      checked={ssoForm.samlEnabled}
                      onChange={(value) => setSsoForm((prev) => ({ ...prev, samlEnabled: value }))}
                    />
                  </div>
                  <div className="row-form settings-top-gap">
                    <label className="field">
                      <span className="k">SAML metadata URL</span>
                      <input
                        type="url"
                        placeholder="https://idp.example.com/metadata.xml"
                        value={ssoForm.samlMetadataUrl}
                        onChange={(event) => setSsoForm((prev) => ({ ...prev, samlMetadataUrl: event.target.value }))}
                      />
                    </label>
                  </div>
                  <div className="settings-save-row">
                    <div className="settings-inline-note">SAML metadata is required when SAML is enabled.</div>
                    <button type="button" className="btn btn-purple btn-sm" onClick={handleSaveSso} disabled={ssoSaving}>
                      {ssoSaving ? 'Saving...' : 'Save SSO settings'}
                    </button>
                  </div>
                </SectionPanel>
              </div>

              <div ref={(node) => { sectionRefs.current.api = node; }}>
                <SectionPanel
                  id="api"
                  title="API keys"
                  subtitle="Workspace-wide API credentials and recruiter email defaults."
                >
                  <div className="settings-subgrid">
                    <div className="settings-subcard">
                      <div className="settings-subcard-head">
                        <Mail size={18} />
                        <div>
                          <h3>Invite template</h3>
                          <p>Default invite body for manual recruiter sends.</p>
                        </div>
                      </div>
                      <label className="field">
                        <span className="k">Template body</span>
                        <textarea
                          rows={5}
                          value={emailTemplatePreview}
                          onChange={(event) => setEmailTemplatePreview(event.target.value)}
                        />
                      </label>
                    </div>
                  </div>

                  <div className="settings-save-row">
                    <div className="settings-inline-note">
                      Supports placeholders like {'{{candidate_name}}'} and {'{{assessment_link}}'}.
                    </div>
                    <button type="button" className="btn btn-purple btn-sm" onClick={handleSaveApiKeys} disabled={apiSaving}>
                      {apiSaving ? 'Saving...' : 'Save API key settings'}
                    </button>
                  </div>

                  <div className="settings-subcard settings-top-gap">
                    <div className="settings-subcard-head">
                      <KeyRound size={18} />
                      <div>
                        <h3>Fireflies transcript ingestion</h3>
                        <p>Link screening and technical interview transcripts back to candidate records.</p>
                      </div>
                    </div>
                    <div className="row-form">
                      <label className="field">
                        <span className="k">Owner email</span>
                        <input
                          type="email"
                          value={firefliesForm.ownerEmail}
                          onChange={(event) => setFirefliesForm((prev) => ({ ...prev, ownerEmail: event.target.value }))}
                          placeholder="recruiter@company.com"
                        />
                      </label>
                      <label className="field">
                        <span className="k">Invite email</span>
                        <input
                          type="email"
                          value={firefliesForm.inviteEmail}
                          onChange={(event) => setFirefliesForm((prev) => ({ ...prev, inviteEmail: event.target.value }))}
                          placeholder="taali@fireflies.ai"
                        />
                      </label>
                      <label className="field">
                        <span className="k">Mode</span>
                        <select
                          value={firefliesForm.singleAccountMode ? 'single_account' : 'shared'}
                          onChange={(event) => setFirefliesForm((prev) => ({ ...prev, singleAccountMode: event.target.value !== 'shared' }))}
                        >
                          <option value="single_account">Single recruiter-owned account</option>
                          <option value="shared">Shared / multi-account</option>
                        </select>
                      </label>
                      <label className="field">
                        <span className="k">API key</span>
                        <input
                          type="password"
                          value={firefliesForm.apiKey}
                          onChange={(event) => {
                            const nextValue = event.target.value;
                            setFirefliesForm((prev) => ({ ...prev, apiKey: nextValue }));
                            if (nextValue.trim()) setFirefliesClearApiKey(false);
                          }}
                          placeholder={firefliesHasApiKey ? 'Leave blank to keep current key' : 'Enter Fireflies API key'}
                        />
                      </label>
                      <label className="field">
                        <span className="k">Webhook secret</span>
                        <input
                          type="password"
                          value={firefliesForm.webhookSecret}
                          onChange={(event) => {
                            const nextValue = event.target.value;
                            setFirefliesForm((prev) => ({ ...prev, webhookSecret: nextValue }));
                            if (nextValue.trim()) setFirefliesClearWebhookSecret(false);
                          }}
                          placeholder={firefliesWebhookSecretConfigured ? 'Leave blank to keep current secret' : 'Enter Fireflies webhook secret'}
                        />
                      </label>
                    </div>
                    <div className="settings-chip-row">
                      <span className={`chip ${firefliesHasApiKey ? 'green' : ''}`}>
                        {firefliesClearApiKey ? 'API key will be cleared' : (firefliesHasApiKey ? 'API key configured' : 'API key missing')}
                      </span>
                      <span className={`chip ${firefliesWebhookSecretConfigured ? 'green' : ''}`}>
                        {firefliesClearWebhookSecret ? 'Webhook secret will be cleared' : (firefliesWebhookSecretConfigured ? 'Webhook secret configured' : 'Webhook secret missing')}
                      </span>
                      {firefliesHasApiKey ? (
                        <button
                          type="button"
                          className="btn btn-outline btn-sm"
                          onClick={() => {
                            setFirefliesForm((prev) => ({ ...prev, apiKey: '' }));
                            setFirefliesClearApiKey(true);
                          }}
                        >
                          Clear stored API key
                        </button>
                      ) : null}
                      {firefliesWebhookSecretConfigured ? (
                        <button
                          type="button"
                          className="btn btn-outline btn-sm"
                          onClick={() => {
                            setFirefliesForm((prev) => ({ ...prev, webhookSecret: '' }));
                            setFirefliesClearWebhookSecret(true);
                          }}
                        >
                          Clear webhook secret
                        </button>
                      ) : null}
                    </div>
                    <div className="settings-save-row">
                      <div className="settings-inline-note">Fireflies matching is conservative and leaves ambiguous transcripts in review.</div>
                      <button type="button" className="btn btn-purple btn-sm" onClick={handleSaveFireflies} disabled={firefliesSaving}>
                        {firefliesSaving ? 'Saving...' : 'Save Fireflies Settings'}
                      </button>
                    </div>
                  </div>
                </SectionPanel>
              </div>

              <div ref={(node) => { sectionRefs.current.billing = node; }}>
                <SectionPanel
                  id="billing"
                  title="Billing"
                  subtitle="Usage, credits, and spend thresholds for this workspace."
                >
                  {billingLoading ? (
                    <div className="settings-loading-inline">
                      <Spinner size={18} />
                      Loading billing...
                    </div>
                  ) : (
                    <>
                      <div className="settings-billing-summary">
                        <div className="settings-billing-card">
                          <div className="settings-summary-label">Current plan</div>
                          <div className="settings-summary-value">{orgData?.plan || 'Pay per use'}</div>
                          <div className="settings-summary-note">Total usage {toAedWithUsdLabel(monthlyCost)} across {usageHistory.length} assessments.</div>
                        </div>
                        <div className="settings-billing-card">
                          <div className="settings-summary-label">Credits balance</div>
                          <div className="settings-summary-value">{creditsBalance}</div>
                          <div className="settings-summary-note">Add more credits for upcoming hiring bursts.</div>
                        </div>
                        <div className="settings-billing-card">
                          <div className="settings-summary-label">Daily spend threshold</div>
                          <div className="settings-summary-value">{toAedWithUsdLabel(thresholdConfig.daily_spend_usd ?? 0, null, { maximumFractionDigits: 2 })}</div>
                          <div className="settings-summary-note">
                            Today: {toAedWithUsdLabel(Number(spendSummary.daily_spend_usd || 0), null, { maximumFractionDigits: 2 })} · {thresholdStatus.daily_spend_exceeded ? 'Exceeded' : 'Within threshold'}
                          </div>
                        </div>
                      </div>
                      <div className="settings-credit-packs">
                        {Object.entries(packCatalog).map(([packId, pack]) => (
                          <button
                            key={packId}
                            type="button"
                            className="settings-credit-pack"
                            onClick={() => handleAddCredits(packId)}
                            disabled={checkoutLoading}
                          >
                            <span>{pack.label || packId}</span>
                            <span className="settings-credit-pack-meta">
                              {checkoutLoading ? <Spinner size={14} /> : <CreditCard size={14} />}
                              +{pack.credits || 0}
                            </span>
                          </button>
                        ))}
                      </div>
                      <div className="settings-usage-table">
                        <div className="settings-usage-head">
                          <h3>Usage history</h3>
                        </div>
                        <table>
                          <thead>
                            <tr>
                              <th>Date</th>
                              <th>Candidate</th>
                              <th>Task</th>
                              <th>Cost</th>
                            </tr>
                          </thead>
                          <tbody>
                            {usageHistory.length === 0 ? (
                              <tr>
                                <td colSpan={4} className="empty">No usage yet. Completed assessments will appear here.</td>
                              </tr>
                            ) : usageHistory.map((row, index) => (
                              <tr key={row.assessment_id ?? index}>
                                <td>{row.date}</td>
                                <td>{row.candidate}</td>
                                <td>{row.task}</td>
                                <td>{toAedWithUsdLabel(row.cost)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )}
                </SectionPanel>
              </div>

              <div ref={(node) => { sectionRefs.current.notifications = node; }}>
                <SectionPanel
                  id="notifications"
                  title="Notifications"
                  subtitle="Choose which recruiter updates should reach this workspace."
                >
                  <div className="settings-toggle-list">
                    <ToggleCard
                      title="Candidate updates"
                      description="Notify the team when candidates submit, expire, or upload missing documents."
                      checked={notificationPreferencesForm.candidate_updates}
                      onChange={(value) => setNotificationPreferencesForm((prev) => ({ ...prev, candidate_updates: value }))}
                    />
                    <ToggleCard
                      title="Daily digest"
                      description="Send a once-a-day summary across jobs, candidates, and sync health."
                      checked={notificationPreferencesForm.daily_digest}
                      onChange={(value) => setNotificationPreferencesForm((prev) => ({ ...prev, daily_digest: value }))}
                    />
                    <ToggleCard
                      title="Panel reminders"
                      description="Remind panel members when interview packs or standing reports are ready to review."
                      checked={notificationPreferencesForm.panel_reminders}
                      onChange={(value) => setNotificationPreferencesForm((prev) => ({ ...prev, panel_reminders: value }))}
                    />
                    <ToggleCard
                      title="Sync failures"
                      description="Alert the workspace if Workable or transcript syncs need attention."
                      checked={notificationPreferencesForm.sync_failures}
                      onChange={(value) => setNotificationPreferencesForm((prev) => ({ ...prev, sync_failures: value }))}
                    />
                  </div>
                  <div className="settings-save-row">
                    <div className="settings-inline-note">Notification preferences are stored at the workspace level for now.</div>
                    <button type="button" className="btn btn-purple btn-sm" onClick={handleSaveNotifications} disabled={notificationsSaving}>
                      {notificationsSaving ? 'Saving...' : 'Save notification settings'}
                    </button>
                  </div>
                </SectionPanel>
              </div>
            </main>
          </div>
        )}
      </div>

      <Sheet
        open={workableDrawerOpen}
        onClose={() => {
          setWorkableConnectError('');
          setWorkableDrawerOpen(false);
        }}
        title="Connect Workable"
        description="Choose how Taali should connect before starting the OAuth or token flow."
        footer={null}
      >
        <div className="space-y-5">
          <div className="settings-segmented">
            <button
              type="button"
              className={workableConnectMode === 'oauth' ? 'active' : ''}
              onClick={() => {
                setWorkableConnectMode('oauth');
                setWorkableConnectError('');
              }}
            >
              OAuth
            </button>
            <button
              type="button"
              className={workableConnectMode === 'token' ? 'active' : ''}
              onClick={() => {
                setWorkableConnectMode('token');
                setWorkableConnectError('');
              }}
            >
              API Token
            </button>
          </div>

          <Panel className="space-y-3 p-4">
            <div className="settings-scope-list">
              {WORKABLE_SCOPE_OPTIONS.map((scope) => (
                <label key={scope.id} className="settings-scope-item">
                  <input
                    type="checkbox"
                    checked={workableSelectedScopes[scope.id]}
                    onChange={() => toggleWorkableScope(scope.id)}
                  />
                  <span>
                    <b>{scope.label}</b>
                    <small>{scope.description}</small>
                  </span>
                </label>
              ))}
            </div>
            <div className="settings-inline-note">Selected scopes: {selectedWorkableScopes.join(' ') || 'none'}</div>
            {missingRequiredWorkableScopes.length > 0 ? (
              <div className="settings-error-copy">Workable requires both r_jobs and r_candidates.</div>
            ) : null}
          </Panel>

          {workableConnectMode === 'oauth' ? (
            <div className="space-y-3">
              <p className="settings-inline-note">OAuth is the recommended path for managed production connections.</p>
              <Button type="button" variant="primary" onClick={handleConnectWorkableOAuth} disabled={workableOAuthLoading}>
                {workableOAuthLoading ? 'Redirecting...' : 'Continue with OAuth'}
              </Button>
            </div>
          ) : (
            <form className="space-y-3" onSubmit={handleConnectWorkableToken}>
              <label className="field">
                <span className="k">Workable subdomain</span>
                <input
                  value={workableTokenForm.subdomain}
                  onChange={(event) => setWorkableTokenForm((prev) => ({ ...prev, subdomain: event.target.value }))}
                  placeholder="deeplight-ai"
                />
              </label>
              <label className="field">
                <span className="k">API access token</span>
                <input
                  type="password"
                  value={workableTokenForm.accessToken}
                  onChange={(event) => setWorkableTokenForm((prev) => ({ ...prev, accessToken: event.target.value }))}
                  placeholder="Paste token"
                />
              </label>
              <Button type="submit" variant="primary" disabled={workableTokenSaving}>
                {workableTokenSaving ? 'Connecting...' : 'Connect token'}
              </Button>
            </form>
          )}

          {workableConnectError ? (
            <div className="settings-error-copy">{workableConnectError}</div>
          ) : null}
        </div>
      </Sheet>

      {clearWorkableModalOpen ? (
        <div className="settings-modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="clear-workable-title">
          <Panel className="settings-modal">
            <h2 id="clear-workable-title">Remove all Workable data?</h2>
            <p>This will delete every role, candidate, and application imported from Workable.</p>
            <div className="settings-inline-actions end">
              <button type="button" className="btn btn-outline btn-sm" onClick={() => setClearWorkableModalOpen(false)} disabled={clearWorkableLoading}>
                Cancel
              </button>
              <button type="button" className="btn btn-purple btn-sm danger" onClick={handleClearWorkableData} disabled={clearWorkableLoading}>
                {clearWorkableLoading ? 'Removing...' : 'Remove all data'}
              </button>
            </div>
          </Panel>
        </div>
      ) : null}
    </div>
  );
};

export default SettingsPage;
