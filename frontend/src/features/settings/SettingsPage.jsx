import React, { useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { useToast } from '../../context/ToastContext';
import { billing as billingApi, organizations as orgsApi, team as teamApi } from '../../shared/api';
import { AppShell } from '../../shared/layout/TaaliLayout';

const SETTINGS_SECTIONS = [
  { id: 'overview', label: 'Workspace', path: '/settings' },
  { id: 'team', label: 'Team', path: '/settings/team' },
  { id: 'billing', label: 'Billing', path: '/settings/billing' },
  { id: 'workable', label: 'Workable', path: '/settings/workable' },
  { id: 'enterprise', label: 'Enterprise', path: '/settings/enterprise' },
  { id: 'preferences', label: 'Preferences', path: '/settings/preferences' },
];

const sectionFromPath = (pathname) => {
  const segment = pathname.replace(/^\/settings\/?/, '').split('/')[0];
  return SETTINGS_SECTIONS.find((section) => section.id === segment)?.id || 'overview';
};

const normalizeWorkableError = (input) => {
  const raw = String(input || '').trim();
  if (!raw) return 'Workable connection failed.';
  return raw;
};

const SettingsCard = ({ title, subtitle, children }) => (
  <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
    <h3 className="font-[var(--font-display)] text-[22px] font-semibold tracking-[-0.02em]">{title}</h3>
    {subtitle ? <p className="mt-1 text-[12.5px] text-[var(--mute)]">{subtitle}</p> : null}
    <div className="mt-5">{children}</div>
  </div>
);

const Field = ({ label, children, hint = null }) => (
  <label className="field">
    <span className="k">{label}</span>
    {children}
    {hint ? <span className="mt-1 block text-[11.5px] text-[var(--mute)]">{hint}</span> : null}
  </label>
);

export const SettingsPage = ({ onNavigate, ConnectWorkableButton }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const currentSection = sectionFromPath(location.pathname);

  const [orgData, setOrgData] = useState(null);
  const [loadingOrg, setLoadingOrg] = useState(true);
  const [billingUsage, setBillingUsage] = useState(null);
  const [billingCosts, setBillingCosts] = useState(null);
  const [billingCredits, setBillingCredits] = useState(null);
  const [teamMembers, setTeamMembers] = useState([]);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteName, setInviteName] = useState('');
  const [loadingSection, setLoadingSection] = useState(false);
  const [saving, setSaving] = useState(false);
  const [enterpriseForm, setEnterpriseForm] = useState({
    allowedEmailDomains: '',
    ssoEnforced: false,
    samlEnabled: false,
    samlMetadataUrl: '',
    candidateFeedbackEnabled: true,
  });
  const [preferencesForm, setPreferencesForm] = useState({
    defaultAssessmentDurationMinutes: 30,
    inviteEmailTemplate: '',
    hasCustomClaudeApiKey: false,
  });
  const [workspaceForm, setWorkspaceForm] = useState({
    name: '',
    workableSubdomain: '',
  });
  const [workableError, setWorkableError] = useState('');
  const [workableSyncing, setWorkableSyncing] = useState(false);
  const [checkoutLoading, setCheckoutLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const loadOrg = async () => {
      setLoadingOrg(true);
      try {
        const res = await orgsApi.get();
        if (cancelled) return;
        const data = res?.data || {};
        setOrgData(data);
        setWorkspaceForm({
          name: data?.name || '',
          workableSubdomain: data?.workable_subdomain || '',
        });
        setEnterpriseForm({
          allowedEmailDomains: Array.isArray(data?.allowed_email_domains) ? data.allowed_email_domains.join(', ') : '',
          ssoEnforced: Boolean(data?.sso_enforced),
          samlEnabled: Boolean(data?.saml_enabled),
          samlMetadataUrl: data?.saml_metadata_url || '',
          candidateFeedbackEnabled: data?.candidate_feedback_enabled !== false,
        });
        setPreferencesForm({
          defaultAssessmentDurationMinutes: Number(data?.default_assessment_duration_minutes || 30),
          inviteEmailTemplate: String(data?.invite_email_template || '').trim() || 'Hi {{candidate_name}}, your TAALI assessment is ready: {{assessment_link}}',
          hasCustomClaudeApiKey: Boolean(data?.has_custom_claude_api_key),
        });
      } catch {
        if (!cancelled) {
          setOrgData(null);
        }
      } finally {
        if (!cancelled) setLoadingOrg(false);
      }
    };
    void loadOrg();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadSectionData = async () => {
      if (currentSection === 'billing') {
        setLoadingSection(true);
        try {
          const [usageRes, costsRes, creditsRes] = await Promise.all([
            billingApi.usage(),
            billingApi.costs(),
            billingApi.credits(),
          ]);
          if (cancelled) return;
          setBillingUsage(usageRes?.data || null);
          setBillingCosts(costsRes?.data || null);
          setBillingCredits(creditsRes?.data || null);
        } catch {
          if (cancelled) return;
          setBillingUsage(null);
          setBillingCosts(null);
          setBillingCredits(null);
        } finally {
          if (!cancelled) setLoadingSection(false);
        }
        return;
      }

      if (currentSection === 'team') {
        setLoadingSection(true);
        try {
          const res = await teamApi.list();
          if (!cancelled) setTeamMembers(Array.isArray(res?.data) ? res.data : []);
        } catch {
          if (!cancelled) setTeamMembers([]);
        } finally {
          if (!cancelled) setLoadingSection(false);
        }
        return;
      }

      setLoadingSection(false);
    };
    void loadSectionData();
    return () => {
      cancelled = true;
    };
  }, [currentSection]);

  const billingStats = useMemo(() => [
    {
      label: 'Assessments this month',
      value: billingUsage?.assessments_this_month ?? billingUsage?.assessments_used ?? 0,
    },
    {
      label: 'Credits remaining',
      value: billingCredits?.remaining_credits ?? billingCredits?.balance ?? 0,
    },
    {
      label: 'Spend this month',
      value: billingCosts?.current_month_total_usd != null ? `$${Number(billingCosts.current_month_total_usd).toFixed(2)}` : '$0.00',
    },
  ], [billingCosts?.current_month_total_usd, billingCredits?.balance, billingCredits?.remaining_credits, billingUsage?.assessments_this_month, billingUsage?.assessments_used]);

  const saveWorkspace = async () => {
    setSaving(true);
    try {
      await orgsApi.update({
        name: workspaceForm.name,
        workable_subdomain: workspaceForm.workableSubdomain || null,
      });
      showToast('Workspace updated.', 'success');
    } catch {
      showToast('Failed to update workspace.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const saveEnterprise = async () => {
    setSaving(true);
    try {
      await orgsApi.update({
        allowed_email_domains: enterpriseForm.allowedEmailDomains.split(',').map((value) => value.trim()).filter(Boolean),
        sso_enforced: enterpriseForm.ssoEnforced,
        saml_enabled: enterpriseForm.samlEnabled,
        saml_metadata_url: enterpriseForm.samlMetadataUrl || null,
        candidate_feedback_enabled: enterpriseForm.candidateFeedbackEnabled,
      });
      showToast('Enterprise settings updated.', 'success');
    } catch {
      showToast('Failed to update enterprise settings.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const savePreferences = async () => {
    setSaving(true);
    try {
      await orgsApi.update({
        default_assessment_duration_minutes: Number(preferencesForm.defaultAssessmentDurationMinutes || 30),
        invite_email_template: preferencesForm.inviteEmailTemplate,
      });
      showToast('Preferences updated.', 'success');
    } catch {
      showToast('Failed to update preferences.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const inviteMember = async () => {
    if (!inviteEmail.trim()) return;
    setSaving(true);
    try {
      await teamApi.invite({
        email: inviteEmail.trim(),
        full_name: inviteName.trim() || undefined,
      });
      showToast('Invite sent.', 'success');
      setInviteEmail('');
      setInviteName('');
      const res = await teamApi.list();
      setTeamMembers(Array.isArray(res?.data) ? res.data : []);
    } catch {
      showToast('Failed to invite teammate.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const connectWorkable = async () => {
    setWorkableSyncing(true);
    setWorkableError('');
    try {
      const res = await orgsApi.getWorkableAuthorizeUrl();
      const url = res?.data?.authorize_url || res?.data?.url;
      if (url) {
        window.location.href = url;
        return;
      }
      setWorkableError('Workable authorize URL is unavailable.');
    } catch (err) {
      setWorkableError(normalizeWorkableError(err?.response?.data?.detail || err?.message));
    } finally {
      setWorkableSyncing(false);
    }
  };

  const addCredits = async () => {
    setCheckoutLoading(true);
    try {
      const base = `${window.location.origin}/settings/billing`;
      const res = await billingApi.createCheckoutSession({
        success_url: `${base}?payment=success`,
        cancel_url: base,
      });
      if (res?.data?.url) {
        window.location.href = res.data.url;
      }
    } catch {
      showToast('Failed to start checkout.', 'error');
    } finally {
      setCheckoutLoading(false);
    }
  };

  const renderCurrentSection = () => {
    if (loadingOrg) {
      return <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-8 text-sm text-[var(--mute)]">Loading settings…</div>;
    }

    if (currentSection === 'overview') {
      return (
        <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
          <SettingsCard title={<>Workspace <em>details</em>.</>} subtitle="Brand, organization, and integration defaults.">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Workspace name">
                <input value={workspaceForm.name} onChange={(event) => setWorkspaceForm((prev) => ({ ...prev, name: event.target.value }))} />
              </Field>
              <Field label="Workable subdomain">
                <input value={workspaceForm.workableSubdomain} onChange={(event) => setWorkspaceForm((prev) => ({ ...prev, workableSubdomain: event.target.value }))} />
              </Field>
            </div>
            <button type="button" className="btn btn-purple btn-sm mt-5" onClick={saveWorkspace} disabled={saving}>Save workspace</button>
          </SettingsCard>
          <SettingsCard title={<>Workspace <em>summary</em>.</>} subtitle="Quick reference for the current setup.">
            <div className="space-y-3 text-sm">
              <div className="flex justify-between border-b border-[var(--line-2)] pb-3"><span className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Org</span><span>{orgData?.name || 'Taali'}</span></div>
              <div className="flex justify-between border-b border-[var(--line-2)] pb-3"><span className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Feedback</span><span>{orgData?.candidate_feedback_enabled === false ? 'Disabled' : 'Enabled'}</span></div>
              <div className="flex justify-between"><span className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Workable</span><span>{orgData?.workable_subdomain || 'Not connected'}</span></div>
            </div>
          </SettingsCard>
        </div>
      );
    }

    if (currentSection === 'team') {
      return (
        <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
          <SettingsCard title={<>Team <em>members</em>.</>} subtitle="Invite recruiters, hiring managers, and reviewers.">
            {loadingSection ? (
              <div className="text-sm text-[var(--mute)]">Loading team…</div>
            ) : (
              <div className="space-y-3">
                {teamMembers.length === 0 ? (
                  <div className="text-sm text-[var(--mute)]">No teammates added yet.</div>
                ) : teamMembers.map((member) => (
                  <div key={member.id || member.email} className="flex items-center justify-between rounded-[12px] border border-[var(--line-2)] px-4 py-3">
                    <div>
                      <div className="text-sm font-medium">{member.full_name || member.email}</div>
                      <div className="mt-1 text-[11.5px] text-[var(--mute)]">{member.email}</div>
                    </div>
                    <span className="chip">{member.role || 'member'}</span>
                  </div>
                ))}
              </div>
            )}
          </SettingsCard>
          <SettingsCard title={<>Invite <em>member</em>.</>} subtitle="Add another teammate to the hiring workspace.">
            <div className="space-y-4">
              <Field label="Full name">
                <input value={inviteName} onChange={(event) => setInviteName(event.target.value)} placeholder="Alex Weston" />
              </Field>
              <Field label="Email">
                <input type="email" value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} placeholder="alex@company.com" />
              </Field>
            </div>
            <button type="button" className="btn btn-purple btn-sm mt-5" onClick={inviteMember} disabled={saving}>Send invite</button>
          </SettingsCard>
        </div>
      );
    }

    if (currentSection === 'billing') {
      return (
        <div className="space-y-5">
          <div className="grid gap-4 md:grid-cols-3">
            {billingStats.map((stat) => (
              <div key={stat.label} className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] px-6 py-6 shadow-[var(--shadow-sm)]">
                <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">{stat.label}</div>
                <div className="mt-3 font-[var(--font-display)] text-[40px] tracking-[-0.03em]">{stat.value}</div>
              </div>
            ))}
          </div>
          <SettingsCard title={<>Billing <em>controls</em>.</>} subtitle="Review usage and top up credits when needed.">
            {loadingSection ? <div className="text-sm text-[var(--mute)]">Loading billing…</div> : null}
            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-[12px] border border-[var(--line-2)] p-4 text-sm text-[var(--ink-2)]">
                Current month: {billingCosts?.current_month_total_usd != null ? `$${Number(billingCosts.current_month_total_usd).toFixed(2)}` : '$0.00'}
              </div>
              <div className="rounded-[12px] border border-[var(--line-2)] p-4 text-sm text-[var(--ink-2)]">
                Credits available: {billingCredits?.remaining_credits ?? billingCredits?.balance ?? 0}
              </div>
            </div>
            <button type="button" className="btn btn-purple btn-sm mt-5" onClick={addCredits} disabled={checkoutLoading}>
              {checkoutLoading ? 'Opening checkout…' : 'Add credits'}
            </button>
          </SettingsCard>
        </div>
      );
    }

    if (currentSection === 'workable') {
      return (
        <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
          <SettingsCard title={<>Workable <em>connection</em>.</>} subtitle="Connect the ATS, import jobs, and keep the pipeline aligned.">
            <div className="rounded-[12px] border border-[var(--line-2)] p-4 text-sm text-[var(--ink-2)]">
              Current status: {orgData?.workable_subdomain ? `Connected to ${orgData.workable_subdomain}` : 'Not connected'}
            </div>
            {workableError ? <div className="mt-4 rounded-[12px] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">{workableError}</div> : null}
            <div className="mt-5 flex flex-wrap gap-3">
              <button type="button" className="btn btn-purple btn-sm" onClick={connectWorkable} disabled={workableSyncing}>
                {workableSyncing ? 'Connecting…' : 'Connect Workable'}
              </button>
              {ConnectWorkableButton ? <ConnectWorkableButton onNavigate={onNavigate} /> : null}
            </div>
          </SettingsCard>
          <SettingsCard title={<>Why it <em>matters</em>.</>} subtitle="Sync candidate stages and remove manual handoffs.">
            <ul className="space-y-3 text-sm text-[var(--ink-2)]">
              <li>Import open roles into the recruiter workspace.</li>
              <li>Invite candidates into Taali from the ATS loop.</li>
              <li>Keep recruiter state and review decisions aligned.</li>
            </ul>
          </SettingsCard>
        </div>
      );
    }

    if (currentSection === 'enterprise') {
      return (
        <SettingsCard title={<>Enterprise <em>controls</em>.</>} subtitle="SSO, domains, and org-wide feedback policy.">
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Allowed email domains">
              <input value={enterpriseForm.allowedEmailDomains} onChange={(event) => setEnterpriseForm((prev) => ({ ...prev, allowedEmailDomains: event.target.value }))} placeholder="company.com, subsidiary.com" />
            </Field>
            <Field label="SAML metadata URL">
              <input value={enterpriseForm.samlMetadataUrl} onChange={(event) => setEnterpriseForm((prev) => ({ ...prev, samlMetadataUrl: event.target.value }))} placeholder="https://idp.example.com/metadata" />
            </Field>
          </div>
          <div className="mt-5 grid gap-3 md:grid-cols-3">
            {[
              ['Enforce SSO', enterpriseForm.ssoEnforced, 'ssoEnforced'],
              ['Enable SAML', enterpriseForm.samlEnabled, 'samlEnabled'],
              ['Candidate feedback', enterpriseForm.candidateFeedbackEnabled, 'candidateFeedbackEnabled'],
            ].map(([label, checked, key]) => (
              <label key={label} className="flex items-center justify-between rounded-[12px] border border-[var(--line-2)] px-4 py-3 text-sm">
                <span>{label}</span>
                <input
                  type="checkbox"
                  checked={Boolean(checked)}
                  onChange={(event) => setEnterpriseForm((prev) => ({ ...prev, [key]: event.target.checked }))}
                />
              </label>
            ))}
          </div>
          <button type="button" className="btn btn-purple btn-sm mt-5" onClick={saveEnterprise} disabled={saving}>Save enterprise settings</button>
        </SettingsCard>
      );
    }

    return (
      <SettingsCard title={<>Preferences <em>defaults</em>.</>} subtitle="Assessment duration, invite copy, and recruiter-side defaults.">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Default assessment minutes">
            <input
              type="number"
              min="15"
              max="180"
              value={preferencesForm.defaultAssessmentDurationMinutes}
              onChange={(event) => setPreferencesForm((prev) => ({ ...prev, defaultAssessmentDurationMinutes: event.target.value }))}
            />
          </Field>
          <Field label="Custom Claude key" hint={preferencesForm.hasCustomClaudeApiKey ? 'A custom key is already configured.' : 'No custom key configured.'}>
            <input value={preferencesForm.hasCustomClaudeApiKey ? 'Configured' : 'Not configured'} readOnly />
          </Field>
        </div>
        <Field label="Invite email template">
          <textarea
            className="min-h-[140px]"
            value={preferencesForm.inviteEmailTemplate}
            onChange={(event) => setPreferencesForm((prev) => ({ ...prev, inviteEmailTemplate: event.target.value }))}
          />
        </Field>
        <button type="button" className="btn btn-purple btn-sm mt-5" onClick={savePreferences} disabled={saving}>Save preferences</button>
      </SettingsCard>
    );
  };

  return (
    <AppShell currentPage="settings" onNavigate={onNavigate}>
      <div className="page">
        <div className="page-head">
          <div className="tally-bg" />
          <div>
            <div className="kicker">04 · RECRUITER WORKSPACE</div>
            <h1>Settings<em>.</em></h1>
            <p className="sub">Workspace controls, billing, integrations, and the defaults recruiters use every day.</p>
          </div>
        </div>

        <div className="mb-5 flex flex-wrap gap-2">
          {SETTINGS_SECTIONS.map((section) => (
            <button
              key={section.id}
              type="button"
              className={`btn ${currentSection === section.id ? 'btn-primary' : 'btn-outline'} btn-sm`.trim()}
              onClick={() => navigate(section.path)}
            >
              {section.label}
            </button>
          ))}
        </div>

        {renderCurrentSection()}
      </div>
    </AppShell>
  );
};

export default SettingsPage;
