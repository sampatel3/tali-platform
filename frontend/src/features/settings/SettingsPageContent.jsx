import React, { useState, useEffect, useRef } from 'react';
import { AlertTriangle, CheckCircle, CreditCard } from 'lucide-react';
import { useLocation, useNavigate } from 'react-router-dom';

import {
  Badge,
  Button,
  Card,
  Input,
  PageContainer,
  PageHeader,
  Select,
  Spinner,
  TabBar,
  TableShell,
  Textarea,
  Panel,
  Sheet,
} from '../../shared/ui/TaaliPrimitives';
import { CardSkeleton } from '../../shared/ui/Skeletons';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { organizations as orgsApi, billing as billingApi, team as teamApi } from '../../shared/api';
import { aedToUsd, formatAed } from '../../lib/currency';
import { GlobalThemeToggle } from '../../shared/ui/GlobalThemeToggle';

const WORKABLE_SCOPE_OPTIONS = [
  { id: 'r_jobs', label: 'r_jobs', description: 'Read jobs and roles from Workable.' },
  { id: 'r_candidates', label: 'r_candidates', description: 'Read candidate profiles and stages.' },
  { id: 'w_candidates', label: 'w_candidates', description: 'Write candidate stage activity for invites, disqualify actions, and notes.' },
];

const WORKABLE_REQUIRED_SCOPES = ['r_jobs', 'r_candidates'];
const DEFAULT_INVITE_TEMPLATE = 'Hi {{candidate_name}}, your TAALI assessment is ready: {{assessment_link}}';

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

const PreferencesSettingsTab = ({
  defaultAssessmentMinutes,
  setDefaultAssessmentMinutes,
  emailTemplatePreview,
  setEmailTemplatePreview,
  preferencesSavedAt,
  preferencesSaving,
  handleSavePreferences,
}) => (
  <div className="space-y-6">
    <Panel className="p-5">
      <h3 className="mb-3 text-lg font-bold text-[var(--taali-text)]">Display Preferences</h3>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-[var(--taali-text)]">Theme</p>
          <p className="mt-1 text-xs text-[var(--taali-muted)]">
            Uses the same light and dark switch as the landing page and recruiter app header.
          </p>
        </div>
        <GlobalThemeToggle className="shrink-0" />
      </div>
    </Panel>

    <Panel className="p-5">
      <h3 className="mb-3 text-lg font-bold text-[var(--taali-text)]">Assessment Defaults</h3>
      <label className="block">
        <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Default assessment duration (minutes)</span>
        <Input
          type="number"
          min={15}
          max={180}
          value={defaultAssessmentMinutes}
          onChange={(event) => {
            const raw = Number(event.target.value || 30);
            const clamped = Math.max(15, Math.min(180, raw));
            setDefaultAssessmentMinutes(Number.isFinite(clamped) ? clamped : 30);
          }}
        />
      </label>
      <p className="mt-2 text-xs text-[var(--taali-muted)]">
        Applied to newly created assessments.
      </p>
    </Panel>

    <Panel className="p-5">
      <h3 className="mb-3 text-lg font-bold text-[var(--taali-text)]">Invite Email Template Preview</h3>
      <label className="block">
        <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Template body</span>
        <Textarea
          value={emailTemplatePreview}
          rows={5}
          onChange={(event) => setEmailTemplatePreview(event.target.value)}
        />
      </label>
      <p className="mt-2 text-xs text-[var(--taali-muted)]">
        Supports placeholders like {'{{candidate_name}}'} and {'{{assessment_link}}'}.
      </p>
    </Panel>

    <Panel className="p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="font-mono text-xs text-[var(--taali-muted)]">
          {preferencesSavedAt
            ? `Last saved ${new Date(preferencesSavedAt).toLocaleTimeString()}`
            : 'Save to apply workspace preferences'}
        </div>
        <Button
          type="button"
          variant="primary"
          disabled={preferencesSaving}
          onClick={handleSavePreferences}
        >
          {preferencesSaving ? 'Saving…' : 'Save preferences'}
        </Button>
      </div>
    </Panel>
  </div>
);

