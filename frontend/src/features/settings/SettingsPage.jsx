import React, { useState, useEffect } from 'react';
import { AlertTriangle, CheckCircle, CreditCard, Loader2 } from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { organizations as orgsApi, billing as billingApi, team as teamApi } from '../../shared/api';
import { formatAed } from '../../lib/currency';

export const SettingsPage = ({ onNavigate, NavComponent = null, ConnectWorkableButton }) => {
  const { user } = useAuth();
  const [settingsTab, setSettingsTab] = useState('workable');
  const [orgData, setOrgData] = useState(null);
  const [orgLoading, setOrgLoading] = useState(true);
  const [billingUsage, setBillingUsage] = useState(null);
  const [billingCosts, setBillingCosts] = useState(null);
  const [billingCredits, setBillingCredits] = useState(null);
  const [checkoutLoading, setCheckoutLoading] = useState(false);
  const [workableSaving, setWorkableSaving] = useState(false);
  const [workableSyncLoading, setWorkableSyncLoading] = useState(false);
  const [teamMembers, setTeamMembers] = useState([]);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteName, setInviteName] = useState('');
  const [inviteLoading, setInviteLoading] = useState(false);
  const [darkMode, setDarkMode] = useState(() => localStorage.getItem('taali_dark_mode') === '1');
  const [enterpriseSaving, setEnterpriseSaving] = useState(false);
  const [enterpriseForm, setEnterpriseForm] = useState({
    allowedEmailDomains: '',
    ssoEnforced: false,
    samlEnabled: false,
    samlMetadataUrl: '',
  });
  const [workableForm, setWorkableForm] = useState({
    workflowMode: 'manual',
    emailMode: 'manual_taali',
    syncIntervalMinutes: 30,
    inviteStageName: 'TAALI Assessment Requested',
  });

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
    localStorage.setItem('taali_dark_mode', darkMode ? '1' : '0');
    document.documentElement.classList.toggle('dark', darkMode);
  }, [darkMode]);

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
      workflowMode: cfg.workflow_mode || 'manual',
      emailMode: cfg.email_mode || 'manual_taali',
      syncIntervalMinutes: Number(cfg.sync_interval_minutes || 30),
      inviteStageName: cfg.invite_stage_name || 'TAALI Assessment Requested',
    });
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

  const handleSyncWorkableNow = async () => {
    setWorkableSyncLoading(true);
    try {
      const res = await orgsApi.syncWorkable({ full_resync: false });
      setOrgData((prev) => ({
        ...(prev || {}),
        workable_last_sync_at: res.data?.workable_last_sync_at || new Date().toISOString(),
        workable_last_sync_status: res.data?.workable_last_sync_status || 'success',
        workable_last_sync_summary: res.data?.summary || {},
      }));
      alert('Workable sync completed.');
    } catch (err) {
      alert(err?.response?.data?.detail || 'Workable sync failed');
    } finally {
      setWorkableSyncLoading(false);
    }
  };

  const handleSaveWorkable = async () => {
    setWorkableSaving(true);
    try {
      const res = await orgsApi.update({
        workable_config: {
          workflow_mode: workableForm.workflowMode,
          email_mode: workableForm.emailMode,
          sync_model: 'scheduled_pull_only',
          sync_scope: 'open_jobs_active_candidates',
          score_precedence: 'workable_first',
          sync_interval_minutes: Number(workableForm.syncIntervalMinutes || 30),
          invite_stage_name: workableForm.inviteStageName || 'TAALI Assessment Requested',
        },
      });
      setOrgData(res.data);
      alert('Workable workflow settings saved.');
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to save Workable settings');
    } finally {
      setWorkableSaving(false);
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
      alert(err?.response?.data?.detail || 'Failed to invite team member');
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
      alert('Enterprise access controls updated.');
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to save enterprise settings');
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
        <p className="font-mono text-sm text-gray-600 mb-8">Manage integrations and billing</p>

        <div className="flex border-2 border-black mb-8">
          {['workable', 'billing', 'team', 'enterprise', 'preferences'].map((tab) => (
            <button
              key={tab}
              className={`flex-1 px-6 py-3 font-mono text-sm font-bold border-r-2 border-black last:border-r-0 transition-colors ${
                settingsTab === tab ? 'text-white' : 'bg-white hover:bg-gray-100'
              }`}
              style={settingsTab === tab ? { backgroundColor: '#9D00FF' } : {}}
              onClick={() => setSettingsTab(tab)}
            >
              {tab === 'workable' && 'Workable'}
              {tab === 'billing' && 'Billing'}
              {tab === 'team' && 'Team'}
              {tab === 'enterprise' && 'Enterprise'}
              {tab === 'preferences' && 'Preferences'}
            </button>
          ))}
        </div>

        {orgLoading ? (
          <div className="flex items-center justify-center py-16 gap-3">
            <Loader2 size={24} className="animate-spin" style={{ color: '#9D00FF' }} />
            <span className="font-mono text-sm text-gray-500">Loading settings...</span>
          </div>
        ) : (
          <>
            {settingsTab === 'workable' && (
              <div>
                <div className={`border-2 border-black p-6 mb-8 flex items-center justify-between gap-4 flex-wrap ${workableConnected ? 'bg-green-50' : 'bg-yellow-50'}`}>
                  <div className="flex items-center gap-4">
                    {workableConnected ? <CheckCircle size={24} className="text-green-600" /> : <AlertTriangle size={24} className="text-yellow-600" />}
                    <div>
                      <div className="font-bold text-lg">Status: {workableConnected ? 'Connected' : 'Not Connected'}</div>
                      <div className="font-mono text-sm text-gray-600">
                        {workableConnected ? 'Workable integration is active' : 'Connect your Workable account to sync candidates'}
                      </div>
                    </div>
                  </div>
                  {!workableConnected && ConnectWorkableButton ? <ConnectWorkableButton /> : null}
                </div>

                <div className="border-2 border-black p-6 space-y-4">
                  <div>
                    <div className="font-mono text-xs text-gray-500 mb-1">Organization</div>
                    <div className="font-bold">{orgName}</div>
                  </div>
                  <div>
                    <div className="font-mono text-xs text-gray-500 mb-1">Admin Email</div>
                    <div className="font-mono">{adminEmail}</div>
                  </div>
                  <div>
                    <div className="font-mono text-xs text-gray-500 mb-1">Connected Since</div>
                    <div className="font-mono">{workableConnected ? connectedSince : '—'}</div>
                  </div>
                  <div>
                    <div className="font-mono text-xs text-gray-500 mb-1">Last Sync</div>
                    <div className="font-mono">{lastSyncAt} ({lastSyncStatus})</div>
                  </div>
                  <hr className="border-black" />
                  <div>
                    <div className="font-bold mb-3">Workflow Settings</div>
                    <div className="grid md:grid-cols-2 gap-3">
                      <label className="block">
                        <span className="font-mono text-xs text-gray-500 mb-1 block">Workflow mode</span>
                        <select
                          className="w-full border-2 border-black px-3 py-2 font-mono text-sm"
                          value={workableForm.workflowMode}
                          onChange={(e) => setWorkableForm((prev) => ({ ...prev, workflowMode: e.target.value }))}
                        >
                          <option value="manual">Manual</option>
                          <option value="workable_hybrid">Workable hybrid</option>
                        </select>
                      </label>
                      <label className="block">
                        <span className="font-mono text-xs text-gray-500 mb-1 block">Email mode</span>
                        <select
                          className="w-full border-2 border-black px-3 py-2 font-mono text-sm"
                          value={workableForm.emailMode}
                          onChange={(e) => setWorkableForm((prev) => ({ ...prev, emailMode: e.target.value }))}
                        >
                          <option value="manual_taali">Manual via TAALI</option>
                          <option value="workable_preferred_fallback_manual">Hybrid with fallback</option>
                        </select>
                      </label>
                      <label className="block">
                        <span className="font-mono text-xs text-gray-500 mb-1 block">Sync interval (minutes)</span>
                        <input
                          type="number"
                          min={5}
                          max={1440}
                          className="w-full border-2 border-black px-3 py-2 font-mono text-sm"
                          value={workableForm.syncIntervalMinutes}
                          onChange={(e) => setWorkableForm((prev) => ({ ...prev, syncIntervalMinutes: e.target.value }))}
                        />
                      </label>
                      <label className="block">
                        <span className="font-mono text-xs text-gray-500 mb-1 block">Invite stage name</span>
                        <input
                          type="text"
                          className="w-full border-2 border-black px-3 py-2 font-mono text-sm"
                          value={workableForm.inviteStageName}
                          onChange={(e) => setWorkableForm((prev) => ({ ...prev, inviteStageName: e.target.value }))}
                        />
                      </label>
                    </div>
                    <div className="mt-4 flex flex-wrap gap-3">
                      <button
                        type="button"
                        disabled={workableSaving}
                        className="border-2 border-black px-4 py-2 font-mono text-sm font-bold text-white"
                        style={{ backgroundColor: '#9D00FF' }}
                        onClick={handleSaveWorkable}
                      >
                        {workableSaving ? 'Saving…' : 'Save Workable Settings'}
                      </button>
                      <button
                        type="button"
                        disabled={workableSyncLoading || !workableConnected}
                        className="border-2 border-black px-4 py-2 font-mono text-sm font-bold bg-black text-white disabled:opacity-60"
                        onClick={handleSyncWorkableNow}
                      >
                        {workableSyncLoading ? 'Syncing…' : 'Sync Now'}
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {settingsTab === 'billing' && (
              <div>
                <div className="border-2 border-black p-6 mb-8">
                  <div className="flex items-start justify-between flex-wrap gap-4">
                    <div>
                      <div className="font-mono text-xs text-gray-500 mb-1">Current Plan</div>
                      <div className="text-2xl font-bold">{billingPlan}</div>
                      <div className="font-mono text-sm text-gray-600 mt-1">Billing provider: Lemon</div>
                    </div>
                    <div className="text-right">
                      <div className="font-mono text-xs text-gray-500 mb-1">Total usage</div>
                      <div className="text-3xl font-bold" style={{ color: '#9D00FF' }}>{formatAed(monthlyCost)}</div>
                      <div className="font-mono text-xs text-gray-500">{monthlyAssessments} assessments</div>
                    </div>
                    <div className="text-right">
                      <div className="font-mono text-xs text-gray-500 mb-1">Credits balance</div>
                      <div className="text-3xl font-bold" style={{ color: '#9D00FF' }}>{creditsBalance}</div>
                    </div>
                  </div>
                  <div className="mt-5 grid md:grid-cols-3 gap-3">
                    {Object.entries(packCatalog).map(([packId, pack]) => (
                      <button
                        key={packId}
                        type="button"
                        onClick={() => handleAddCredits(packId)}
                        disabled={checkoutLoading}
                        className="flex items-center justify-between gap-2 px-4 py-3 font-mono text-sm font-bold border-2 border-black bg-black text-white hover:bg-gray-800 disabled:opacity-60 disabled:cursor-not-allowed"
                      >
                        <span>{pack.label || packId}</span>
                        <span className="inline-flex items-center gap-1">
                          {checkoutLoading ? <Loader2 size={14} className="animate-spin" /> : <CreditCard size={14} />}
                          +{pack.credits || 0}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>

                <div className="grid md:grid-cols-2 gap-4 mb-8">
                  <div className="border-2 border-black p-4 bg-white">
                    <div className="font-mono text-xs text-gray-500 mb-1">Daily spend threshold</div>
                    <div className="text-2xl font-bold">{formatAed(thresholdConfig.daily_spend_usd ?? 0, { maximumFractionDigits: 2 })}</div>
                    <div className={`font-mono text-xs mt-2 ${thresholdStatus.daily_spend_exceeded ? 'text-red-700' : 'text-green-700'}`}>
                      Today: {formatAed(Number(spendSummary.daily_spend_usd || 0), { maximumFractionDigits: 2 })} • {thresholdStatus.daily_spend_exceeded ? 'Exceeded' : 'Within threshold'}
                    </div>
                  </div>
                  <div className="border-2 border-black p-4 bg-white">
                    <div className="font-mono text-xs text-gray-500 mb-1">Cost / completed assessment threshold</div>
                    <div className="text-2xl font-bold">{formatAed(thresholdConfig.cost_per_completed_assessment_usd ?? 0, { maximumFractionDigits: 2 })}</div>
                    <div className={`font-mono text-xs mt-2 ${thresholdStatus.cost_per_completed_assessment_exceeded ? 'text-red-700' : 'text-green-700'}`}>
                      Current: {formatAed(Number(spendSummary.cost_per_completed_assessment_usd || 0), { maximumFractionDigits: 2 })} • {thresholdStatus.cost_per_completed_assessment_exceeded ? 'Exceeded' : 'Within threshold'}
                    </div>
                  </div>
                </div>

                <div className="border-2 border-black">
                  <div className="border-b-2 border-black px-6 py-4 bg-black text-white">
                    <h3 className="font-bold">Usage History</h3>
                  </div>
                  <table className="w-full">
                    <thead>
                      <tr className="border-b-2 border-black bg-gray-50">
                        <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Date</th>
                        <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Candidate</th>
                        <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Task</th>
                        <th className="text-right px-6 py-3 font-mono text-xs font-bold uppercase">Cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {usageHistory.length === 0 ? (
                        <tr>
                          <td colSpan={4} className="px-6 py-8 font-mono text-sm text-gray-500 text-center">
                            No usage yet. Completed assessments will appear here.
                          </td>
                        </tr>
                      ) : (
                        usageHistory.map((row, i) => (
                          <tr key={row.assessment_id ?? i} className="border-b border-gray-200 hover:bg-gray-50">
                            <td className="px-6 py-3 font-mono text-sm">{row.date}</td>
                            <td className="px-6 py-3 text-sm">{row.candidate}</td>
                            <td className="px-6 py-3 font-mono text-sm">{row.task}</td>
                            <td className="px-6 py-3 font-mono text-sm text-right font-bold">{toAedLabel(row.cost)}</td>
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
                <div className="border-2 border-black p-6">
                  <h3 className="text-xl font-bold mb-4">Invite Team Member</h3>
                  <form className="grid md:grid-cols-3 gap-3" onSubmit={handleInvite}>
                    <input
                      type="text"
                      className="border-2 border-black px-3 py-2 font-mono text-sm"
                      placeholder="Full name"
                      value={inviteName}
                      onChange={(e) => setInviteName(e.target.value)}
                    />
                    <input
                      type="email"
                      className="border-2 border-black px-3 py-2 font-mono text-sm"
                      placeholder="Email"
                      value={inviteEmail}
                      onChange={(e) => setInviteEmail(e.target.value)}
                    />
                    <button
                      type="submit"
                      disabled={inviteLoading}
                      className="border-2 border-black px-4 py-2 font-mono font-bold text-white"
                      style={{ backgroundColor: '#9D00FF' }}
                    >
                      {inviteLoading ? 'Inviting…' : 'Invite'}
                    </button>
                  </form>
                </div>
                <div className="border-2 border-black">
                  <div className="border-b-2 border-black px-6 py-4 bg-black text-white">
                    <h3 className="font-bold">Team Members</h3>
                  </div>
                  <table className="w-full">
                    <thead>
                      <tr className="border-b-2 border-black bg-gray-50">
                        <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Name</th>
                        <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Email</th>
                      </tr>
                    </thead>
                    <tbody>
                      {teamMembers.length === 0 ? (
                        <tr><td colSpan={2} className="px-6 py-8 font-mono text-sm text-gray-500 text-center">No members yet.</td></tr>
                      ) : teamMembers.map((m) => (
                        <tr key={m.id} className="border-b border-gray-200">
                          <td className="px-6 py-3">{m.full_name || '—'}</td>
                          <td className="px-6 py-3 font-mono text-sm">{m.email}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {settingsTab === 'enterprise' && (
              <div className="space-y-6">
                <div className="border-2 border-black p-6">
                  <h3 className="text-xl font-bold mb-4">Enterprise Access Controls</h3>
                  <div className="space-y-4">
                    <div>
                      <label className="font-mono text-xs text-gray-500 mb-1 block">Allowed email domains (comma separated)</label>
                      <input
                        type="text"
                        className="w-full border-2 border-black px-3 py-2 font-mono text-sm"
                        placeholder="acme.com, subsidiary.org"
                        value={enterpriseForm.allowedEmailDomains}
                        onChange={(e) => setEnterpriseForm((prev) => ({ ...prev, allowedEmailDomains: e.target.value }))}
                      />
                      <div className="font-mono text-xs text-gray-500 mt-1">
                        Leave empty to allow any domain.
                      </div>
                    </div>
                    <label className="flex items-center gap-3 cursor-pointer">
                      <input
                        type="checkbox"
                        className="w-4 h-4 accent-purple-600"
                        checked={enterpriseForm.ssoEnforced}
                        onChange={(e) => setEnterpriseForm((prev) => ({ ...prev, ssoEnforced: e.target.checked }))}
                      />
                      <span className="font-mono text-sm">Enforce SSO (blocks password login and invites)</span>
                    </label>
                    <label className="flex items-center gap-3 cursor-pointer">
                      <input
                        type="checkbox"
                        className="w-4 h-4 accent-purple-600"
                        checked={enterpriseForm.samlEnabled}
                        onChange={(e) => setEnterpriseForm((prev) => ({ ...prev, samlEnabled: e.target.checked }))}
                      />
                      <span className="font-mono text-sm">Enable SAML metadata configuration</span>
                    </label>
                    <div>
                      <label className="font-mono text-xs text-gray-500 mb-1 block">SAML metadata URL</label>
                      <input
                        type="url"
                        className="w-full border-2 border-black px-3 py-2 font-mono text-sm"
                        placeholder="https://idp.example.com/metadata.xml"
                        value={enterpriseForm.samlMetadataUrl}
                        onChange={(e) => setEnterpriseForm((prev) => ({ ...prev, samlMetadataUrl: e.target.value }))}
                      />
                    </div>
                    <button
                      type="button"
                      disabled={enterpriseSaving}
                      className="border-2 border-black px-4 py-2 font-mono text-sm font-bold text-white"
                      style={{ backgroundColor: '#9D00FF' }}
                      onClick={handleSaveEnterprise}
                    >
                      {enterpriseSaving ? 'Saving…' : 'Save enterprise settings'}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {settingsTab === 'preferences' && (
              <div className="border-2 border-black p-6">
                <h3 className="text-xl font-bold mb-4">Display Preferences</h3>
                <label className="flex items-center gap-3 font-mono text-sm">
                  <input
                    type="checkbox"
                    checked={darkMode}
                    onChange={(e) => setDarkMode(e.target.checked)}
                    className="w-4 h-4 accent-purple-600"
                  />
                  Enable dark mode
                </label>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};
