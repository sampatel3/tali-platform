import React, { useState, useEffect, useRef } from 'react';
import { AlertTriangle, CheckCircle, CreditCard } from 'lucide-react';

import {
  Button,
  Input,
  Select,
  Spinner,
  TabBar,
  Panel,
  Sheet,
} from '../../shared/ui/TaaliPrimitives';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { organizations as orgsApi, billing as billingApi, team as teamApi } from '../../shared/api';
import { formatAed } from '../../lib/currency';
import {
  readDarkModePreference,
  setDarkModePreference,
  subscribeThemePreference,
} from '../../lib/themePreference';

const WORKABLE_SCOPE_OPTIONS = [
  { id: 'r_jobs', label: 'r_jobs', description: 'Read jobs and roles from Workable.' },
  { id: 'r_candidates', label: 'r_candidates', description: 'Read candidate profiles and stages.' },
  { id: 'w_candidates', label: 'w_candidates', description: 'Write candidate stage/activity for invite + notes.' },
];

const WORKABLE_REQUIRED_SCOPES = ['r_jobs', 'r_candidates'];

const normalizeWorkableError = (input) => {
  const raw = (input || '').toString();
  const lower = raw.toLowerCase();
  if (lower.includes('not configured')) {
    return 'Workable OAuth is not configured. Add WORKABLE_CLIENT_ID and WORKABLE_CLIENT_SECRET in backend environment variables first.';
  }
  if (lower.includes('disabled for mvp')) {
    return 'Workable integration is currently disabled by environment flag.';
  }
  if (lower.includes('oauth failed')) {
    return 'Workable OAuth failed. Verify callback URL and scopes in your Workable app, then try again.';
  }
  return raw || 'Workable connection failed.';
};