export const SettingsPage = ({ onNavigate, NavComponent = null, ConnectWorkableButton }) => {
  const { user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const settingsPathSegment = location.pathname.replace(/^\/settings\/?/, '').split('/')[0];
  const routeSettingsTab = ['workable', 'billing', 'team', 'enterprise', 'preferences'].includes(settingsPathSegment)
    ? settingsPathSegment
    : 'billing';
  const [orgData, setOrgData] = useState(null);
  const [orgLoading, setOrgLoading] = useState(true);
  const [billingUsage, setBillingUsage] = useState(null);
  const [billingCosts, setBillingCosts] = useState(null);
  const [billingCredits, setBillingCredits] = useState(null);
  const [checkoutLoading, setCheckoutLoading] = useState(false);
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
  const workableSyncPollRef = useRef(null);
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
  const [teamMembers, setTeamMembers] = useState([]);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteName, setInviteName] = useState('');
  const [inviteLoading, setInviteLoading] = useState(false);
  const [defaultAssessmentMinutes, setDefaultAssessmentMinutes] = useState(30);
  const [emailTemplatePreview, setEmailTemplatePreview] = useState(DEFAULT_INVITE_TEMPLATE);
  const [preferencesSaving, setPreferencesSaving] = useState(false);
  const [preferencesSavedAt, setPreferencesSavedAt] = useState(null);
  const [enterpriseSaving, setEnterpriseSaving] = useState(false);
  const [enterpriseForm, setEnterpriseForm] = useState({
    allowedEmailDomains: '',
    ssoEnforced: false,
    samlEnabled: false,
    samlMetadataUrl: '',
    candidateFeedbackEnabled: true,
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
  const [firefliesSaving, setFirefliesSaving] = useState(false);
  const [firefliesForm, setFirefliesForm] = useState({
    apiKey: '',
    webhookSecret: '',
    ownerEmail: '',
    inviteEmail: '',
    singleAccountMode: true,
  });
  const [firefliesHasApiKey, setFirefliesHasApiKey] = useState(false);
  const [firefliesWebhookSecretConfigured, setFirefliesWebhookSecretConfigured] = useState(false);
  const [firefliesClearApiKey, setFirefliesClearApiKey] = useState(false);
  const [firefliesClearWebhookSecret, setFirefliesClearWebhookSecret] = useState(false);
  const [clearWorkableModalOpen, setClearWorkableModalOpen] = useState(false);
  const [clearWorkableLoading, setClearWorkableLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const fetchOrg = async () => {
      try {
        const res = await orgsApi.get();
        if (!cancelled) setOrgData(res.data);
      } catch (err) {
        console.warn('Failed to fetch org data:', err.message);
      } finally {
        if (!cancelled) setOrgLoading(false);
      }
    };
    fetchOrg();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (routeSettingsTab !== 'billing') return;
    let cancelled = false;
    const fetchUsage = async () => {
      try {
        const [usageRes, costsRes, creditsRes] = await Promise.all([billingApi.usage(), billingApi.costs(), billingApi.credits()]);
        if (!cancelled) {
          setBillingUsage(usageRes.data);
          setBillingCosts(costsRes.data);
          setBillingCredits(creditsRes.data);
        }
      } catch (err) {
        console.warn('Failed to fetch billing usage:', err.message);
      }
    };
    fetchUsage();
    return () => {
      cancelled = true;
    };
  }, [routeSettingsTab]);

  useEffect(() => {
    if (routeSettingsTab !== 'team') return;
    let cancelled = false;
    const fetchTeam = async () => {
      try {
        const res = await teamApi.list();
        if (!cancelled) setTeamMembers(res.data || []);
      } catch (err) {
        console.warn('Failed to fetch team:', err.message);
      }
    };
    fetchTeam();
    return () => {
      cancelled = true;
    };
  }, [routeSettingsTab]);

  useEffect(() => {
    if (!orgData) return;
    const domains = Array.isArray(orgData.allowed_email_domains) ? orgData.allowed_email_domains.join(', ') : '';
    const cfg = orgData.workable_config || {};
    const grantedScopes = Array.isArray(cfg.granted_scopes) ? cfg.granted_scopes : [];
    const firefliesCfg = orgData.fireflies_config || {};
    setEnterpriseForm({
      allowedEmailDomains: domains,
      ssoEnforced: Boolean(orgData.sso_enforced),
      samlEnabled: Boolean(orgData.saml_enabled),
      samlMetadataUrl: orgData.saml_metadata_url || '',
      candidateFeedbackEnabled: orgData.candidate_feedback_enabled !== false,
    });
    setWorkableForm({
      emailMode: cfg.email_mode || 'manual_taali',
      defaultSyncMode: cfg.default_sync_mode || 'full',
      syncIntervalMinutes: Number(cfg.sync_interval_minutes || 30),
      inviteStageName: cfg.invite_stage_name || '',
      autoRejectEnabled: Boolean(cfg.auto_reject_enabled),
      autoRejectThreshold100: cfg.auto_reject_threshold_100 ?? '',
      workableActorMemberId: cfg.workable_actor_member_id || '',
      workableDisqualifyReasonId: cfg.workable_disqualify_reason_id || '',
      autoRejectNoteTemplate: cfg.auto_reject_note_template || '',
    });
    setWorkableSelectedScopes(
      grantedScopes.length > 0
        ? buildWorkableScopeSelection(grantedScopes)
        : {
          r_jobs: true,
          r_candidates: true,
          w_candidates: (cfg.email_mode || 'manual_taali') === 'workable_preferred_fallback_manual' || Boolean(cfg.auto_reject_enabled),
        }
    );
    setWorkableTokenForm((prev) => ({
      ...prev,
      subdomain: prev.subdomain || orgData.workable_subdomain || '',
    }));
    setFirefliesForm({
      apiKey: '',
      webhookSecret: '',
      ownerEmail: firefliesCfg.owner_email || '',
      inviteEmail: firefliesCfg.invite_email || '',
      singleAccountMode: firefliesCfg.single_account_mode !== false,
    });
    setFirefliesHasApiKey(Boolean(firefliesCfg.has_api_key));
    setFirefliesWebhookSecretConfigured(Boolean(firefliesCfg.webhook_secret_configured));
    setFirefliesClearApiKey(false);
    setFirefliesClearWebhookSecret(false);
  }, [orgData]);

  useEffect(() => {
    if (!orgData) return;
    const configuredMinutes = Number(orgData.default_assessment_duration_minutes ?? 30);
    const clampedMinutes = Number.isFinite(configuredMinutes)
      ? Math.max(15, Math.min(180, configuredMinutes))
      : 30;
    setDefaultAssessmentMinutes(clampedMinutes);
    setEmailTemplatePreview(
      String(orgData.invite_email_template || '').trim() || DEFAULT_INVITE_TEMPLATE
    );
  }, [
    orgData?.id,
    orgData?.default_assessment_duration_minutes,
    orgData?.invite_email_template,
  ]);

  const handleAddCredits = async (packId) => {
    const base = `${window.location.origin}/settings`;
    setCheckoutLoading(true);
    try {
      const res = await billingApi.createCheckoutSession({
        success_url: `${base}?payment=success`,
        cancel_url: base,
        pack_id: packId,
      });
      if (res.data?.url) window.location.href = res.data.url;
      else setCheckoutLoading(false);
    } catch (err) {
      console.warn('Checkout failed:', err?.response?.data?.detail || err.message);
      setCheckoutLoading(false);
    }
  };

  const handleClearWorkableData = async () => {
    setClearWorkableLoading(true);
    try {
      const res = await orgsApi.clearWorkableData();
      const data = res.data || {};
      showToast(
        `Removed ${data.roles_soft_deleted ?? 0} roles, ${data.applications_soft_deleted ?? 0} applications, ${data.candidates_soft_deleted ?? 0} candidates.`,
        'success'
      );
      setClearWorkableModalOpen(false);
    } catch (err) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail ?? err?.message;
      const message = status === 404
        ? 'Data removal is temporarily unavailable. Contact support if you need to reset your Workable data.'
        : normalizeWorkableError(detail || 'Failed to clear Workable data');
      showToast(message, 'error');
    } finally {
      setClearWorkableLoading(false);
    }
  };

  const fetchWorkableSyncStatus = async (runIdOverride = null) => {
    try {
      const runId = runIdOverride != null ? runIdOverride : workableActiveRunId;
      const res = await orgsApi.getWorkableSyncStatus(runId);
      const data = res.data || {};
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
  };

  const loadWorkableSyncJobs = async () => {
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
      const available = jobs
        .map((job) => String(job?.shortcode || job?.id || '').trim())
        .filter(Boolean);
      setWorkableSelectedJobShortcodes((prev) => {
        const kept = prev.filter((id) => available.includes(id));
        return kept.length > 0 ? kept : available;
      });
    } catch (err) {
      setWorkableJobsError(err?.response?.data?.detail || 'Failed to load Workable roles.');
    } finally {
      setWorkableJobsLoading(false);
    }
  };

  const loadWorkableLookups = async () => {
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
    } catch (err) {
      console.warn('Failed to load Workable configuration data:', err?.message || err);
      setWorkableMembers([]);
      setWorkableReasons([]);
      setWorkableStages([]);
    } finally {
      setWorkableMembersLoading(false);
      setWorkableReasonsLoading(false);
      setWorkableStagesLoading(false);
    }
  };

  useEffect(() => {
    if (routeSettingsTab !== 'workable') return;
    fetchWorkableSyncStatus();
    loadWorkableSyncJobs();
    loadWorkableLookups();
  }, [routeSettingsTab, orgData?.workable_connected]);

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
        const s = data.workable_last_sync_summary || {};
        const modeLabel = String(data.mode || s.mode || workableForm.defaultSyncMode || 'full').toLowerCase() === 'metadata'
          ? 'Metadata sync'
          : 'Full sync';
        const msg = Array.isArray(s.errors) && s.errors.length > 0
          ? `${s.errors[0]}`
          : `${modeLabel} finished. Roles: ${s.jobs_processed ?? s.jobs_seen ?? 0}/${s.jobs_total ?? s.jobs_seen ?? 0}, Candidates: ${s.candidates_seen ?? 0} seen (${s.candidates_upserted ?? 0} upserted).`;
        showToast(msg, (data.workable_last_sync_status || '').toLowerCase() === 'success' ? 'success' : 'info');
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
  }, [workableSyncInProgress, workableActiveRunId]);

  const handleCancelWorkableSync = async () => {
    setWorkableSyncCancelLoading(true);
    try {
      await orgsApi.cancelWorkableSync(workableActiveRunId);
      showToast('Cancel requested. Sync will stop shortly.', 'info');
      fetchWorkableSyncStatus(workableActiveRunId);
    } catch (err) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail ?? err?.message;
      const message = status === 404
        ? 'Sync cancellation is temporarily unavailable. The sync will complete on its own — check back shortly.'
        : normalizeWorkableError(detail || 'Failed to cancel sync');
      showToast(message, 'error');
    } finally {
      setWorkableSyncCancelLoading(false);
    }
  };

  const handleSyncWorkable = async () => {
    setWorkableSyncLoading(true);
    try {
      const availableIdentifiers = (workableJobs || [])
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
        fetchWorkableSyncStatus(runId);
        return;
      }
      const selectedCount = selectedIdentifiers.length;
      const modeLabel = syncMode === 'metadata' ? 'Metadata sync' : 'Full sync';
      showToast(
        `${modeLabel} is running for ${selectedCount} role${selectedCount === 1 ? '' : 's'} in the background.`,
        'info'
      );
      fetchWorkableSyncStatus(runId);
    } catch (err) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail ?? err?.message ?? String(err);
      if (status === 409) {
        showToast("A sync is already running in the background. We'll notify you when it's done.", 'info');
        fetchWorkableSyncStatus();
      } else {
        setWorkableSyncInProgress(false);
        console.error('Workable sync failed:', err?.response?.data ?? err);
        showToast(detail || 'Workable sync failed', 'error');
      }
    } finally {
      setWorkableSyncLoading(false);
    }
  };

  const toggleWorkableSyncRole = (identifier) => {
    if (!identifier) return;
    setWorkableSelectedJobShortcodes((prev) => (
      prev.includes(identifier)
        ? prev.filter((id) => id !== identifier)
        : [...prev, identifier]
    ));
  };

  const handleSaveWorkable = async () => {
    const emailMode = workableForm.emailMode || 'manual_taali';
    const defaultSyncMode = workableForm.defaultSyncMode || 'full';
    const inviteStageName = (workableForm.inviteStageName || '').trim();
    const autoRejectEnabled = Boolean(workableForm.autoRejectEnabled);
    const hasWriteScope = selectedWorkableScopes.includes('w_candidates');
    const workableActorMemberId = String(workableForm.workableActorMemberId || '').trim();
    const workableDisqualifyReasonId = String(workableForm.workableDisqualifyReasonId || '').trim();
    const autoRejectNoteTemplate = String(workableForm.autoRejectNoteTemplate || '').trim();
    const parsedThreshold = workableForm.autoRejectThreshold100 === ''
      ? null
      : Number(workableForm.autoRejectThreshold100);
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
      setOrgData(res.data);
      showToast('Workable sync settings saved.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to save Workable settings', 'error');
    } finally {
      setWorkableSaving(false);
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
      const res = await orgsApi.update({
        fireflies_config: firefliesPayload,
      });
      setOrgData(res.data);
      setFirefliesForm((prev) => ({
        ...prev,
        apiKey: '',
        webhookSecret: '',
      }));
      setFirefliesClearApiKey(false);
      setFirefliesClearWebhookSecret(false);
      showToast('Fireflies settings saved.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to save Fireflies settings', 'error');
    } finally {
      setFirefliesSaving(false);
    }
  };

  const toggleWorkableScope = (scopeId) => {
    setWorkableSelectedScopes((prev) => ({
      ...prev,
      [scopeId]: !prev[scopeId],
    }));
  };

  const openWorkableDrawer = () => {
    setWorkableConnectError('');
    setWorkableDrawerOpen(true);
  };

  const closeWorkableDrawer = () => {
    setWorkableConnectError('');
    setWorkableDrawerOpen(false);
  };

  const selectedWorkableScopes = WORKABLE_SCOPE_OPTIONS
    .filter((scope) => workableSelectedScopes[scope.id])
    .map((scope) => scope.id);

  const missingRequiredWorkableScopes = WORKABLE_REQUIRED_SCOPES.filter((scope) => !selectedWorkableScopes.includes(scope));

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
      setWorkableForm((prev) => ({
        ...prev,
        emailMode: hasWriteScope ? 'workable_preferred_fallback_manual' : 'manual_taali',
      }));
      const res = await orgsApi.getWorkableAuthorizeUrl({ scopes: selectedWorkableScopes });
      if (res.data?.url) {
        window.location.href = res.data.url;
        return;
      }
      setWorkableConnectError('Could not get Workable authorization URL.');
    } catch (err) {
      setWorkableConnectError(normalizeWorkableError(err?.response?.data?.detail || err.message));
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
    const hasWriteScope = selectedWorkableScopes.includes('w_candidates');
    const readOnly = !hasWriteScope;
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
        workable_subdomain: res.data?.subdomain || subdomain,
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
      closeWorkableDrawer();
      showToast(
        readOnly
          ? 'Workable connected in read-only mode. Sync is enabled, but invite/reject/reopen actions stay local to TAALI.'
          : 'Workable connected with candidate write-back. Configure the actor member to enable invites, rejects, and reopens from TAALI.',
        'success'
      );
    } catch (err) {
      setWorkableConnectError(normalizeWorkableError(err?.response?.data?.detail || err.message));
    } finally {
      setWorkableTokenSaving(false);
    }
  };

  const handleInvite = async (e) => {
    e.preventDefault();
    if (!inviteEmail || !inviteName) return;
    setInviteLoading(true);
    try {
      const res = await teamApi.invite({ email: inviteEmail, full_name: inviteName });
      setTeamMembers((prev) => [...prev, res.data]);
      setInviteEmail('');
      setInviteName('');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to invite team member', 'error');
    } finally {
      setInviteLoading(false);
    }
  };

  const handleSaveEnterprise = async () => {
    setEnterpriseSaving(true);
    const domains = enterpriseForm.allowedEmailDomains
      .split(',')
      .map((domain) => domain.trim())
      .filter(Boolean);
    try {
      const res = await orgsApi.update({
        allowed_email_domains: domains,
        sso_enforced: enterpriseForm.ssoEnforced,
        saml_enabled: enterpriseForm.samlEnabled,
        saml_metadata_url: enterpriseForm.samlMetadataUrl || null,
        candidate_feedback_enabled: enterpriseForm.candidateFeedbackEnabled,
      });
      setOrgData(res.data);
      showToast('Enterprise access controls updated.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to save enterprise settings', 'error');
    } finally {
      setEnterpriseSaving(false);
    }
  };

  const handleSavePreferences = async () => {
    const clampedMinutes = Math.max(15, Math.min(180, Number(defaultAssessmentMinutes || 30)));
    const trimmedTemplate = String(emailTemplatePreview || '').trim();
    const payload = {
      default_assessment_duration_minutes: clampedMinutes,
      invite_email_template: trimmedTemplate || null,
    };

    setPreferencesSaving(true);
    try {
      const res = await orgsApi.update(payload);
      const updated = res?.data || {};
      setOrgData((prev) => ({ ...(prev || {}), ...updated }));
      setDefaultAssessmentMinutes(
        Math.max(15, Math.min(180, Number(updated.default_assessment_duration_minutes ?? clampedMinutes)))
      );
      setEmailTemplatePreview(
        String(updated.invite_email_template || '').trim() || DEFAULT_INVITE_TEMPLATE
      );
      setPreferencesSavedAt(new Date().toISOString());
      showToast('Preferences saved.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to save preferences', 'error');
    } finally {
      setPreferencesSaving(false);
    }
  };

  const orgName = orgData?.name || user?.organization?.name || '--';
  const adminEmail = user?.email || '--';
  const workableConnected = orgData?.workable_connected ?? false;
  const connectedSince = orgData?.workable_connected_at
    ? new Date(orgData.workable_connected_at).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
    : '—';
  const lastSyncAt = orgData?.workable_last_sync_at
    ? new Date(orgData.workable_last_sync_at).toLocaleString()
    : 'Never';
  const lastSyncStatus = orgData?.workable_last_sync_status || 'not_started';
  const billingPlan = orgData?.plan || 'Pay-Per-Use';
  const workableCallbackUrl = `${window.location.origin}/settings/workable/callback`;
  const isEnterprisePlan = String(orgData?.plan || '').toLowerCase().includes('enterprise');
  const showWorkableTab = true;
  const settingsTabs = [
    ...(showWorkableTab ? [{ id: 'workable', label: 'Workable', panelId: 'settings-workable' }] : []),
    { id: 'billing', label: 'Billing', panelId: 'settings-billing' },
    { id: 'team', label: 'Team', panelId: 'settings-team' },
    ...(isEnterprisePlan ? [{ id: 'enterprise', label: 'Enterprise', panelId: 'settings-enterprise' }] : []),
    { id: 'preferences', label: 'Preferences', panelId: 'settings-preferences' },
  ];
  const activeSettingsTab = settingsTabs.some((tab) => tab.id === routeSettingsTab)
    ? routeSettingsTab
    : settingsTabs[0].id;
  const setSettingsTab = (tab) => {
    if (!tab) return;
    navigate(`/settings/${tab}`);
  };
  const workableScopes = selectedWorkableScopes.join(' ') || 'none';
  const workableWriteScopeEnabled = selectedWorkableScopes.includes('w_candidates');
  const workableWriteActionsEnabled = workableWriteScopeEnabled;
  const workableSyncJobs = Array.isArray(workableJobs) ? workableJobs : [];
  const normalizedWorkableJobSearch = (workableJobSearch || '').trim().toLowerCase();
  const filteredWorkableSyncJobs = workableSyncJobs.filter((job) => {
    if (!normalizedWorkableJobSearch) return true;
    const identifier = String(job?.shortcode || job?.id || '').toLowerCase();
    const title = String(job?.title || '').toLowerCase();
    return identifier.includes(normalizedWorkableJobSearch) || title.includes(normalizedWorkableJobSearch);
  });
  const selectedRoleCountForSync = workableSelectedJobShortcodes.length;
  const totalRoleCountForSync = workableSyncJobs.length;
  const selectedRoleSetForSync = new Set(workableSelectedJobShortcodes);
  const creditsBalance = Number(billingCredits?.credits_balance ?? orgData?.credits_balance ?? 0);
  const packCatalog = billingCredits?.packs || {
    starter_5: { label: 'Starter (5 credits)', credits: 5 },
    growth_10: { label: 'Growth (10 credits)', credits: 10 },
    scale_20: { label: 'Scale (20 credits)', credits: 20 },
  };
  const usageHistory = billingUsage?.usage ?? [];
  const monthlyAssessments = usageHistory.length;
  const monthlyCost = Number(billingUsage?.total_cost ?? 0);
  const thresholdConfig = billingCosts?.thresholds || {};
  const thresholdStatus = billingCosts?.threshold_status || {};
  const spendSummary = billingCosts?.summary || {};
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

  const WorkableSettingsTab = () => (
    <div>
      <Panel className={`mb-5 flex flex-wrap items-center justify-between gap-4 p-4 ${workableConnected ? 'bg-[var(--taali-success-soft)]' : 'bg-[var(--taali-warning-soft)]'}`}>
        <div className="flex items-center gap-4">
          {workableConnected ? <CheckCircle size={24} className="text-[var(--taali-success)]" /> : <AlertTriangle size={24} className="text-[var(--taali-warning)]" />}
          <div>
            <div className="font-bold text-base text-[var(--taali-text)]">Status: {workableConnected ? 'Connected' : 'Not Connected'}</div>
            <div className="text-sm text-[var(--taali-muted)]">
              {workableConnected ? 'Workable integration is active' : 'Connect your Workable account to sync candidates'}
            </div>
          </div>
        </div>
        {!workableConnected ? (
          ConnectWorkableButton ? (
            <ConnectWorkableButton onClick={openWorkableDrawer} />
          ) : (
            <Button variant="primary" onClick={openWorkableDrawer}>
              Connect Workable
            </Button>
          )
        ) : null}
      </Panel>

      <Panel className="space-y-4 p-4">
        <div>
          <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Organization</div>
          <div className="font-bold text-[var(--taali-text)]">{orgName}</div>
        </div>
        <div>
          <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Admin Email</div>
          <div className="font-mono text-[var(--taali-text)]">{adminEmail}</div>
        </div>
        <div>
          <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Connected Since</div>
          <div className="font-mono text-[var(--taali-text)]">{workableConnected ? connectedSince : '—'}</div>
        </div>
        <div>
          <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Active Claude model</div>
          <div className="font-mono text-[var(--taali-text)]">
            {`Assessment model: ${orgData?.active_claude_model || '—'} · Scoring model: ${orgData?.active_claude_scoring_model || orgData?.active_claude_model || '—'}`}
          </div>
        </div>
        <div>
          <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Last Sync</div>
          <div className="font-mono text-[var(--taali-text)]">{lastSyncAt} ({lastSyncStatus})</div>
          {Array.isArray(orgData?.workable_last_sync_summary?.errors) && orgData.workable_last_sync_summary.errors.length > 0 && (
            <div className="mt-1 text-sm text-[var(--taali-warning)] font-mono">
              {orgData.workable_last_sync_summary.errors[0]}
            </div>
          )}
        </div>
        <Card className="bg-[var(--taali-surface-subtle)] p-4 text-sm text-[var(--taali-text)]">
          <div className="font-semibold mb-1">What happens when you sync</div>
          <ul className="list-disc list-inside space-y-0.5">
            <li>Open jobs from Workable are imported as roles; job specs are saved as attachments.</li>
            <li>All candidates for each job are fetched (no 50-candidate limit).</li>
            <li>Full sync enriches candidate profiles, fetches CVs when available, computes CV fit and requirements fit, and writes pre-screen ranking caches.</li>
            <li>TAALI can write invite, reject, and reopen actions back to Workable when `w_candidates` scope and an actor member are configured.</li>
            <li>Bulk reject in TAALI fans out one candidate at a time because Workable does not expose a native bulk reject API.</li>
          </ul>
          <p className="mt-2 text-xs text-[var(--taali-muted)]">
            Use metadata sync only for lightweight troubleshooting. Production recruiter workflows should stay on full sync.
          </p>
        </Card>
        {workableSyncInProgress && (
          <Panel className="mt-3 flex items-center gap-3 border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] p-4">
            <Spinner size={24} className="flex-shrink-0" />
            <div className="min-w-0 flex-1">
              <div className="font-semibold text-[var(--taali-text)]">
                {workableSyncLoading ? 'Starting…' : 'Running in background'}
              </div>
              <div className="text-sm text-[var(--taali-text)]">
                Sync is running in the background. We’ll notify you when it’s done. You can leave this page.
              </div>
              {orgData?.workable_sync_progress && (orgData.workable_sync_progress.current_step || orgData.workable_sync_progress.jobs_total != null || orgData.workable_sync_progress.candidates_seen != null) ? (
                <>
                  {(orgData.workable_sync_progress.current_step || orgData.workable_sync_progress.last_request) && (
                    <div className="mt-2 font-mono text-xs text-[var(--taali-text)]">
                      {orgData.workable_sync_progress.current_step && (
                        <span>Step: {orgData.workable_sync_progress.current_step.replace(/_/g, ' ')}</span>
                      )}
                      {orgData.workable_sync_progress.current_job_shortcode && (
                        <span> · Job {orgData.workable_sync_progress.current_job_shortcode}</span>
                      )}
                      {orgData.workable_sync_progress.current_candidate_index && (
                        <span> · Candidate {orgData.workable_sync_progress.current_candidate_index}</span>
                      )}
                      {orgData.workable_sync_progress.last_request && (
                        <span className="block mt-0.5 text-[var(--taali-muted)]">Request: {orgData.workable_sync_progress.last_request}</span>
                      )}
                    </div>
                  )}
                  <div className="mt-2 font-mono text-xs text-[var(--taali-text)]">
                    {orgData.workable_sync_progress.jobs_processed ?? 0}/{orgData.workable_sync_progress.jobs_total ?? 0} roles processed ({orgData.workable_sync_progress.jobs_upserted ?? 0} new) · {orgData.workable_sync_progress.candidates_seen ?? 0} candidates seen ({orgData.workable_sync_progress.candidates_upserted ?? 0} upserted)
                  </div>
                </>
              ) : (
                <div className="mt-2 font-mono text-xs text-[var(--taali-muted)]">Starting sync…</div>
              )}
              <div className="mt-3">
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  disabled={workableSyncCancelLoading}
                  onClick={handleCancelWorkableSync}
                >
                  {workableSyncCancelLoading ? 'Stopping…' : 'Stop sync'}
                </Button>
              </div>
            </div>
          </Panel>
        )}
        <hr className="border-[var(--taali-border)]" />
        <div>
          <div className="font-bold mb-3 text-[var(--taali-text)]">Sync + Invite Settings</div>
          <div className="grid md:grid-cols-2 gap-3">
            <label className="block">
              <span className="font-mono text-xs text-[var(--taali-muted)] mb-1 block">Default sync mode</span>
              <Select
                className="w-full"
                value={workableForm.defaultSyncMode}
                onChange={(e) => setWorkableForm((prev) => ({ ...prev, defaultSyncMode: e.target.value }))}
              >
                <option value="full">Full sync</option>
                <option value="metadata">Metadata sync</option>
              </Select>
              <span className="font-mono text-xs text-[var(--taali-muted)] mt-1 block">
                Full sync imports candidates, fetches CVs when possible, and computes Workable-first pre-screen scores.
              </span>
            </label>
            <label className="block">
              <span className="font-mono text-xs text-[var(--taali-muted)] mb-1 block">Email mode</span>
              <Select
                className="w-full"
                value={workableForm.emailMode}
                onChange={(e) => {
                  const nextMode = e.target.value;
                  setWorkableForm((prev) => ({
                    ...prev,
                    emailMode: nextMode,
                    inviteStageName: nextMode === 'workable_preferred_fallback_manual' ? prev.inviteStageName : '',
                  }));
                }}
              >
                <option value="manual_taali">Manual</option>
                <option value="workable_preferred_fallback_manual">Automated via Workable</option>
              </Select>
              <span className="font-mono text-xs text-[var(--taali-muted)] mt-1 block">
                Write-back requires `w_candidates`. Automated invite mode also requires an exact Workable stage name.
              </span>
            </label>
            <label className="block">
              <span className="font-mono text-xs text-[var(--taali-muted)] mb-1 block">Sync interval (minutes)</span>
              <Input
                type="number"
                min={5}
                max={1440}
                className="w-full"
                value={workableForm.syncIntervalMinutes}
                onChange={(e) => setWorkableForm((prev) => ({ ...prev, syncIntervalMinutes: e.target.value }))}
              />
            </label>
            {workableForm.emailMode === 'workable_preferred_fallback_manual' ? (
              <label className="block md:col-span-2">
                <span className="font-mono text-xs text-[var(--taali-muted)] mb-1 block">Invite stage name</span>
                <Input
                  type="text"
                  list="workable-stage-options"
                  className="w-full"
                  placeholder="Enter exact Workable stage name"
                  value={workableForm.inviteStageName}
                  onChange={(e) => setWorkableForm((prev) => ({ ...prev, inviteStageName: e.target.value }))}
                />
                <span className="font-mono text-xs text-[var(--taali-muted)] mt-1 block">
                  Keep this blank in manual mode. For automated mode, enter the exact stage already configured in Workable.
                  {workableStagesLoading ? ' Loading stage suggestions…' : ''}
                </span>
              </label>
            ) : null}
            <label className="block md:col-span-2">
              <span className="mb-1 flex items-center gap-2 font-mono text-xs text-[var(--taali-muted)]">
                <input
                  type="checkbox"
                  checked={Boolean(workableForm.autoRejectEnabled)}
                  onChange={(e) => setWorkableForm((prev) => ({ ...prev, autoRejectEnabled: e.target.checked }))}
                />
                Enable Workable auto-reject
              </span>
              <span className="font-mono text-xs text-[var(--taali-muted)] block">
                Auto-reject uses the pre-screen score only. Candidates below the threshold are disqualified in Workable during full sync or re-score.
              </span>
            </label>
            {!workableWriteActionsEnabled ? (
              <p className="md:col-span-2 font-mono text-xs text-[var(--taali-muted)]">
                Reconnect Workable with `w_candidates` scope to enable automated invites plus TAALI reject and reopen write-back.
              </p>
            ) : null}
            {workableForm.autoRejectEnabled ? (
              <label className="block">
                <span className="font-mono text-xs text-[var(--taali-muted)] mb-1 block">Threshold (0-100)</span>
                <Input
                  type="number"
                  min={0}
                  max={100}
                  className="w-full"
                  value={workableForm.autoRejectThreshold100}
                  onChange={(e) => setWorkableForm((prev) => ({ ...prev, autoRejectThreshold100: e.target.value }))}
                />
              </label>
            ) : null}
            {workableWriteActionsEnabled ? (
              <>
                <label className="block">
                  <span className="font-mono text-xs text-[var(--taali-muted)] mb-1 block">Workable actor member</span>
                  <Select
                    className="w-full"
                    value={workableForm.workableActorMemberId}
                    onChange={(e) => setWorkableForm((prev) => ({ ...prev, workableActorMemberId: e.target.value }))}
                    disabled={workableMembersLoading}
                  >
                    <option value="">{workableMembersLoading ? 'Loading members…' : 'Select member'}</option>
                    {workableMembers.map((member) => {
                      const memberId = String(member?.id || member?.member_id || '').trim();
                      if (!memberId) return null;
                      return (
                        <option key={memberId} value={memberId}>
                          {workableMemberLabel(member)}
                        </option>
                      );
                    })}
                  </Select>
                  <span className="font-mono text-xs text-[var(--taali-muted)] mt-1 block">
                    Workable records this member as the actor for automated invites plus TAALI-triggered reject and reopen actions.
                  </span>
                </label>
                <label className="block">
                  <span className="font-mono text-xs text-[var(--taali-muted)] mb-1 block">Default disqualification reason</span>
                  <Select
                    className="w-full"
                    value={workableForm.workableDisqualifyReasonId}
                    onChange={(e) => setWorkableForm((prev) => ({ ...prev, workableDisqualifyReasonId: e.target.value }))}
                    disabled={workableReasonsLoading}
                  >
                    <option value="">{workableReasonsLoading ? 'Loading reasons…' : 'Optional reason'}</option>
                    {workableReasons.map((reason) => {
                      const reasonId = String(reason?.id || reason?.reason_id || '').trim();
                      if (!reasonId) return null;
                      return (
                        <option key={reasonId} value={reasonId}>
                          {workableReasonLabel(reason)}
                        </option>
                      );
                    })}
                  </Select>
                </label>
                <label className="block md:col-span-2">
                  <span className="font-mono text-xs text-[var(--taali-muted)] mb-1 block">Reject note template</span>
                  <Textarea
                    rows={4}
                    value={workableForm.autoRejectNoteTemplate}
                    onChange={(e) => setWorkableForm((prev) => ({ ...prev, autoRejectNoteTemplate: e.target.value }))}
                    placeholder="Optional. Example: Auto-rejected by TAALI. Pre-screen {{pre_screen_score}}/100 below threshold {{threshold}}."
                  />
                  <span className="font-mono text-xs text-[var(--taali-muted)] mt-1 block">
                    Supports both {'{pre_screen_score}'} and {'{{pre_screen_score}}'} placeholders. Notes are truncated to Workable&apos;s 256 character limit.
                  </span>
                </label>
                <Card className="md:col-span-2 bg-[var(--taali-surface-subtle)] p-3 text-xs text-[var(--taali-text)]">
                  Workable sends rejection emails from its own disqualification automation/template. TAALI does not pick a reject template through the API.
                </Card>
              </>
            ) : null}
          </div>
          <p className="mt-2 font-mono text-xs text-[var(--taali-muted)]">
            Workable-first mode keeps TAALI compatibility fields for existing views, but recruiters should rank candidates by pre-screen score.
          </p>
          <datalist id="workable-stage-options">
            {workableStages.map((stage, index) => {
              const label = workableStageLabel(stage);
              return label ? <option key={`${label}-${index}`} value={label} /> : null;
            })}
          </datalist>
          <Card className="mt-3 bg-[var(--taali-surface-subtle)] p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-sm font-semibold text-[var(--taali-text)]">Roles to import</p>
                <p className="text-xs text-[var(--taali-muted)]">
                  {selectedRoleCountForSync}/{totalRoleCountForSync} selected
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={loadWorkableSyncJobs}
                  disabled={workableJobsLoading || !workableConnected}
                >
                  {workableJobsLoading ? 'Refreshing…' : 'Refresh roles'}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setWorkableSelectedJobShortcodes(workableSyncJobs.map((job) => String(job?.shortcode || job?.id || '').trim()).filter(Boolean))}
                  disabled={workableJobsLoading || totalRoleCountForSync === 0}
                >
                  Select all
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setWorkableSelectedJobShortcodes([])}
                  disabled={workableJobsLoading || selectedRoleCountForSync === 0}
                >
                  Clear
                </Button>
              </div>
            </div>
            <Input
              type="text"
              value={workableJobSearch}
              onChange={(e) => setWorkableJobSearch(e.target.value)}
              placeholder="Search role name or shortcode"
              disabled={workableJobsLoading || totalRoleCountForSync === 0}
            />
            {workableJobsError ? (
              <p className="text-xs text-[var(--taali-danger)]">{workableJobsError}</p>
            ) : null}
            <div className="max-h-56 overflow-y-auto rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-3">
              {workableJobsLoading ? (
                <p className="text-xs text-[var(--taali-muted)]">Loading Workable roles…</p>
              ) : filteredWorkableSyncJobs.length === 0 ? (
                <p className="text-xs text-[var(--taali-muted)]">
                  {totalRoleCountForSync === 0 ? 'No Workable roles available.' : 'No roles match your search.'}
                </p>
              ) : (
                <div className="space-y-1">
                  {filteredWorkableSyncJobs.map((job) => {
                    const identifier = String(job?.shortcode || job?.id || '').trim();
                    if (!identifier) return null;
                    return (
                      <label key={identifier} className="flex items-start gap-2 text-sm text-[var(--taali-text)]">
                        <input
                          type="checkbox"
                          checked={selectedRoleSetForSync.has(identifier)}
                          onChange={() => toggleWorkableSyncRole(identifier)}
                        />
                        <span>
                          <span className="font-medium">{job?.title || identifier}</span>
                          <span className="ml-1 text-xs text-[var(--taali-muted)]">({identifier})</span>
                        </span>
                      </label>
                    );
                  })}
                </div>
              )}
            </div>
          </Card>
          <div className="mt-4 flex flex-wrap gap-3">
            <Button
              type="button"
              variant="primary"
              disabled={workableSaving}
              onClick={handleSaveWorkable}
            >
              {workableSaving ? 'Saving…' : 'Save Workable Settings'}
            </Button>
            <Button
              type="button"
              variant="secondary"
              className="bg-[var(--taali-inverse-bg)] text-[var(--taali-inverse-text)] hover:opacity-90"
              disabled={workableSyncLoading || workableSyncInProgress || !workableConnected || (totalRoleCountForSync > 0 && selectedRoleCountForSync === 0)}
              onClick={handleSyncWorkable}
            >
              {workableSyncInProgress
                ? 'Running in background'
                : (workableForm.defaultSyncMode === 'metadata' ? 'Run metadata sync' : 'Run full sync')}
            </Button>
          </div>
        </div>

        <hr className="border-[var(--taali-border)]" />
        <div>
          <div className="font-bold mb-3 text-[var(--taali-text)]">Fireflies transcript ingestion</div>
          <Card className="mb-3 bg-[var(--taali-surface-subtle)] p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="text-sm font-semibold text-[var(--taali-text)]">
                  {orgData?.fireflies_config?.connected ? 'Transcript ingestion ready' : 'Transcript ingestion needs configuration'}
                </div>
                <p className="mt-1 text-xs leading-5 text-[var(--taali-muted)]">
                  {orgData?.fireflies_config?.connected
                    ? `Fireflies is ready to match screening calls for ${orgData.fireflies_config.owner_email || 'this workspace'} and feed transcript evidence back into recruiter review.`
                    : 'Add the Fireflies owner email, invite email, and API credentials so TAALI can match interview transcripts back to the candidate record.'}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={orgData?.fireflies_config?.connected ? 'success' : 'muted'} className="font-mono text-[11px]">
                  {orgData?.fireflies_config?.connected ? 'Connected' : 'Not connected'}
                </Badge>
                <Badge variant={firefliesForm.singleAccountMode ? 'muted' : 'purple'} className="font-mono text-[11px]">
                  {firefliesForm.singleAccountMode ? 'Single account mode' : 'Shared account mode'}
                </Badge>
              </div>
            </div>
          </Card>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            <label className="block">
              <span className="font-mono text-xs text-[var(--taali-muted)] mb-1 block">Owner email</span>
              <Input
                type="email"
                className="w-full"
                value={firefliesForm.ownerEmail}
                onChange={(e) => setFirefliesForm((prev) => ({ ...prev, ownerEmail: e.target.value }))}
                placeholder="recruiter@company.com"
              />
            </label>
            <label className="block">
              <span className="font-mono text-xs text-[var(--taali-muted)] mb-1 block">Invite email</span>
              <Input
                type="email"
                className="w-full"
                value={firefliesForm.inviteEmail}
                onChange={(e) => setFirefliesForm((prev) => ({ ...prev, inviteEmail: e.target.value }))}
                placeholder="taali@fireflies.ai"
              />
              <span className="mt-1 block text-xs leading-5 text-[var(--taali-muted)]">
                Recruiters include this address in Workable interview invites so the shared Fireflies seat joins workable interviews.
              </span>
            </label>
            <label className="block">
              <span className="font-mono text-xs text-[var(--taali-muted)] mb-1 block">Mode</span>
              <Select
                className="w-full"
                value={firefliesForm.singleAccountMode ? 'single_account' : 'shared'}
                onChange={(e) => setFirefliesForm((prev) => ({ ...prev, singleAccountMode: e.target.value !== 'shared' }))}
              >
                <option value="single_account">Single recruiter-owned account</option>
                <option value="shared">Shared / multi-account</option>
              </Select>
            </label>
            <label className="block">
              <span className="font-mono text-xs text-[var(--taali-muted)] mb-1 block">API key</span>
              <Input
                type="password"
                className="w-full"
                value={firefliesForm.apiKey}
                onChange={(e) => {
                  const nextValue = e.target.value;
                  setFirefliesForm((prev) => ({ ...prev, apiKey: nextValue }));
                  if (nextValue.trim()) setFirefliesClearApiKey(false);
                }}
                placeholder={firefliesHasApiKey ? 'Leave blank to keep current key' : 'Enter Fireflies API key'}
              />
            </label>
            <label className="block">
              <span className="font-mono text-xs text-[var(--taali-muted)] mb-1 block">Webhook secret</span>
              <Input
                type="password"
                className="w-full"
                value={firefliesForm.webhookSecret}
                onChange={(e) => {
                  const nextValue = e.target.value;
                  setFirefliesForm((prev) => ({ ...prev, webhookSecret: nextValue }));
                  if (nextValue.trim()) setFirefliesClearWebhookSecret(false);
                }}
                placeholder={firefliesWebhookSecretConfigured ? 'Leave blank to keep current secret' : 'Enter Fireflies webhook secret'}
              />
            </label>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Badge variant={firefliesHasApiKey ? 'success' : 'muted'} className="font-mono text-[11px]">
              {firefliesClearApiKey ? 'API key will be cleared' : (firefliesHasApiKey ? 'API key configured' : 'API key missing')}
            </Badge>
            <Badge variant={firefliesWebhookSecretConfigured ? 'success' : 'muted'} className="font-mono text-[11px]">
              {firefliesClearWebhookSecret ? 'Webhook secret will be cleared' : (firefliesWebhookSecretConfigured ? 'Webhook secret configured' : 'Webhook secret missing')}
            </Badge>
            {firefliesHasApiKey ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => {
                  setFirefliesForm((prev) => ({ ...prev, apiKey: '' }));
                  setFirefliesClearApiKey(true);
                }}
              >
                Clear stored API key
              </Button>
            ) : null}
            {firefliesWebhookSecretConfigured ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => {
                  setFirefliesForm((prev) => ({ ...prev, webhookSecret: '' }));
                  setFirefliesClearWebhookSecret(true);
                }}
              >
                Clear webhook secret
              </Button>
            ) : null}
          </div>
          <p className="mt-2 font-mono text-xs text-[var(--taali-muted)]">
            Fireflies transcripts feed screening support, second-stage tech interview packs, and interview evidence summaries. Ambiguous transcript matches stay in review instead of auto-linking, and this phase does not auto-insert Fireflies into Workable invites.
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <Button
              type="button"
              variant="primary"
              disabled={firefliesSaving}
              onClick={handleSaveFireflies}
            >
              {firefliesSaving ? 'Saving…' : 'Save Fireflies Settings'}
            </Button>
          </div>
        </div>

        <Panel className="mt-5 border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4">
          <div className="font-bold text-[var(--taali-danger)] mb-1">Remove all Workable data</div>
          <p className="text-sm text-[var(--taali-text)] mb-3">
            This will delete all roles, candidates, and applications that were imported from Workable.
          </p>
          <Button
            type="button"
            variant="danger"
            disabled={clearWorkableLoading}
            onClick={() => setClearWorkableModalOpen(true)}
          >
            {clearWorkableLoading ? 'Removing…' : 'Remove all candidates and roles'}
          </Button>
        </Panel>

        {clearWorkableModalOpen ? (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-4"
            role="dialog"
            aria-modal="true"
            aria-labelledby="clear-workable-title"
          >
            <Panel className="max-w-md w-full bg-[var(--taali-surface)] p-5 shadow-xl">
              <h2 id="clear-workable-title" className="text-lg font-bold mb-2 text-[var(--taali-text)]">Remove all Workable data?</h2>
              <p className="text-sm text-[var(--taali-muted)] mb-4">
                All roles, candidates, and applications imported from Workable will be deleted from this account.
              </p>
              <div className="flex gap-3 justify-end">
                <Button
                  type="button"
                  variant="secondary"
                  disabled={clearWorkableLoading}
                  onClick={() => setClearWorkableModalOpen(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="button"
                  variant="danger"
                  disabled={clearWorkableLoading}
                  onClick={handleClearWorkableData}
                >
                  {clearWorkableLoading ? 'Removing…' : 'Remove all data'}
                </Button>
              </div>
            </Panel>
          </div>
        ) : null}
      </Panel>
    </div>
  );

  const BillingSettingsTab = () => (
    <div>
      <Panel className="mb-5 p-4">
        <div className="flex items-start justify-between flex-wrap gap-4">
          <div>
            <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Current Plan</div>
            <div className="text-xl font-bold text-[var(--taali-text)]">{billingPlan}</div>
          </div>
          <div className="text-right">
            <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Total usage</div>
            <div className="text-2xl font-bold text-[var(--taali-purple)]">{toAedWithUsdLabel(monthlyCost)}</div>
            <div className="font-mono text-xs text-[var(--taali-muted)]">{monthlyAssessments} assessments</div>
          </div>
          <div className="text-right">
            <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Credits balance</div>
            <div className="text-2xl font-bold text-[var(--taali-purple)]">{creditsBalance}</div>
          </div>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          {Object.entries(packCatalog).map(([packId, pack]) => (
            <Button
              key={packId}
              type="button"
              variant="secondary"
              className="flex items-center justify-between gap-2 !px-4 !py-3 bg-[var(--taali-inverse-bg)] text-[var(--taali-inverse-text)] hover:opacity-90"
              onClick={() => handleAddCredits(packId)}
              disabled={checkoutLoading}
            >
              <span>{pack.label || packId}</span>
              <span className="inline-flex items-center gap-1">
                {checkoutLoading ? <Spinner size={14} /> : <CreditCard size={14} />}
                +{pack.credits || 0}
              </span>
            </Button>
          ))}
        </div>
      </Panel>

      <div className="mb-5 grid gap-4 md:grid-cols-2">
        <Panel className="p-4">
          <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Daily spend threshold</div>
          <div className="text-xl font-bold text-[var(--taali-text)]">{toAedWithUsdLabel(thresholdConfig.daily_spend_usd ?? 0, null, { maximumFractionDigits: 2 })}</div>
          <div className={`font-mono text-xs mt-2 ${thresholdStatus.daily_spend_exceeded ? 'text-[var(--taali-danger)]' : 'text-[var(--taali-success)]'}`}>
            Today: {toAedWithUsdLabel(Number(spendSummary.daily_spend_usd || 0), null, { maximumFractionDigits: 2 })} • {thresholdStatus.daily_spend_exceeded ? 'Exceeded' : 'Within threshold'}
          </div>
        </Panel>
        <Panel className="p-4">
          <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Cost / completed assessment threshold</div>
          <div className="text-xl font-bold text-[var(--taali-text)]">{toAedWithUsdLabel(thresholdConfig.cost_per_completed_assessment_usd ?? 0, null, { maximumFractionDigits: 2 })}</div>
          <div className={`font-mono text-xs mt-2 ${thresholdStatus.cost_per_completed_assessment_exceeded ? 'text-[var(--taali-danger)]' : 'text-[var(--taali-success)]'}`}>
            Current: {toAedWithUsdLabel(Number(spendSummary.cost_per_completed_assessment_usd || 0), null, { maximumFractionDigits: 2 })} • {thresholdStatus.cost_per_completed_assessment_exceeded ? 'Exceeded' : 'Within threshold'}
          </div>
        </Panel>
      </div>

      <TableShell>
        <div className="flex items-center justify-between gap-3 border-b border-[var(--taali-border-soft)] px-4 py-3">
          <h3 className="font-bold text-[var(--taali-text)]">Usage History</h3>
        </div>
        <table className="w-full">
          <thead>
            <tr>
              <th className="px-4 py-2.5 text-left font-mono text-[11px] font-bold uppercase text-[var(--taali-text)]">Date</th>
              <th className="px-4 py-2.5 text-left font-mono text-[11px] font-bold uppercase text-[var(--taali-text)]">Candidate</th>
              <th className="px-4 py-2.5 text-left font-mono text-[11px] font-bold uppercase text-[var(--taali-text)]">Task</th>
              <th className="px-4 py-2.5 text-right font-mono text-[11px] font-bold uppercase text-[var(--taali-text)]">Cost</th>
            </tr>
          </thead>
          <tbody>
            {usageHistory.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-6 py-8 font-mono text-sm text-[var(--taali-muted)] text-center">
                  No usage yet. Completed assessments will appear here.
                </td>
              </tr>
            ) : (
              usageHistory.map((row, i) => (
                <tr key={row.assessment_id ?? i} className="border-b border-[var(--taali-border-muted)] hover:bg-[var(--taali-bg)]">
                  <td className="px-4 py-2.5 font-mono text-sm text-[var(--taali-text)]">{row.date}</td>
                  <td className="px-4 py-2.5 text-sm text-[var(--taali-text)]">{row.candidate}</td>
                  <td className="px-4 py-2.5 font-mono text-sm text-[var(--taali-text)]">{row.task}</td>
                  <td className="px-4 py-2.5 text-right font-mono text-sm font-bold text-[var(--taali-text)]">{toAedWithUsdLabel(row.cost)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </TableShell>
    </div>
  );

  const TeamSettingsTab = () => (
    <div className="space-y-6">
      <Panel className="p-4">
        <h3 className="mb-3 text-lg font-bold text-[var(--taali-text)]">Invite Team Member</h3>
        <form className="grid md:grid-cols-3 gap-3" onSubmit={handleInvite}>
          <Input
            type="text"
            placeholder="Full name"
            value={inviteName}
            onChange={(e) => setInviteName(e.target.value)}
          />
          <Input
            type="email"
            placeholder="Email"
            value={inviteEmail}
            onChange={(e) => setInviteEmail(e.target.value)}
          />
          <Button
            type="submit"
            variant="primary"
            disabled={inviteLoading}
          >
            {inviteLoading ? 'Inviting…' : 'Invite'}
          </Button>
        </form>
      </Panel>
      <TableShell>
        <div className="flex items-center justify-between gap-3 border-b border-[var(--taali-border-soft)] px-4 py-3">
          <h3 className="font-bold text-[var(--taali-text)]">Team Members</h3>
        </div>
        <table className="w-full">
          <thead>
            <tr>
              <th className="px-4 py-2.5 text-left font-mono text-[11px] font-bold uppercase text-[var(--taali-text)]">Name</th>
              <th className="px-4 py-2.5 text-left font-mono text-[11px] font-bold uppercase text-[var(--taali-text)]">Email</th>
            </tr>
          </thead>
          <tbody>
            {teamMembers.length === 0 ? (
              <tr><td colSpan={2} className="px-6 py-8 font-mono text-sm text-[var(--taali-muted)] text-center">No members yet.</td></tr>
            ) : teamMembers.map((m) => (
              <tr key={m.id} className="border-b border-[var(--taali-border-muted)]">
                <td className="px-4 py-2.5 text-[var(--taali-text)]">{m.full_name || '—'}</td>
                <td className="px-4 py-2.5 font-mono text-sm text-[var(--taali-text)]">{m.email}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableShell>
    </div>
  );

  const EnterpriseSettingsTab = () => (
    <div className="space-y-6">
      <Panel className="p-4">
        <h3 className="mb-3 text-lg font-bold text-[var(--taali-text)]">Enterprise Access Controls</h3>
        <div className="space-y-4">
          <div>
            <label className="font-mono text-xs text-[var(--taali-muted)] mb-1 block">Allowed email domains (comma separated)</label>
            <Input
              type="text"
              className="w-full"
              placeholder="acme.com, subsidiary.org"
              value={enterpriseForm.allowedEmailDomains}
              onChange={(e) => setEnterpriseForm((prev) => ({ ...prev, allowedEmailDomains: e.target.value }))}
            />
            <div className="font-mono text-xs text-[var(--taali-muted)] mt-1">
              Leave empty to allow any domain.
            </div>
          </div>
          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              className="w-4 h-4 accent-[var(--taali-purple)]"
              checked={enterpriseForm.ssoEnforced}
              onChange={(e) => setEnterpriseForm((prev) => ({ ...prev, ssoEnforced: e.target.checked }))}
            />
            <span className="text-sm text-[var(--taali-text)]">Enforce SSO (blocks password login and invites)</span>
          </label>
          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              className="w-4 h-4 accent-[var(--taali-purple)]"
              checked={enterpriseForm.samlEnabled}
              onChange={(e) => setEnterpriseForm((prev) => ({ ...prev, samlEnabled: e.target.checked }))}
            />
            <span className="text-sm text-[var(--taali-text)]">Enable SAML metadata configuration</span>
          </label>
          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              className="w-4 h-4 accent-[var(--taali-purple)]"
              checked={enterpriseForm.candidateFeedbackEnabled}
              onChange={(e) => setEnterpriseForm((prev) => ({ ...prev, candidateFeedbackEnabled: e.target.checked }))}
            />
            <span className="text-sm text-[var(--taali-text)]">Enable candidate feedback reports</span>
          </label>
          <div>
            <label className="font-mono text-xs text-[var(--taali-muted)] mb-1 block">SAML metadata URL</label>
            <Input
              type="url"
              className="w-full"
              placeholder="https://idp.example.com/metadata.xml"
              value={enterpriseForm.samlMetadataUrl}
              onChange={(e) => setEnterpriseForm((prev) => ({ ...prev, samlMetadataUrl: e.target.value }))}
            />
          </div>
          <Button
            type="button"
            variant="primary"
            disabled={enterpriseSaving}
            onClick={handleSaveEnterprise}
          >
            {enterpriseSaving ? 'Saving…' : 'Save enterprise settings'}
          </Button>
        </div>
      </Panel>
    </div>
  );

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="settings" onNavigate={onNavigate} /> : null}
      <PageContainer density="compact" width="wide">
        <PageHeader
          density="compact"
          className="mb-5"
          title="Settings"
          subtitle="Manage integrations, billing, workspace preferences, and team access."
        />

        <TabBar
          tabs={settingsTabs}
          activeTab={activeSettingsTab}
          onChange={setSettingsTab}
          density="compact"
          className="mb-5"
        />

        {orgLoading ? (
          <div className="space-y-5">
            <CardSkeleton lines={3} />
            <div className="grid gap-4 md:grid-cols-2">
              <CardSkeleton lines={4} />
              <CardSkeleton lines={4} />
            </div>
          </div>
        ) : (
          <>
            {activeSettingsTab === 'workable' && <WorkableSettingsTab />}

            {activeSettingsTab === 'billing' && <BillingSettingsTab />}

            {activeSettingsTab === 'team' && <TeamSettingsTab />}

            {activeSettingsTab === 'enterprise' && <EnterpriseSettingsTab />}

            {activeSettingsTab === 'preferences' && (
              <PreferencesSettingsTab
                defaultAssessmentMinutes={defaultAssessmentMinutes}
                setDefaultAssessmentMinutes={setDefaultAssessmentMinutes}
                emailTemplatePreview={emailTemplatePreview}
                setEmailTemplatePreview={setEmailTemplatePreview}
                preferencesSavedAt={preferencesSavedAt}
                preferencesSaving={preferencesSaving}
                handleSavePreferences={handleSavePreferences}
              />
            )}
          </>
        )}
      </PageContainer>
      <Sheet
        open={workableDrawerOpen && activeSettingsTab === 'workable'}
        onClose={closeWorkableDrawer}
        title="Connect Workable"
        description="Choose connection mode and rights before connecting."
        footer={null}
      >
        <div className="space-y-5">
          <div className="grid grid-cols-2 rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface-subtle)] p-1">
            <button
              type="button"
              className={`rounded-full px-4 py-2 font-mono text-sm font-bold ${workableConnectMode === 'oauth' ? 'bg-[var(--taali-inverse-bg)] text-[var(--taali-inverse-text)]' : 'bg-transparent text-[var(--taali-text)] hover:bg-[var(--taali-surface)]'}`}
              onClick={() => {
                setWorkableConnectMode('oauth');
                setWorkableConnectError('');
              }}
            >
              OAuth
            </button>
            <button
              type="button"
              className={`rounded-full px-4 py-2 font-mono text-sm font-bold ${workableConnectMode === 'token' ? 'bg-[var(--taali-inverse-bg)] text-[var(--taali-inverse-text)]' : 'bg-transparent text-[var(--taali-text)] hover:bg-[var(--taali-surface)]'}`}
              onClick={() => {
                setWorkableConnectMode('token');
                setWorkableConnectError('');
              }}
            >
              API Token
            </button>
          </div>

          <Panel className="p-4 space-y-3">
            <div className="font-bold text-[var(--taali-text)]">Token Rights / Scopes</div>
            {WORKABLE_SCOPE_OPTIONS.map((scope) => (
              <label key={scope.id} className="flex items-start gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  className="mt-0.5 w-4 h-4 accent-[var(--taali-purple)]"
                  checked={Boolean(workableSelectedScopes[scope.id])}
                  onChange={() => toggleWorkableScope(scope.id)}
                />
                <span>
                  <span className="font-mono text-sm font-bold text-[var(--taali-text)]">{scope.label}</span>
                  <span className="font-mono text-xs text-[var(--taali-muted)] block">{scope.description}</span>
                </span>
              </label>
            ))}
            <div className="font-mono text-xs text-[var(--taali-muted)]">
              Selected scopes: {workableScopes}
            </div>
            <div className="font-mono text-xs text-[var(--taali-muted)]">
              Mode after connect: {workableWriteScopeEnabled
                ? 'Write-enabled (TAALI can invite, reject, and reopen in Workable after actor member setup)'
                : 'Read-only sync only (TAALI actions stay local)'}
            </div>
          </Panel>

          {workableConnectMode === 'oauth' ? (
            <Panel className="p-4 space-y-3">
              <div className="font-bold text-[var(--taali-text)]">OAuth Setup</div>
              <div className="font-mono text-xs text-[var(--taali-muted)]">Callback URL: {workableCallbackUrl}</div>
              <Button
                type="button"
                variant="secondary"
                className="bg-[var(--taali-inverse-bg)] text-[var(--taali-inverse-text)] hover:opacity-90"
                disabled={workableOAuthLoading}
                onClick={handleConnectWorkableOAuth}
              >
                {workableOAuthLoading ? 'Redirecting…' : 'Continue with Workable OAuth'}
              </Button>
            </Panel>
          ) : (
            <form className="space-y-3 p-4 taali-panel" onSubmit={handleConnectWorkableToken}>
              <div className="font-bold text-[var(--taali-text)]">API Token Setup</div>
              <Input
                type="text"
                placeholder="Workable subdomain (e.g. acme)"
                className="w-full"
                value={workableTokenForm.subdomain}
                onChange={(e) => setWorkableTokenForm((prev) => ({ ...prev, subdomain: e.target.value }))}
              />
              <Input
                type="password"
                placeholder="Workable API access token"
                className="w-full"
                value={workableTokenForm.accessToken}
                onChange={(e) => setWorkableTokenForm((prev) => ({ ...prev, accessToken: e.target.value }))}
              />
              <Button
                type="submit"
                variant="secondary"
                className="bg-[var(--taali-inverse-bg)] text-[var(--taali-inverse-text)] hover:opacity-90"
                disabled={workableTokenSaving}
              >
                {workableTokenSaving ? 'Connecting…' : 'Connect via API Token'}
              </Button>
            </form>
          )}

          {missingRequiredWorkableScopes.length > 0 ? (
            <div className="font-mono text-xs text-[var(--taali-danger)]">
              Missing required scopes: {missingRequiredWorkableScopes.join(', ')}
            </div>
          ) : null}
          {workableConnectError ? (
            <div className="font-mono text-xs text-[var(--taali-danger)]">
              {workableConnectError}
            </div>
          ) : null}
        </div>
      </Sheet>
    </div>
  );
};
