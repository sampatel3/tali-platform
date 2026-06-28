import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CreditCard,
} from 'lucide-react';
import { useLocation, useNavigate } from 'react-router-dom';

import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { aedToUsd, formatAed } from '../../lib/currency';
import { organizations as orgsApi, billing as billingApi, team as teamApi } from '../../shared/api';
import { AgentHeader } from '../../shared/layout/AgentHeader';
import {
  Button,
  Panel,
  Select,
  Sheet,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';
import {
  SyncPulse,
  WorkableLogo,
  formatRelativeDateTime,
} from '../../shared/ui/RecruiterDesignPrimitives';
import BackgroundJobsPanel from './BackgroundJobsPanel';
import UsagePanel from './UsagePanel';
import ApiKeysPanel from './ApiKeysPanel';
import CriteriaEditor from '../../shared/ui/CriteriaEditor';

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
// HANDOFF settings.md — Notifications tab covers 6 toggles. The two
// new keys (spend_over_budget, agent_paused) are persisted in the same
// notification_preferences JSON column the backend already accepts.
const DEFAULT_NOTIFICATION_PREFERENCES = {
  candidate_updates: true,
  daily_digest: true,
  panel_reminders: true,
  sync_failures: true,
  spend_over_budget: true,
  agent_paused: true,
};
const DEFAULT_FIRELIES_FORM = {
  apiKey: '',
  webhookSecret: '',
  ownerEmail: '',
  inviteEmail: '',
  singleAccountMode: true,
};
// HANDOFF settings.md — final 10-tab layout. Aliases map every legacy URL
// (the v3 `team` / `scoring` / `ai` / `sso` paths, plus a few even older
// names) onto the new canonical tab keys so bookmarked deep-links keep
// working.
const SECTION_ALIASES = {
  '': 'org',
  org: 'org',
  organization: 'org',
  workable: 'workable',
  billing: 'billing',
  usage: 'usage',
  team: 'members',
  members: 'members',
  roles: 'members',
  access: 'members',
  enterprise: 'security',
  sso: 'security',
  saml: 'security',
  security: 'security',
  scoring: 'agent',
  ai: 'agent',
  agent: 'agent',
  preferences: 'email',
  api: 'email',
  email: 'email',
  fireflies: 'email',
  notifications: 'notifications',
  jobs: 'jobs',
  'background-jobs': 'jobs',
  developers: 'developers',
  'api-keys': 'developers',
  apikeys: 'developers',
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

// Workable's disqualification-reasons endpoint returns objects shaped
// `{ id, description }` — `description` is the human-readable label
// (e.g. "Lacks experience"). Earlier shapes used `name`/`title`/`label`
// so we still check those for forwards-compat with custom integrations.
const workableReasonLabel = (reason) => (
  reason?.description
  || reason?.name
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

// Settings → AI agent tab. Workspace defaults inherited at role-create
// time: a chip-based requirements list (must / preferred / constraint),
// a default monthly budget, and a 0..100 score threshold. Uses the
// same settings-subcard styling as every other tab so the IA stays
// consistent.
const AgentDefaultsForm = ({
  criteria,
  criteriaBusy,
  onCreateCriterion,
  onUpdateCriterion,
  onDeleteCriterion,
  budgetUsd,
  threshold,
  onChange,
}) => {
  const thresholdDisplay = Math.max(0, Math.min(100, Number(threshold) || 0));
  const activeCount = (Array.isArray(criteria) ? criteria : []).filter((c) => !c.deleted_at).length;
  return (
    <>
      <div className="settings-subcard">
        <div className="settings-subcard-head">
          <div>
            <h3>Default role criteria</h3>
            <p>Add one criterion at a time and pick the bucket. The agent reads <strong>must-haves</strong> as the bar, <strong>preferred</strong> as positive signals, and <strong>constraints</strong> as logistics (timezone, start date). Every new role inherits these; recruiters can customize per role.</p>
          </div>
        </div>
        <CriteriaEditor
          mode="workspace"
          criteria={criteria}
          busy={criteriaBusy}
          onCreate={onCreateCriterion}
          onUpdate={onUpdateCriterion}
          onDelete={onDeleteCriterion}
        />
      </div>

      <div className="settings-subgrid settings-top-gap">
        <div className="settings-subcard">
          <div className="settings-subcard-head">
            <div>
              <h3>Default budget per role</h3>
              <p>Cap the agent will respect on each new role until a recruiter changes it. Resets monthly.</p>
            </div>
          </div>
          <label className="field">
            <span className="k">Default budget (USD/month)</span>
            <input
              type="number"
              min={0}
              step="5"
              value={budgetUsd}
              onChange={(event) => onChange({ budgetUsd: event.target.value })}
              placeholder="200"
            />
          </label>
        </div>

        <div className="settings-subcard">
          <div className="settings-subcard-head">
            <div>
              <h3>Default score threshold</h3>
              <p>Minimum total score on a new role&apos;s auto-shortlist. Below = flagged for recruiter review.</p>
            </div>
          </div>
          <label className="field">
            <span className="k">Threshold ({thresholdDisplay}/100)</span>
            <input
              type="range"
              min={0}
              max={100}
              value={thresholdDisplay}
              className="ce-range"
              style={{ '--ce-range-val': thresholdDisplay }}
              onChange={(event) => onChange({ threshold: Number(event.target.value) })}
              aria-label="Default score threshold"
            />
          </label>
          <div className="settings-summary-note" style={{ marginTop: 8 }}>
            {activeCount
              ? `${activeCount} default ${activeCount === 1 ? 'criterion' : 'criteria'} will be copied into each new role.`
              : 'No default criteria set yet.'}
          </div>
        </div>
      </div>
    </>
  );
};

const SettingsNavLink = ({ active, label, onClick }) => (
  <button
    type="button"
    className={`mc-settings-link ${active ? 'on' : ''}`.trim()}
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
  // Tracks the org id we've already seeded the various form state
  // objects from. The big "reset forms from orgData" effect below runs
  // every time `orgData` reference changes — including the Workable
  // sync-status polling that does `setOrgData(prev => ({...prev, ...}))`
  // every few seconds. Without this guard the polling would clobber any
  // unsaved input the recruiter just typed (e.g. picking "Workable
  // actor member" then watching it revert when the next poll lands).
  const formsInitForOrgIdRef = useRef(null);
  // One-shot guard: auto-default the disqualification reason at most once
  // per page load. Without this the effect would refire on every reasons
  // refresh and re-write the same value.
  const workableReasonAutoDefaultedRef = useRef(false);

  // HANDOFF v2 §11 — Settings is one page with internal anchor scroll.
  // We still derive the initial section from the URL path so legacy
  // deep links like /settings/billing keep working, but `activeSection`
  // is state-driven after that and updates via in-page navigation
  // (the rail clicks update hash, not history). See navigateToSection.
  const pathSegment = location.pathname.replace(/^\/settings\/?/, '').split('/')[0];
  const initialHashSection = (typeof window !== 'undefined' && window.location.hash)
    ? canonicalSection(String(window.location.hash).replace(/^#/, ''))
    : null;
  const initialSection = initialHashSection || canonicalSection(pathSegment);

  const [activeSection, setActiveSection] = useState(initialSection);
  const [orgData, setOrgData] = useState(null);
  const [orgLoading, setOrgLoading] = useState(true);
  const [workspaceForm, setWorkspaceForm] = useState(DEFAULT_WORKSPACE_SETTINGS);
  const [workspaceSaving, setWorkspaceSaving] = useState(false);
  const [notificationPreferencesForm, setNotificationPreferencesForm] = useState(DEFAULT_NOTIFICATION_PREFERENCES);
  const [notificationsSaving, setNotificationsSaving] = useState(false);
  const [accessForm, setAccessForm] = useState({
    allowedEmailDomains: '',
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
  const [billingBreakdown, setBillingBreakdown] = useState(null);
  const [billingEvents, setBillingEvents] = useState([]);
  const [billingLoading, setBillingLoading] = useState(false);
  const [checkoutLoading, setCheckoutLoading] = useState(false);
  const [emailTemplatePreview, setEmailTemplatePreview] = useState(DEFAULT_INVITE_TEMPLATE);
  const [apiSaving, setApiSaving] = useState(false);
  // HANDOFF settings.md — AI agent tab. Workspace-wide defaults every
  // new role inherits. ``criteria`` are now structured chips loaded
  // separately from /organizations/me/criteria; budget + threshold stay
  // on the org PATCH endpoint.
  const [agentDefaultsForm, setAgentDefaultsForm] = useState({
    budgetUsd: '',
    threshold: 70,
  });
  const [agentDefaultsSaving, setAgentDefaultsSaving] = useState(false);
  const [orgCriteria, setOrgCriteria] = useState([]);
  const [orgCriteriaBusy, setOrgCriteriaBusy] = useState(false);
  // Workspace spend cap (cents). Lives on the Billing tab and is enforced
  // by the agent before it sends new invites.
  const [spendCapForm, setSpendCapForm] = useState({ usd: '' });
  const [spendCapSaving, setSpendCapSaving] = useState(false);
  // 2FA + audit toggle on the Security tab. Persisted alongside SSO.
  const [twoFactorRequired, setTwoFactorRequired] = useState(false);
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
    inviteStageName: '',
    autoRejectEnabled: false,
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
      const [usageRes, costsRes, creditsRes, breakdownRes, eventsRes] = await Promise.all([
        billingApi.usage(),
        billingApi.costs(),
        billingApi.credits(),
        billingApi.usageBreakdown(30),
        billingApi.usageEvents(50),
      ]);
      setBillingUsage(usageRes?.data || null);
      setBillingCosts(costsRes?.data || null);
      setBillingCredits(creditsRes?.data || null);
      setBillingBreakdown(breakdownRes?.data || null);
      setBillingEvents(eventsRes?.data?.events || []);
    } catch {
      setBillingUsage(null);
      setBillingCosts(null);
      setBillingCredits(null);
      setBillingBreakdown(null);
      setBillingEvents([]);
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
    // Fetch each lookup independently so one failure doesn't blank out the
    // others. Surface the failure as a toast so the recruiter can tell the
    // difference between "Workable returned zero" and "the call errored."
    const [membersRes, reasonsRes, stagesRes] = await Promise.allSettled([
      orgsApi.getWorkableMembers(),
      orgsApi.getWorkableDisqualificationReasons(),
      orgsApi.getWorkableStages(),
    ]);
    if (membersRes.status === 'fulfilled') {
      setWorkableMembers(Array.isArray(membersRes.value?.data?.members) ? membersRes.value.data.members : []);
    } else {
      setWorkableMembers([]);
      showToast(getErrorMessage(membersRes.reason, 'Failed to load Workable members.'), 'error');
    }
    if (reasonsRes.status === 'fulfilled') {
      setWorkableReasons(Array.isArray(reasonsRes.value?.data?.disqualification_reasons) ? reasonsRes.value.data.disqualification_reasons : []);
    } else {
      setWorkableReasons([]);
      showToast(getErrorMessage(reasonsRes.reason, 'Failed to load Workable disqualification reasons.'), 'error');
    }
    if (stagesRes.status === 'fulfilled') {
      setWorkableStages(Array.isArray(stagesRes.value?.data?.stages) ? stagesRes.value.data.stages : []);
    } else {
      setWorkableStages([]);
      showToast(getErrorMessage(stagesRes.reason, 'Failed to load Workable stages.'), 'error');
    }
    setWorkableMembersLoading(false);
    setWorkableReasonsLoading(false);
    setWorkableStagesLoading(false);
  }, [orgData?.workable_connected, showToast]);

  // Auto-default the Workable disqualification reason. Every Workable
  // workspace ships with at least one default reason ("Position filled",
  // "Not qualified", etc.), so making the recruiter pick one before the
  // reject path can fire emails is unnecessary friction. When reasons
  // load and the org has no reason configured, pick the first one and
  // persist immediately so the integration "just works" — recruiter can
  // change the choice in settings later. Critically, the *email-firing*
  // automated action in Workable still has to be attached to whichever
  // reason ends up selected, so we surface a toast that nudges them to
  // verify.
  useEffect(() => {
    if (workableReasonAutoDefaultedRef.current) return;
    if (workableReasonsLoading) return;
    if (workableReasons.length === 0) return;
    if (!orgData?.workable_connected) return;
    const currentValue = String(workableForm.workableDisqualifyReasonId || '').trim();
    const persistedValue = String(orgData?.workable_config?.workable_disqualify_reason_id || '').trim();
    if (currentValue || persistedValue) return;
    const firstReason = workableReasons[0] || {};
    const firstReasonId = String(firstReason?.id || firstReason?.reason_id || '').trim();
    if (!firstReasonId) return;
    workableReasonAutoDefaultedRef.current = true;
    setWorkableForm((prev) => ({ ...prev, workableDisqualifyReasonId: firstReasonId }));
    // Persist quietly. We send only this single field — the org PATCH
    // merges into existing workable_config so other fields are unaffected.
    const reasonLabel = workableReasonLabel(firstReason);
    (async () => {
      try {
        await orgsApi.update({
          workable_config: { workable_disqualify_reason_id: firstReasonId },
        });
        showToast(
          `Defaulted disqualification reason to "${reasonLabel}". Verify it has a "Disqualification message" automated action in Workable, or change the reason here.`,
          'success',
        );
      } catch (error) {
        showToast(
          getErrorMessage(error, 'Failed to auto-default the disqualification reason. Pick one and save manually.'),
          'error',
        );
      }
    })();
  }, [
    workableReasons,
    workableReasonsLoading,
    orgData?.workable_connected,
    orgData?.workable_config?.workable_disqualify_reason_id,
    workableForm.workableDisqualifyReasonId,
    showToast,
  ]);

  useEffect(() => {
    void loadOrg();
  }, [loadOrg]);

  useEffect(() => {
    if (activeSection === 'billing') {
      void loadBilling();
    }
    if (activeSection === 'members') {
      void loadTeam();
    }
    if (activeSection === 'workable') {
      void fetchWorkableSyncStatus();
      void loadWorkableSyncJobs();
      void loadWorkableLookups();
    }
  }, [activeSection, fetchWorkableSyncStatus, loadBilling, loadTeam, loadWorkableLookups, loadWorkableSyncJobs]);

  useEffect(() => {
    if (!orgData) {
      formsInitForOrgIdRef.current = null;
      return;
    }
    // Only seed the forms once per org. Without this, the Workable
    // sync-status polling reaches into setOrgData every few seconds,
    // bumps the orgData reference, and re-runs this effect — which
    // wipes any unsaved input the user just typed (e.g. the
    // "Workable actor member" select going back to "Select member"
    // a few seconds after they pick someone).
    if (formsInitForOrgIdRef.current === orgData.id) return;
    formsInitForOrgIdRef.current = orgData.id;
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
    setNotificationPreferencesForm({
      ...DEFAULT_NOTIFICATION_PREFERENCES,
      ...(orgData.notification_preferences || {}),
    });
    setAccessForm({
      allowedEmailDomains: Array.isArray(orgData.allowed_email_domains) ? orgData.allowed_email_domains.join(', ') : '',
    });
    setSsoForm({
      ssoEnforced: Boolean(orgData.sso_enforced),
      samlEnabled: Boolean(orgData.saml_enabled),
      samlMetadataUrl: orgData.saml_metadata_url || '',
    });
    setEmailTemplatePreview(
      String(orgData.invite_email_template || '').trim() || DEFAULT_INVITE_TEMPLATE
    );
    // Agent defaults — budget + threshold come off the org record; chips
    // load separately from /organizations/me/criteria.
    const seedBudgetCents = Number.isFinite(Number(orgData.default_role_budget_cents))
      ? Number(orgData.default_role_budget_cents)
      : null;
    const seedThreshold = Number.isFinite(Number(orgData.default_score_threshold))
      ? Number(orgData.default_score_threshold)
      : 70;
    setAgentDefaultsForm({
      budgetUsd: seedBudgetCents != null ? String((seedBudgetCents / 100).toFixed(2)) : '',
      threshold: Math.max(0, Math.min(100, seedThreshold)),
    });
    const seedCapCents = Number.isFinite(Number(orgData.monthly_spend_cap_cents))
      ? Number(orgData.monthly_spend_cap_cents)
      : null;
    setSpendCapForm({
      usd: seedCapCents != null ? String((seedCapCents / 100).toFixed(2)) : '',
    });
    setTwoFactorRequired(Boolean(orgData.two_factor_required));
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
      inviteStageName: workableConfig.invite_stage_name || '',
      autoRejectEnabled: Boolean(workableConfig.auto_reject_enabled),
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

  // After the initial load, scroll to whichever section was selected
  // (initial section comes from the URL path or hash). After mount,
  // navigateToSection handles its own scroll on click — this effect
  // only fires once when the page becomes ready.
  useEffect(() => {
    if (orgLoading) return;
    const target = sectionRefs.current[activeSection];
    if (!target) return;
    const timer = window.setTimeout(() => {
      if (typeof target.scrollIntoView === 'function') {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }, 0);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgLoading]);

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

  const handleSaveAccess = async () => {
    setAccessSaving(true);
    const domains = String(accessForm.allowedEmailDomains || '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);
    try {
      const res = await orgsApi.update({
        allowed_email_domains: domains,
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
      // The backend silently ignores keys it doesn't recognise (Pydantic
      // strict-mode is off on OrgUpdate), so sending two_factor_required
      // is safe even on workspaces that haven't shipped the column yet.
      const res = await orgsApi.update({
        sso_enforced: Boolean(ssoForm.ssoEnforced),
        saml_enabled: Boolean(ssoForm.samlEnabled),
        saml_metadata_url: String(ssoForm.samlMetadataUrl || '').trim() || null,
        two_factor_required: Boolean(twoFactorRequired),
      });
      setOrgData(res?.data || null);
      showToast('Security settings saved.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save security settings.'), 'error');
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
      showToast('Invite template saved.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save invite template.'), 'error');
    } finally {
      setApiSaving(false);
    }
  };

  const handleSaveAgentDefaults = async () => {
    setAgentDefaultsSaving(true);
    const budgetUsd = Number(agentDefaultsForm.budgetUsd);
    const budgetCents = Number.isFinite(budgetUsd) && budgetUsd > 0
      ? Math.round(budgetUsd * 100)
      : null;
    const threshold = Math.max(0, Math.min(100, Number(agentDefaultsForm.threshold) || 0));
    try {
      const res = await orgsApi.update({
        default_role_budget_cents: budgetCents == null ? 0 : budgetCents,
        default_score_threshold: threshold,
      });
      setOrgData(res?.data || null);
      showToast('Agent defaults saved.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save agent defaults.'), 'error');
    } finally {
      setAgentDefaultsSaving(false);
    }
  };

  const loadOrgCriteria = useCallback(async () => {
    try {
      const res = await orgsApi.listCriteria();
      setOrgCriteria(Array.isArray(res?.data) ? res.data : []);
    } catch (error) {
      // Surface as a toast on first load, then leave the editor empty so
      // the recruiter can still author chips.
      showToast(getErrorMessage(error, 'Failed to load workspace criteria.'), 'error');
      setOrgCriteria([]);
    }
  }, [showToast]);

  const handleCreateOrgCriterion = useCallback(async ({ text, bucket }) => {
    setOrgCriteriaBusy(true);
    try {
      const res = await orgsApi.createCriterion({ text, bucket });
      setOrgCriteria((prev) => [...prev, res?.data].filter(Boolean));
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to add criterion.'), 'error');
    } finally {
      setOrgCriteriaBusy(false);
    }
  }, [showToast]);

  const handleUpdateOrgCriterion = useCallback(async (id, updates) => {
    setOrgCriteriaBusy(true);
    try {
      const res = await orgsApi.updateCriterion(id, updates);
      setOrgCriteria((prev) => prev.map((c) => (c.id === id ? (res?.data || c) : c)));
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to update criterion.'), 'error');
    } finally {
      setOrgCriteriaBusy(false);
    }
  }, [showToast]);

  const handleDeleteOrgCriterion = useCallback(async (id) => {
    setOrgCriteriaBusy(true);
    try {
      await orgsApi.deleteCriterion(id);
      setOrgCriteria((prev) => prev.filter((c) => c.id !== id));
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to remove criterion.'), 'error');
    } finally {
      setOrgCriteriaBusy(false);
    }
  }, [showToast]);

  // Lazy-load workspace chips when the AI agent tab is opened. Defined
  // after ``loadOrgCriteria`` so the dependency exists at first render.
  useEffect(() => {
    if (activeSection === 'agent') {
      void loadOrgCriteria();
    }
  }, [activeSection, loadOrgCriteria]);

  const handleSaveSpendCap = async () => {
    setSpendCapSaving(true);
    // A blank input means "no cap" (Number('') === 0 would otherwise send a
    // hard $0 cap). Send null to clear the cap; only send cents for a real
    // value entered.
    const raw = String(spendCapForm.usd ?? '').trim();
    const usd = Number(raw);
    const cents = raw !== '' && Number.isFinite(usd) && usd >= 0 ? Math.round(usd * 100) : null;
    try {
      const res = await orgsApi.update({ monthly_spend_cap_cents: cents });
      setOrgData(res?.data || null);
      showToast('Spend cap saved.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save spend cap.'), 'error');
    } finally {
      setSpendCapSaving(false);
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
      const res = await billingApi.topup({
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

    if ((emailMode === 'workable_preferred_fallback_manual' || autoRejectEnabled) && !hasWriteScope) {
      showToast('Reconnect Workable with `w_candidates` scope to enable Workable invite, reject, and reopen actions.', 'error');
      return;
    }
    if (emailMode === 'workable_preferred_fallback_manual' && !inviteStageName) {
      showToast('Enter the exact Workable stage name for automated invite mode.', 'error');
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
          invite_stage_name: emailMode === 'workable_preferred_fallback_manual' ? inviteStageName : '',
          auto_reject_enabled: autoRejectEnabled,
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
  // Jobs metadata syncs every 15 min (sync_workable_jobs Beat task).
  // The legacy ``sync_interval_minutes`` config was removed by the 2026-05-20
  // sync redesign — per-candidate cadences live in the beat schedule now.
  const nextWorkablePull = orgData?.workable_last_sync_at
    ? new Date(new Date(orgData.workable_last_sync_at).getTime() + 15 * 60000)
    : null;
  const lastSyncSummary = orgData?.workable_last_sync_summary || {};
  // Usage-based pricing (post 2026-04-29). Balance is in micro-credits
  // ($0.000001 per credit). Packs come pre-shaped from the backend.
  const creditsBalance = Number(billingCredits?.credits_balance ?? orgData?.credits_balance ?? 0);
  const balanceUsd = Number(billingCredits?.credits_balance_usd ?? creditsBalance / 1_000_000);
  const balanceLow = balanceUsd > 0 && balanceUsd < 1.0;
  const creditPacks = Array.isArray(billingCredits?.packs) ? billingCredits.packs : [];
  const featureBreakdown = billingBreakdown?.by_feature || [];
  const breakdownTotalUsd =
    featureBreakdown.reduce((sum, row) => sum + Number(row.credits_charged || 0), 0) / 1_000_000;
  const breakdownTotalEvents = featureBreakdown.reduce(
    (sum, row) => sum + Number(row.event_count || 0), 0,
  );
  const FEATURE_LABELS = {
    prescreen: 'Pre-screening',
    score: 'CV scoring',
    assessment: 'Assessment workspace',
    taali_chat: 'Taali Chat',
    agent_autonomous: 'Autonomous agent',
    cv_parse: 'CV parsing',
    cv_rerank: 'Search rerank',
    search_parse: 'Search query parsing',
    archetype_synthesis: 'Archetype synthesis',
    pairwise_judge: 'Pairwise calibration',
    interview_focus: 'Interview focus',
    interview_tech: 'Tech interview prompts',
    fit_matching: 'Fit matching',
    other: 'Other / unattributed',
  };
  const formatUsd = (n) => `$${Number(n || 0).toFixed(2)}`;
  const formatUsd6 = (n) => `$${Number(n || 0).toFixed(4)}`;

  const navigateToSection = (sectionId) => {
    const next = canonicalSection(sectionId);
    setActiveSection(next);
    if (typeof window !== 'undefined' && window.history?.replaceState) {
      const hash = next === 'org' ? '' : `#${next}`;
      // Collapse any /settings/<section> path the user landed on via a
      // legacy deep link onto the canonical /settings#<section> hash.
      window.history.replaceState(null, '', `/settings${hash}`);
    }
    // v4 spec: each tab is its own focused page (only the active
    // section renders), so scroll-into-view is irrelevant. Scroll the
    // window back to the top so users start at the top of the card.
    if (typeof window !== 'undefined') {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  };

  const renderLoadingState = (
    <div className="flex min-h-[16.25rem] items-center justify-center">
      <Spinner size={32} />
    </div>
  );

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="settings" onNavigate={onNavigate} /> : null}
      <AgentHeader
        breadcrumbs={[{ label: 'Settings' }]}
        kicker="SETTINGS · WORKSPACE"
        title="Settings"
        subtitle="Workspace, scoring policy, integrations, and access. Changes apply to new recruiter-facing surfaces immediately."
        actions={(
          <>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'rgba(255,255,255,0.55)', letterSpacing: '.06em', textTransform: 'uppercase' }}>Workspace</span>
            <span style={{ fontSize: 13, fontWeight: 500, color: '#fff', padding: '5px 10px', background: 'rgba(255,255,255,0.10)', border: '1px solid rgba(255,255,255,0.18)', borderRadius: 8 }}>
              {orgData?.name || user?.organization?.name || 'Workspace'}
            </span>
          </>
        )}
      />
      <div className="mc-page">

        {orgLoading ? renderLoadingState : (
          <div className="mc-settings">
            {/* HANDOFF settings.md — final 10-tab layout. Removed:
                scoring (rubric is product IP), ai tooling (Claude is the
                only LLM), api keys (no public API in v1). The agent tab
                replaces the old scoring + assessment defaults with three
                workspace-wide defaults inherited at role creation. */}
            <div className="vtabs" role="tablist" aria-label="Settings sections">
              {[
                { k: 'org', l: 'Organization' },
                { k: 'members', l: 'Members' },
                { k: 'agent', l: 'AI agent' },
                { k: 'workable', l: 'Workable' },
                { k: 'email', l: 'Email & transcripts' },
                { k: 'notifications', l: 'Notifications' },
                { k: 'billing', l: 'Billing' },
                { k: 'usage', l: 'Usage' },
                { k: 'security', l: 'Security' },
                { k: 'developers', l: 'Developers' },
                { k: 'jobs', l: 'Background jobs' },
              ].map((tab) => (
                <button
                  key={tab.k}
                  type="button"
                  role="tab"
                  aria-selected={activeSection === tab.k}
                  className={`vtab ${activeSection === tab.k ? 'on' : ''}`.trim()}
                  onClick={() => navigateToSection(tab.k)}
                >
                  {tab.l}
                </button>
              ))}
            </div>

            <main className="mc-settings-main">
              <div ref={(node) => { sectionRefs.current.org = node; }} hidden={activeSection !== "org"}>
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

                  {/* Link out to the dedicated requisition-template editor (its
                      own page, not a tab here). Defines what a complete
                      requisition looks like — drives the live brief + the
                      intake agent's questions. */}
                  <div className="settings-subcard settings-top-gap">
                    <div className="settings-subcard-head">
                      <div>
                        <h3>Requisition template</h3>
                        <p>Define what a complete requisition spec looks like — comp, location, logistics, requirements, agent context. Drives the live brief and the questions the intake agent asks.</p>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="btn btn-outline btn-sm"
                      onClick={() => onNavigate?.('settings-requisition-template')}
                    >
                      Edit requisition template →
                    </button>
                  </div>

                  {/* Consultancy clients — managed here (moved out of the top
                      nav). The per-client view + open/filled rollup is reached
                      from the Jobs page's client filter. */}
                  <div className="settings-subcard settings-top-gap">
                    <div className="settings-subcard-head">
                      <div>
                        <h3>Clients</h3>
                        <p>Manage the consultancy clients you hire for. Assign a requisition to a client to track its rate, margin, and open / filled jobs; filter the Jobs page by client to see each client's pipeline.</p>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="btn btn-outline btn-sm"
                      onClick={() => onNavigate?.('clients')}
                    >
                      Manage clients →
                    </button>
                  </div>
                </SectionPanel>
              </div>

              <div ref={(node) => { sectionRefs.current.developers = node; }} hidden={activeSection !== "developers"}>
                <SectionPanel
                  id="developers"
                  title="Developers"
                  subtitle="API keys for the Taali public API. Keys are scoped to this workspace; the secret is shown once on creation."
                >
                  <ApiKeysPanel />
                </SectionPanel>
              </div>

              <div ref={(node) => { sectionRefs.current.agent = node; }} hidden={activeSection !== "agent"}>
                <SectionPanel
                  id="agent"
                  title="AI agent"
                  subtitle="Workspace-wide defaults inherited by every new role. Per-role overrides on the role page win — existing roles are not retroactively updated when these change."
                >
                  <AgentDefaultsForm
                    criteria={orgCriteria}
                    criteriaBusy={orgCriteriaBusy}
                    onCreateCriterion={handleCreateOrgCriterion}
                    onUpdateCriterion={handleUpdateOrgCriterion}
                    onDeleteCriterion={handleDeleteOrgCriterion}
                    budgetUsd={agentDefaultsForm.budgetUsd}
                    threshold={agentDefaultsForm.threshold}
                    onChange={(next) => setAgentDefaultsForm((prev) => ({ ...prev, ...next }))}
                  />
                  <div className="settings-save-row">
                    <div className="settings-inline-note">
                      Criteria save as you add them. Budget &amp; threshold need a save click.
                    </div>
                    <button
                      type="button"
                      className="btn btn-purple btn-sm"
                      onClick={handleSaveAgentDefaults}
                      disabled={agentDefaultsSaving}
                    >
                      {agentDefaultsSaving ? 'Saving...' : 'Save budget & threshold'}
                    </button>
                  </div>
                </SectionPanel>
              </div>

              <div ref={(node) => { sectionRefs.current.members = node; }} hidden={activeSection !== "members"}>
                <SectionPanel
                  id="members"
                  title="Members"
                  subtitle={`${teamMembers.length} ${teamMembers.length === 1 ? 'person' : 'people'} in this workspace.`}
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
                  {/* HANDOFF settings.md — role assignment moved off the
                      removed "Roles & access" tab onto a column on this
                      table. We default to Owner / Admin / Recruiter /
                      Hiring manager, with Owner/Admin able to manage
                      others. */}
                  {/* Preview `.member` — flat divider list: avatar · name/email ·
                      role chip. Active roles read purple, an unverified
                      "Invited" member greys out the avatar + chip. No per-row
                      action button (the preview omits it). */}
                  <div className="members">
                    {teamMembers.map((member) => {
                      const isSelf = member?.email === user?.email;
                      const role = isSelf
                        ? 'Owner'
                        : (String(member?.role || '').trim() || (member?.is_email_verified ? 'Recruiter' : 'Invited'));
                      const invited = role === 'Invited';
                      return (
                        <div key={member.id} className="mb">
                          <div className={`av${invited ? ' inv' : ''}`}>{initialsFor(member.full_name || member.email)}</div>
                          <div className="who">
                            <b>{member.full_name || member.email}</b>
                            <div>{isSelf ? 'you' : (member?.email || '—')}</div>
                          </div>
                          <span className={`chip${invited ? '' : ' purple'}`}>{role}</span>
                        </div>
                      );
                    })}
                    {teamMembers.length === 0 ? (
                      <div className="settings-empty-state">
                        No team members yet.
                      </div>
                    ) : null}
                  </div>

                  {/* Access — preview shows this as its own flat divider-led
                      section ("Access" / "Limit who can join by email
                      domain."). The summary card stays (live-derived, useful)
                      but the section now carries the matching heading. */}
                  <div className="settings-subcard settings-top-gap">
                    <div className="settings-subcard-head">
                      <div>
                        <h3>Access</h3>
                        <p>Limit who can join this workspace by email domain.</p>
                      </div>
                    </div>
                    <div className="row-form">
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
                  </div>
                </SectionPanel>
              </div>

              <div ref={(node) => { sectionRefs.current.jobs = node; }} hidden={activeSection !== "jobs"}>
                <SectionPanel
                  id="jobs"
                  title="Background jobs"
                  subtitle="Recent infrastructure runs — decision approvals, scoring, CV fetch, Workable sync, and graph sync. The agent fleet lives on the Analytics page."
                >
                  <BackgroundJobsPanel />
                </SectionPanel>
              </div>

              <div ref={(node) => { sectionRefs.current.workable = node; }} hidden={activeSection !== "workable"}>
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

                  {/* HANDOFF settings.md — sync mode renamed
                      hybrid|manual → two_way|read_only for clarity in
                      copy. The underlying email_mode + granted_scopes
                      stay the same so existing roles keep working. */}
                  <div className="wk-grid settings-top-gap">
                    <div className={`wk-mode-card ${workableForm.emailMode === 'workable_preferred_fallback_manual' ? 'selected' : ''}`}>
                      <div>
                        <h5>Two-way</h5>
                        <p>Taali invites, scores, and writes candidate activity back as private notes or stage actions. Requires the <code>w_candidates</code> scope.</p>
                      </div>
                      <button
                        type="button"
                        className={`sw ${workableForm.emailMode === 'workable_preferred_fallback_manual' ? 'on' : ''}`}
                        aria-label="Two-way"
                        onClick={() => setWorkableForm((prev) => ({ ...prev, emailMode: 'workable_preferred_fallback_manual' }))}
                      />
                    </div>
                    <div className={`wk-mode-card ${workableForm.emailMode === 'manual_taali' ? 'selected' : ''}`}>
                      <div>
                        <h5>Read-only</h5>
                        <p>Workable stays read-only while Taali manages invites and review locally. No write-backs.</p>
                      </div>
                      <button
                        type="button"
                        className={`sw ${workableForm.emailMode === 'manual_taali' ? 'on' : ''}`}
                        aria-label="Read-only"
                        onClick={() => setWorkableForm((prev) => ({ ...prev, emailMode: 'manual_taali', inviteStageName: '' }))}
                      />
                    </div>
                  </div>

                  <div className="row-form settings-top-gap">
                    <label className="field">
                      <span className="k">Default sync mode</span>
                      <Select
                        value={workableForm.defaultSyncMode}
                        onChange={(event) => setWorkableForm((prev) => ({ ...prev, defaultSyncMode: event.target.value }))}
                      >
                        <option value="full">Full sync</option>
                        <option value="metadata">Metadata sync</option>
                      </Select>
                    </label>
                    <div className="field" style={{ gridColumn: '1 / -1' }}>
                      <span className="k">Sync schedule</span>
                      <div className="v" style={{ display: 'grid', gap: 4, fontSize: 13, lineHeight: 1.5 }}>
                        <div><strong>Jobs metadata</strong> — every 15 minutes (new postings + title/description edits)</div>
                        <div><strong>Starred role candidates</strong> — every 5 minutes</div>
                        <div><strong>Agent-mode role candidates</strong> — every 5 minutes</div>
                        <div><strong>All other roles' candidates</strong> — once nightly at 03:15 UTC</div>
                      </div>
                    </div>
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
                      <Select
                        value={workableForm.workableActorMemberId}
                        onChange={(event) => setWorkableForm((prev) => ({ ...prev, workableActorMemberId: event.target.value }))}
                      >
                        <option value="">{workableMembersLoading ? 'Loading members...' : 'Select member'}</option>
                        {workableMembers.map((member) => {
                          const memberId = String(member?.id || member?.member_id || '').trim();
                          if (!memberId) return null;
                          return <option key={memberId} value={memberId}>{workableMemberLabel(member)}</option>;
                        })}
                      </Select>
                    </label>
                    <label className="field">
                      <span className="k">Default disqualification reason</span>
                      <Select
                        value={workableForm.workableDisqualifyReasonId}
                        onChange={(event) => setWorkableForm((prev) => ({ ...prev, workableDisqualifyReasonId: event.target.value }))}
                      >
                        <option value="">{workableReasonsLoading ? 'Loading reasons...' : 'Optional reason'}</option>
                        {workableReasons.map((reason) => {
                          const reasonId = String(reason?.id || reason?.reason_id || '').trim();
                          if (!reasonId) return null;
                          return <option key={reasonId} value={reasonId}>{workableReasonLabel(reason)}</option>;
                        })}
                      </Select>
                      {!workableReasonsLoading && workableReasons.length === 0 && (
                        <span className="settings-inline-note">
                          No disqualification reasons found in Workable. Add one in Workable&nbsp;Settings &rarr; Recruiting &rarr; Disqualification reasons (and attach a &ldquo;Disqualification message&rdquo; automated action), then refresh this page.
                        </span>
                      )}
                    </label>
                  </div>

                  <div className="settings-toggle-list settings-top-gap">
                    <ToggleCard
                      title="Enable Workable auto-reject"
                      description="Workspace kill-switch for the disqualify pipeline. The per-role score cutoff and HITL toggle live on the role page."
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

              <div ref={(node) => { sectionRefs.current.security = node; }} hidden={activeSection !== "security"}>
                <SectionPanel
                  id="security"
                  title="Security"
                  subtitle="SAML SSO, two-factor authentication, and the audit log entry point."
                >
                  {/* HANDOFF settings.md — Security tab combines the
                      legacy SSO / SAML page with a 2FA toggle and a link
                      to the audit log. */}
                  <div className="settings-subcard">
                    <div className="settings-subcard-head">
                      <div>
                        <h3>SAML SSO</h3>
                        <p>Pick a preset, paste your metadata URL, and toggle enforcement once verified.</p>
                      </div>
                    </div>
                    <div className="row-form">
                      <label className="field">
                        <span className="k">Identity provider</span>
                        <select
                          defaultValue={String(orgData?.saml_provider || '').trim() || 'okta'}
                          onChange={() => { /* preset is informational; metadata URL is the source of truth */ }}
                        >
                          <option value="okta">Okta</option>
                          <option value="azure_ad">Azure AD</option>
                          <option value="google">Google Workspace</option>
                          <option value="onelogin">OneLogin</option>
                          <option value="custom">Custom (any SAML 2.0 IdP)</option>
                        </select>
                      </label>
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
                    <div className="settings-toggle-list settings-top-gap">
                      <ToggleCard
                        title="Enable SAML metadata"
                        description="Store SAML metadata so this workspace can be connected to an IdP."
                        checked={ssoForm.samlEnabled}
                        onChange={(value) => setSsoForm((prev) => ({ ...prev, samlEnabled: value }))}
                      />
                      <ToggleCard
                        title="Enforce SSO"
                        description="Block password login and team invites. Provision access through your identity provider."
                        checked={ssoForm.ssoEnforced}
                        onChange={(value) => setSsoForm((prev) => ({ ...prev, ssoEnforced: value }))}
                      />
                    </div>
                  </div>

                  <div className="settings-subcard settings-top-gap">
                    <div className="settings-subcard-head">
                      <div>
                        <h3>Two-factor authentication</h3>
                        <p>Require recruiters to confirm a second factor on every sign-in. Bypassed for SSO logins.</p>
                      </div>
                    </div>
                    <div className="settings-toggle-list">
                      <ToggleCard
                        title="Require 2FA for password login"
                        description="Time-based codes via authenticator app. Backup codes available from each member's profile."
                        checked={twoFactorRequired}
                        onChange={(value) => setTwoFactorRequired(value)}
                      />
                    </div>
                  </div>

                  <div className="settings-subcard settings-top-gap">
                    <div className="settings-subcard-head">
                      <div>
                        <h3>Audit log</h3>
                        <p>Every recruiter action and every consequential agent decision is recorded.</p>
                      </div>
                    </div>
                    <div className="settings-inline-actions">
                      <a
                        className="btn btn-outline btn-sm"
                        href="/reporting?view=audit"
                      >
                        Open audit log →
                      </a>
                    </div>
                  </div>

                  <div className="settings-save-row">
                    <div className="settings-inline-note">SAML metadata is required when SAML is enabled. 2FA is workspace-wide.</div>
                    <button type="button" className="btn btn-purple btn-sm" onClick={handleSaveSso} disabled={ssoSaving}>
                      {ssoSaving ? 'Saving...' : 'Save security settings'}
                    </button>
                  </div>
                </SectionPanel>
              </div>

              <div ref={(node) => { sectionRefs.current.email = node; }} hidden={activeSection !== "email"}>
                <SectionPanel
                  id="email"
                  title="Email & transcripts"
                  subtitle="Default candidate invite copy and Fireflies transcript ingestion."
                >
                  <div className="settings-subcard">
                    <div className="settings-subcard-head">
                      <div>
                        <h3>Invite template</h3>
                        <p>Default invite body for manual recruiter sends. Supports {'{{candidate_name}}'} and {'{{assessment_link}}'}.</p>
                      </div>
                    </div>
                    <label className="field">
                      <span className="k">Template body</span>
                      <textarea
                        rows={6}
                        value={emailTemplatePreview}
                        onChange={(event) => setEmailTemplatePreview(event.target.value)}
                      />
                    </label>
                  </div>

                  <div className="settings-save-row">
                    <div className="settings-inline-note" />
                    <button type="button" className="btn btn-purple btn-sm" onClick={handleSaveApiKeys} disabled={apiSaving}>
                      {apiSaving ? 'Saving...' : 'Save invite template'}
                    </button>
                  </div>

                  <div className="settings-subcard settings-top-gap">
                    <div className="settings-subcard-head">
                      <div>
                        <h3>Fireflies transcript ingestion</h3>
                        <p>Pull interview transcripts in automatically and attach them to the matching candidate.</p>
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
                        <Select
                          value={firefliesForm.singleAccountMode ? 'single_account' : 'shared'}
                          onChange={(event) => setFirefliesForm((prev) => ({ ...prev, singleAccountMode: event.target.value !== 'shared' }))}
                        >
                          <option value="single_account">Single recruiter-owned account</option>
                          <option value="shared">Shared / multi-account</option>
                        </Select>
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

              <div ref={(node) => { sectionRefs.current.billing = node; }} hidden={activeSection !== "billing"}>
                <SectionPanel
                  id="billing"
                  title="Billing"
                  subtitle="Pay-as-you-go via Stripe. Card on file, monthly cap, and recent invoices."
                >
                  {billingLoading ? (
                    <div className="settings-loading-inline">
                      <Spinner size={18} />
                      Loading billing...
                    </div>
                  ) : (
                    <>
                      {/* HANDOFF settings.md — Plan is hardcoded
                          "Pay-as-you-go" — no plan picker. Card +
                          spend cap + invoices are the three surfaces. */}
                      <div className="settings-billing-summary">
                        <div className="settings-billing-card">
                          <div className="settings-summary-label">Plan</div>
                          <div className="settings-summary-value">Pay-as-you-go</div>
                          <div className="settings-summary-note">
                            Pre-screen at cost · Scoring &amp; assessments at usage-based pricing
                          </div>
                        </div>
                        <div className="settings-billing-card">
                          <div className="settings-summary-label">Current balance</div>
                          <div
                            className="settings-summary-value"
                            style={balanceLow ? { color: 'var(--taali-danger)' } : undefined}
                          >
                            {formatUsd(balanceUsd)}
                          </div>
                          <div className="settings-summary-note">
                            {balanceLow
                              ? 'Balance is running low — top up to keep scoring & assessments running.'
                              : `${breakdownTotalEvents} billable AI ${breakdownTotalEvents === 1 ? 'request' : 'requests'} in the last 30 days.`}
                          </div>
                        </div>
                        <div className="settings-billing-card">
                          <div className="settings-summary-label">Card on file</div>
                          <div className="settings-summary-value">
                            {orgData?.stripe_customer_id ? 'Stripe customer' : 'No card yet'}
                          </div>
                          <div className="settings-summary-note">
                            <a
                              href="https://billing.stripe.com/p/login"
                              target="_blank"
                              rel="noreferrer noopener"
                              style={{ color: 'var(--purple)' }}
                            >
                              Manage in Stripe →
                            </a>
                          </div>
                        </div>
                      </div>

                      <div className="settings-subcard settings-top-gap">
                        <div className="settings-subcard-head">
                          <div>
                            <h3>Monthly spend cap</h3>
                            <p>Hard cap on workspace spend. When the projected month-end total exceeds this number, the agent pauses new invites and a "Spend over budget" notification fires.</p>
                          </div>
                        </div>
                        <div className="row-form">
                          <label className="field">
                            <span className="k">Cap (USD/month)</span>
                            <input
                              type="number"
                              min={0}
                              step="10"
                              value={spendCapForm.usd}
                              onChange={(event) => setSpendCapForm({ usd: event.target.value })}
                              placeholder="500"
                            />
                          </label>
                        </div>
                        <div className="settings-save-row">
                          <div className="settings-inline-note">Leave blank to disable the cap.</div>
                          <button
                            type="button"
                            className="btn btn-purple btn-sm"
                            onClick={handleSaveSpendCap}
                            disabled={spendCapSaving}
                          >
                            {spendCapSaving ? 'Saving...' : 'Save spend cap'}
                          </button>
                        </div>
                      </div>

                      <div className="settings-credit-packs settings-top-gap">
                        {creditPacks.length === 0 ? (
                          <div className="settings-summary-note" style={{ padding: '8px 0' }}>
                            No top-up packs available — contact support.
                          </div>
                        ) : creditPacks.map((pack) => (
                          <button
                            key={pack.pack_id}
                            type="button"
                            className="settings-credit-pack"
                            onClick={() => handleAddCredits(pack.pack_id)}
                            disabled={checkoutLoading}
                          >
                            <span>
                              {pack.label}
                              {pack.bonus_pct ? ` · +${pack.bonus_pct}% bonus` : ''}
                            </span>
                            <span className="settings-credit-pack-meta">
                              {checkoutLoading ? <Spinner size={14} /> : <CreditCard size={14} />}
                              ${pack.price_usd}
                            </span>
                          </button>
                        ))}
                      </div>

                      <div className="settings-usage-table">
                        <div className="settings-usage-head">
                          <h3>Recent invoices</h3>
                        </div>
                        <table>
                          <thead>
                            <tr>
                              <th>Date</th>
                              <th>Product</th>
                              <th>Cost</th>
                            </tr>
                          </thead>
                          <tbody>
                            {billingEvents.length === 0 ? (
                              <tr>
                                <td colSpan={3} className="empty">
                                  No usage yet. Activity from pre-screening, scoring, and assessments will appear here.
                                </td>
                              </tr>
                            ) : billingEvents.map((row) => {
                              const date = row.created_at ? new Date(row.created_at).toLocaleString() : '—';
                              return (
                                <tr key={row.id}>
                                  <td>{date}</td>
                                  <td>{FEATURE_LABELS[row.feature] || row.feature}</td>
                                  <td>{formatUsd6(row.credits_charged_usd)}</td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )}
                </SectionPanel>
              </div>

              <div ref={(node) => { sectionRefs.current.usage = node; }} hidden={activeSection !== "usage"}>
                <SectionPanel
                  id="usage"
                  title="Usage"
                  subtitle="Per-feature Claude spend, daily breakdowns, and reconciliation against Anthropic billing."
                >
                  <UsagePanel />
                </SectionPanel>
              </div>

              <div ref={(node) => { sectionRefs.current.notifications = node; }} hidden={activeSection !== "notifications"}>
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
                    <ToggleCard
                      title="Spend over budget"
                      description="Fires when projected month-end spend exceeds the workspace cap. Pauses new agent invites until cleared."
                      checked={notificationPreferencesForm.spend_over_budget}
                      onChange={(value) => setNotificationPreferencesForm((prev) => ({ ...prev, spend_over_budget: value }))}
                    />
                    <ToggleCard
                      title="Agent paused"
                      description="Fires when the autonomous agent stops on its own — bad sample, recruiter intervention, or ratelimit."
                      checked={notificationPreferencesForm.agent_paused}
                      onChange={(value) => setNotificationPreferencesForm((prev) => ({ ...prev, agent_paused: value }))}
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