export const SettingsPage = ({ onNavigate, NavComponent = null, ConnectWorkableButton }) => {
  const { user } = useAuth();
  const { showToast } = useToast();
  const [settingsTab, setSettingsTab] = useState('workable');
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
  const [darkMode, setDarkMode] = useState(() => readDarkModePreference());
  const [enterpriseSaving, setEnterpriseSaving] = useState(false);
  const [enterpriseForm, setEnterpriseForm] = useState({
    allowedEmailDomains: '',
    ssoEnforced: false,
    samlEnabled: false,
    samlMetadataUrl: '',
  });
  const [workableForm, setWorkableForm] = useState({
    emailMode: 'manual_taali',
    syncIntervalMinutes: 30,
    inviteStageName: '',
  });
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
    if (settingsTab !== 'billing') return;
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
  }, [settingsTab]);

  useEffect(() => {
    if (settingsTab !== 'team') return;
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
  }, [settingsTab]);

  useEffect(() => {
    setDarkModePreference(darkMode);
  }, [darkMode]);

  useEffect(() => {
    return subscribeThemePreference((next) => {
      setDarkMode(Boolean(next));
    });
  }, []);

  useEffect(() => {
    if (!orgData) return;
    const domains = Array.isArray(orgData.allowed_email_domains) ? orgData.allowed_email_domains.join(', ') : '';
    const cfg = orgData.workable_config || {};
    setEnterpriseForm({
      allowedEmailDomains: domains,
      ssoEnforced: Boolean(orgData.sso_enforced),
      samlEnabled: Boolean(orgData.saml_enabled),
      samlMetadataUrl: orgData.saml_metadata_url || '',
    });
    setWorkableForm({
      emailMode: cfg.email_mode || 'manual_taali',
      syncIntervalMinutes: Number(cfg.sync_interval_minutes || 30),
      inviteStageName: cfg.invite_stage_name || '',
    });
    setWorkableSelectedScopes({
      r_jobs: true,
      r_candidates: true,
      w_candidates: (cfg.email_mode || 'manual_taali') === 'workable_preferred_fallback_manual',
    });
    setWorkableTokenForm((prev) => ({
      ...prev,
      subdomain: prev.subdomain || orgData.workable_subdomain || '',
    }));
  }, [orgData]);

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
        ? 'Clear endpoint not available. Deploy the latest backend (with POST /workable/clear) and run migrations.'
        : (detail || 'Failed to clear Workable data');
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

  useEffect(() => {
    if (settingsTab !== 'workable') return;
    fetchWorkableSyncStatus();
    loadWorkableSyncJobs();
  }, [settingsTab, orgData?.workable_connected]);

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
        const msg = Array.isArray(s.errors) && s.errors.length > 0
          ? `${s.errors[0]}`
          : `Metadata sync finished. Roles: ${s.jobs_processed ?? s.jobs_seen ?? 0}/${s.jobs_total ?? s.jobs_seen ?? 0}, Candidates: ${s.candidates_seen ?? 0} seen (${s.candidates_upserted ?? 0} upserted).`;
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
        ? 'Stop sync is not available yet. Deploy the latest backend (Railway) and run migrations, then try again.'
        : (detail || 'Failed to cancel sync');
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
      const res = await orgsApi.syncWorkable({
        mode: 'metadata',
        job_shortcodes: selectedIdentifiers,
      });
      const runId = res?.data?.run_id ?? null;
      setWorkableActiveRunId(runId);
      setWorkableSyncInProgress(true);
      const selectedCount = selectedIdentifiers.length;
      showToast(
        `Metadata sync is running for ${selectedCount} role${selectedCount === 1 ? '' : 's'} in the background.`,
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
    const inviteStageName = (workableForm.inviteStageName || '').trim();
    setWorkableSaving(true);
    try {
      const res = await orgsApi.update({
        workable_config: {
          email_mode: emailMode,
          sync_model: 'scheduled_pull_only',
          sync_scope: 'open_jobs_active_candidates',
          score_precedence: 'workable_first',
          sync_interval_minutes: Number(workableForm.syncIntervalMinutes || 30),
          invite_stage_name: emailMode === 'workable_preferred_fallback_manual' ? inviteStageName : '',
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
        },
      }));
      setWorkableTokenForm((prev) => ({ ...prev, accessToken: '' }));
      closeWorkableDrawer();
      showToast(
        readOnly
          ? 'Workable connected in read-only mode. Sync is enabled and TAALI email flow remains manual.'
          : 'Workable connected with write scope. You can use Workable-first invite mode.',
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
      });
      setOrgData(res.data);
      showToast('Enterprise access controls updated.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to save enterprise settings', 'error');
    } finally {
      setEnterpriseSaving(false);
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
  const workableScopes = selectedWorkableScopes.join(' ') || 'none';
  const workableWriteScopeEnabled = selectedWorkableScopes.includes('w_candidates');
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
  const toAedLabel = (rawValue, fallbackAmount = null) => {
    if (typeof rawValue === 'string') {
      const trimmed = rawValue.trim();
      if (trimmed.toUpperCase().startsWith('AED')) return trimmed;
      const numeric = Number(trimmed.replace(/[^\d.-]/g, ''));
      if (!Number.isNaN(numeric)) return formatAed(numeric);
    }
    if (typeof rawValue === 'number') return formatAed(rawValue);
    if (fallbackAmount != null) return formatAed(fallbackAmount);
    return formatAed(0);
  };

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="settings" onNavigate={onNavigate} /> : null}
      <div className="max-w-7xl mx-auto px-6 py-8">
        <h1 className="text-3xl font-bold mb-2">Settings</h1>
        <p className="text-sm text-[var(--taali-muted)] mb-8">Manage integrations and billing</p>

        <TabBar
          tabs={[
            { id: 'workable', label: 'Workable', panelId: 'settings-workable' },
            { id: 'billing', label: 'Billing', panelId: 'settings-billing' },
            { id: 'team', label: 'Team', panelId: 'settings-team' },
            { id: 'enterprise', label: 'Enterprise', panelId: 'settings-enterprise' },
            { id: 'preferences', label: 'Preferences', panelId: 'settings-preferences' },
          ]}
          activeTab={settingsTab}
          onChange={setSettingsTab}
          className="mb-8"
        />

        {orgLoading ? (
          <div className="flex items-center justify-center py-16 gap-3">
            <Spinner size={24} />
            <span className="text-sm text-[var(--taali-muted)]">Loading settings...</span>
          </div>
        ) : (
          <>
            {settingsTab === 'workable' && (
              <div>
                <Panel className={`p-6 mb-8 flex items-center justify-between gap-4 flex-wrap ${workableConnected ? 'bg-[var(--taali-success-soft)]' : 'bg-[var(--taali-warning-soft)]'}`}>
                  <div className="flex items-center gap-4">
                    {workableConnected ? <CheckCircle size={24} className="text-[var(--taali-success)]" /> : <AlertTriangle size={24} className="text-[var(--taali-warning)]" />}
                    <div>
                      <div className="font-bold text-lg text-[var(--taali-text)]">Status: {workableConnected ? 'Connected' : 'Not Connected'}</div>
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

                <Panel className="p-6 space-y-4">
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
                    <div className="font-mono text-[var(--taali-text)]">{orgData?.active_claude_model || '—'}</div>
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
                  <div className="border-2 border-[var(--taali-border-muted)] bg-[var(--taali-bg)] p-3 text-sm text-[var(--taali-text)]">
                    <div className="font-semibold mb-1">What happens when you sync</div>
                    <ul className="list-disc list-inside space-y-0.5">
                      <li>Open jobs from Workable are imported as roles; job specs are saved as attachments.</li>
                      <li>All candidates for each job are fetched (no 50-candidate limit).</li>
                      <li>Only metadata is synced in this baseline run (roles, candidate/application records, stages).</li>
                      <li>CV fetch and TAALI scoring are run separately from the Candidates page when needed.</li>
                    </ul>
                    <p className="mt-2 text-xs text-[var(--taali-muted)]">
                      For a completely fresh import, use <strong>Remove all candidates and roles</strong> below, then run <strong>Metadata sync</strong>.
                    </p>
                  </div>
                  {workableSyncInProgress && (
                    <div className="border-2 border-[var(--taali-border)] bg-[var(--taali-warning-soft)] p-4 flex items-center gap-3 mt-3">
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
                    </div>
                  )}
                  <hr className="border-[var(--taali-border)]" />
                  <div>
                    <div className="font-bold mb-3 text-[var(--taali-text)]">Sync + Invite Settings</div>
                    <div className="grid md:grid-cols-2 gap-3">
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
                          Automated mode requires `w_candidates` scope and a Workable stage name.
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
                            className="w-full"
                            placeholder="Enter exact Workable stage name"
                            value={workableForm.inviteStageName}
                            onChange={(e) => setWorkableForm((prev) => ({ ...prev, inviteStageName: e.target.value }))}
                          />
                          <span className="font-mono text-xs text-[var(--taali-muted)] mt-1 block">
                            Keep this blank in manual mode. For automated mode, enter the exact stage already configured in Workable.
                          </span>
                        </label>
                      ) : null}
                    </div>
                    <p className="mt-2 font-mono text-xs text-[var(--taali-muted)]">
                      Metadata sync is the default baseline. Use candidate-level enrichment, CV fetch, and TAALI scoring actions from the Candidates page when needed.
                    </p>
                    <div className="mt-3 border border-[var(--taali-border)] bg-[var(--taali-bg)] p-3 space-y-2">
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
                      <div className="max-h-56 overflow-y-auto border border-[var(--taali-border)] bg-[var(--taali-surface)] p-2">
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
                    </div>
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
                        className="border-2 border-[var(--taali-border)] bg-[var(--taali-text)] text-[var(--taali-surface)] hover:opacity-90"
                        disabled={workableSyncLoading || workableSyncInProgress || !workableConnected || (totalRoleCountForSync > 0 && selectedRoleCountForSync === 0)}
                        onClick={handleSyncWorkable}
                      >
                        {workableSyncInProgress ? 'Running in background' : 'Run metadata sync'}
                      </Button>
                    </div>
                  </div>

                  <Panel className="border-2 border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-6 mt-6">
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
                      <Panel className="border-2 border-[var(--taali-border)] shadow-xl max-w-md w-full p-6 bg-[var(--taali-surface)]">
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
            )}

            {settingsTab === 'billing' && (
              <div>
                <Panel className="p-6 mb-8">
                  <div className="flex items-start justify-between flex-wrap gap-4">
                    <div>
                      <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Current Plan</div>
                      <div className="text-2xl font-bold text-[var(--taali-text)]">{billingPlan}</div>
                      <div className="font-mono text-sm text-[var(--taali-muted)] mt-1">Billing provider: Lemon</div>
                    </div>
                    <div className="text-right">
                      <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Total usage</div>
                      <div className="text-3xl font-bold text-[var(--taali-purple)]">{formatAed(monthlyCost)}</div>
                      <div className="font-mono text-xs text-[var(--taali-muted)]">{monthlyAssessments} assessments</div>
                    </div>
                    <div className="text-right">
                      <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Credits balance</div>
                      <div className="text-3xl font-bold text-[var(--taali-purple)]">{creditsBalance}</div>
                    </div>
                  </div>
                  <div className="mt-5 grid md:grid-cols-3 gap-3">
                    {Object.entries(packCatalog).map(([packId, pack]) => (
                      <Button
                        key={packId}
                        type="button"
                        variant="secondary"
                        className="flex items-center justify-between gap-2 !px-4 !py-3 border-2 border-[var(--taali-border)] bg-[var(--taali-text)] text-[var(--taali-surface)] hover:opacity-90"
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

                <div className="grid md:grid-cols-2 gap-4 mb-8">
                  <Panel className="p-4">
                    <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Daily spend threshold</div>
                    <div className="text-2xl font-bold text-[var(--taali-text)]">{formatAed(thresholdConfig.daily_spend_usd ?? 0, { maximumFractionDigits: 2 })}</div>
                    <div className={`font-mono text-xs mt-2 ${thresholdStatus.daily_spend_exceeded ? 'text-[var(--taali-danger)]' : 'text-[var(--taali-success)]'}`}>
                      Today: {formatAed(Number(spendSummary.daily_spend_usd || 0), { maximumFractionDigits: 2 })} • {thresholdStatus.daily_spend_exceeded ? 'Exceeded' : 'Within threshold'}
                    </div>
                  </Panel>
                  <Panel className="p-4">
                    <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Cost / completed assessment threshold</div>
                    <div className="text-2xl font-bold text-[var(--taali-text)]">{formatAed(thresholdConfig.cost_per_completed_assessment_usd ?? 0, { maximumFractionDigits: 2 })}</div>
                    <div className={`font-mono text-xs mt-2 ${thresholdStatus.cost_per_completed_assessment_exceeded ? 'text-[var(--taali-danger)]' : 'text-[var(--taali-success)]'}`}>
                      Current: {formatAed(Number(spendSummary.cost_per_completed_assessment_usd || 0), { maximumFractionDigits: 2 })} • {thresholdStatus.cost_per_completed_assessment_exceeded ? 'Exceeded' : 'Within threshold'}
                    </div>
                  </Panel>
                </div>

                <div className="border-2 border-[var(--taali-border)]">
                  <div className="border-b-2 border-[var(--taali-border)] px-6 py-4 bg-[var(--taali-text)] text-[var(--taali-surface)]">
                    <h3 className="font-bold">Usage History</h3>
                  </div>
                  <table className="w-full">
                    <thead>
                      <tr className="border-b-2 border-[var(--taali-border)] bg-[var(--taali-bg)]">
                        <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase text-[var(--taali-text)]">Date</th>
                        <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase text-[var(--taali-text)]">Candidate</th>
                        <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase text-[var(--taali-text)]">Task</th>
                        <th className="text-right px-6 py-3 font-mono text-xs font-bold uppercase text-[var(--taali-text)]">Cost</th>
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
                            <td className="px-6 py-3 font-mono text-sm text-[var(--taali-text)]">{row.date}</td>
                            <td className="px-6 py-3 text-sm text-[var(--taali-text)]">{row.candidate}</td>
                            <td className="px-6 py-3 font-mono text-sm text-[var(--taali-text)]">{row.task}</td>
                            <td className="px-6 py-3 font-mono text-sm text-right font-bold text-[var(--taali-text)]">{toAedLabel(row.cost)}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {settingsTab === 'team' && (
              <div className="space-y-6">
                <Panel className="p-6">
                  <h3 className="text-xl font-bold mb-4 text-[var(--taali-text)]">Invite Team Member</h3>
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
                <div className="border-2 border-[var(--taali-border)]">
                  <div className="border-b-2 border-[var(--taali-border)] px-6 py-4 bg-[var(--taali-text)] text-[var(--taali-surface)]">
                    <h3 className="font-bold">Team Members</h3>
                  </div>
                  <table className="w-full">
                    <thead>
                      <tr className="border-b-2 border-[var(--taali-border)] bg-[var(--taali-bg)]">
                        <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase text-[var(--taali-text)]">Name</th>
                        <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase text-[var(--taali-text)]">Email</th>
                      </tr>
                    </thead>
                    <tbody>
                      {teamMembers.length === 0 ? (
                        <tr><td colSpan={2} className="px-6 py-8 font-mono text-sm text-[var(--taali-muted)] text-center">No members yet.</td></tr>
                      ) : teamMembers.map((m) => (
                        <tr key={m.id} className="border-b border-[var(--taali-border-muted)]">
                          <td className="px-6 py-3 text-[var(--taali-text)]">{m.full_name || '—'}</td>
                          <td className="px-6 py-3 font-mono text-sm text-[var(--taali-text)]">{m.email}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {settingsTab === 'enterprise' && (
              <div className="space-y-6">
                <Panel className="p-6">
                  <h3 className="text-xl font-bold mb-4 text-[var(--taali-text)]">Enterprise Access Controls</h3>
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
            )}

            {settingsTab === 'preferences' && (
              <Panel className="p-6">
                <h3 className="text-xl font-bold mb-4 text-[var(--taali-text)]">Display Preferences</h3>
                <label className="flex items-center gap-3 text-sm text-[var(--taali-text)] cursor-pointer">
                  <input
                    type="checkbox"
                    checked={darkMode}
                    onChange={(e) => setDarkMode(e.target.checked)}
                    className="w-4 h-4 accent-[var(--taali-purple)]"
                  />
                  Enable dark mode (default)
                </label>
              </Panel>
            )}
          </>
        )}
      </div>
      <Sheet
        open={workableDrawerOpen && settingsTab === 'workable'}
        onClose={closeWorkableDrawer}
        title="Connect Workable"
        description="Choose connection mode and rights before connecting."
        footer={null}
      >
        <div className="space-y-5">
          <div className="grid grid-cols-2 border-2 border-[var(--taali-border)]">
            <button
              type="button"
              className={`px-4 py-2 font-mono text-sm font-bold border-r-2 border-[var(--taali-border)] ${workableConnectMode === 'oauth' ? 'bg-[var(--taali-text)] text-[var(--taali-surface)]' : 'bg-[var(--taali-surface)] text-[var(--taali-text)] hover:bg-[var(--taali-bg)]'}`}
              onClick={() => {
                setWorkableConnectMode('oauth');
                setWorkableConnectError('');
              }}
            >
              OAuth
            </button>
            <button
              type="button"
              className={`px-4 py-2 font-mono text-sm font-bold ${workableConnectMode === 'token' ? 'bg-[var(--taali-text)] text-[var(--taali-surface)]' : 'bg-[var(--taali-surface)] text-[var(--taali-text)] hover:bg-[var(--taali-bg)]'}`}
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
              Mode after connect: {workableWriteScopeEnabled ? 'Write-enabled (Workable invite path possible)' : 'Read-only (manual TAALI invites only)'}
            </div>
          </Panel>

          {workableConnectMode === 'oauth' ? (
            <Panel className="p-4 space-y-3">
              <div className="font-bold text-[var(--taali-text)]">OAuth Setup</div>
              <div className="font-mono text-xs text-[var(--taali-muted)]">Callback URL: {workableCallbackUrl}</div>
              <Button
                type="button"
                variant="secondary"
                className="border-2 border-[var(--taali-border)] bg-[var(--taali-text)] text-[var(--taali-surface)] hover:opacity-90"
                disabled={workableOAuthLoading}
                onClick={handleConnectWorkableOAuth}
              >
                {workableOAuthLoading ? 'Redirecting…' : 'Continue with Workable OAuth'}
              </Button>
            </Panel>
          ) : (
            <form className="border-2 border-[var(--taali-border)] p-4 space-y-3 taali-panel" onSubmit={handleConnectWorkableToken}>
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
                className="border-2 border-[var(--taali-border)] bg-[var(--taali-text)] text-[var(--taali-surface)] hover:opacity-90"
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
