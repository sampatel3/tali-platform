import React, { useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { WorkablePanel } from '../../components/settings/workable/WorkablePanel';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { billing as billingApi, organizations as organizationsApi, team as teamApi } from '../../shared/api';
import { AppShell } from '../../shared/layout/TaaliLayout';

const sectionFromPath = (pathname) => {
  if (pathname.startsWith('/settings/team')) return 'members';
  if (pathname.startsWith('/settings/billing')) return 'billing';
  if (pathname.startsWith('/settings/workable')) return 'workable';
  if (pathname.startsWith('/settings/enterprise')) return 'sso';
  if (pathname.startsWith('/settings/preferences')) return 'notifications';
  return 'org';
};

const SETTINGS_NAV = [
  {
    label: 'Workspace',
    items: [
      { id: 'org', label: 'Organization', path: '/settings', anchor: 'org' },
      { id: 'scoring', label: 'Scoring policy', path: '/settings', anchor: 'scoring' },
      { id: 'ai', label: 'AI tooling', path: '/settings/preferences', anchor: 'ai' },
    ],
  },
  {
    label: 'People',
    items: [
      { id: 'members', label: 'Members', path: '/settings/team', anchor: 'members' },
      { id: 'roles', label: 'Roles & access', path: '/settings/team', anchor: 'roles' },
    ],
  },
  {
    label: 'Connected',
    items: [
      { id: 'workable', label: 'Workable', path: '/settings/workable', anchor: 'workable' },
      { id: 'sso', label: 'SSO / SAML', path: '/settings/enterprise', anchor: 'sso' },
      { id: 'api', label: 'API keys', path: '/settings/enterprise', anchor: 'api' },
    ],
  },
  {
    label: 'Account',
    items: [
      { id: 'billing', label: 'Billing', path: '/settings/billing', anchor: 'billing' },
      { id: 'notifications', label: 'Notifications', path: '/settings/preferences', anchor: 'notifications' },
    ],
  },
];

const initialScoringToggles = {
  promptQuality: true,
  errorRecovery: true,
  independence: true,
  contextUtilization: true,
  designThinking: true,
  timeToFirstSignal: false,
};

const SectionPanel = ({ id, title, subtitle, children }) => (
  <section
    id={id}
    className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] px-7 py-7 shadow-[var(--shadow-sm)]"
  >
    {title ? (
      <>
        <h2 className="font-[var(--font-display)] text-[26px] font-medium tracking-[-0.015em]">
          {title}
        </h2>
        {subtitle ? <p className="mt-1 text-[13.5px] text-[var(--mute)]">{subtitle}</p> : null}
      </>
    ) : null}
    <div className={title ? 'mt-5' : ''}>{children}</div>
  </section>
);

const ToggleRow = ({ title, body, checked, onChange, disabled = false }) => (
  <label className={`grid grid-cols-[1fr_auto] items-center gap-4 rounded-[12px] border border-[var(--line-2)] px-4 py-4 ${disabled ? 'opacity-60' : ''}`.trim()}>
    <div>
      <h4 className="text-[14px] font-semibold">{title}</h4>
      <p className="mt-1 text-[13px] leading-6 text-[var(--mute)]">{body}</p>
    </div>
    <span
      className={`relative inline-block h-[22px] w-[40px] rounded-full ${checked ? 'bg-[var(--purple)]' : 'bg-[var(--line)]'}`.trim()}
      onClick={(event) => {
        event.preventDefault();
        if (!disabled) onChange(!checked);
      }}
      aria-hidden="true"
    >
      <span
        className="absolute top-[2px] h-[18px] w-[18px] rounded-full bg-white shadow-[0_1px_2px_rgba(0,0,0,.15)] transition"
        style={{ left: checked ? 20 : 2 }}
      />
    </span>
  </label>
);

const SummaryStat = ({ label, value }) => (
  <div className="rounded-[14px] border border-[var(--line)] bg-[var(--bg)] px-5 py-5">
    <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">{label}</div>
    <div className="mt-3 font-[var(--font-display)] text-[34px] tracking-[-0.03em]">{value}</div>
  </div>
);

export const SettingsPage = ({ onNavigate }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const { showToast } = useToast();
  const activeSection = useMemo(
    () => location.hash.replace(/^#/, '') || sectionFromPath(location.pathname),
    [location.hash, location.pathname],
  );

  const [orgData, setOrgData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [teamMembers, setTeamMembers] = useState([]);
  const [billingUsage, setBillingUsage] = useState(null);
  const [billingCosts, setBillingCosts] = useState(null);
  const [billingCredits, setBillingCredits] = useState(null);
  const [saving, setSaving] = useState(false);
  const [checkoutLoading, setCheckoutLoading] = useState(false);
  const [inviteName, setInviteName] = useState('');
  const [inviteEmail, setInviteEmail] = useState('');
  const [workspaceForm, setWorkspaceForm] = useState({
    name: '',
    domain: '',
    candidateBrand: '',
    locale: 'English (US)',
  });
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
  const [scoringToggles, setScoringToggles] = useState(initialScoringToggles);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      setLoading(true);
      try {
        const [orgRes, teamRes, usageRes, costsRes, creditsRes] = await Promise.allSettled([
          organizationsApi.get(),
          teamApi.list(),
          billingApi.usage(),
          billingApi.costs(),
          billingApi.credits(),
        ]);
        if (cancelled) return;

        const nextOrg = orgRes.status === 'fulfilled' ? (orgRes.value?.data || null) : null;
        setOrgData(nextOrg);
        setWorkspaceForm({
          name: nextOrg?.name || '',
          domain: Array.isArray(nextOrg?.allowed_email_domains) ? nextOrg.allowed_email_domains[0] || '' : '',
          candidateBrand: nextOrg?.name || '',
          locale: 'English (US)',
        });
        setEnterpriseForm({
          allowedEmailDomains: Array.isArray(nextOrg?.allowed_email_domains) ? nextOrg.allowed_email_domains.join(', ') : '',
          ssoEnforced: Boolean(nextOrg?.sso_enforced),
          samlEnabled: Boolean(nextOrg?.saml_enabled),
          samlMetadataUrl: nextOrg?.saml_metadata_url || '',
          candidateFeedbackEnabled: nextOrg?.candidate_feedback_enabled !== false,
        });
        setPreferencesForm({
          defaultAssessmentDurationMinutes: Number(nextOrg?.default_assessment_duration_minutes || 30),
          inviteEmailTemplate: String(nextOrg?.invite_email_template || '').trim() || 'Hi {{candidate_name}}, your Taali assessment is ready: {{assessment_link}}',
          hasCustomClaudeApiKey: Boolean(nextOrg?.has_custom_claude_api_key),
        });

        setTeamMembers(teamRes.status === 'fulfilled' && Array.isArray(teamRes.value?.data) ? teamRes.value.data : []);
        setBillingUsage(usageRes.status === 'fulfilled' ? usageRes.value?.data || null : null);
        setBillingCosts(costsRes.status === 'fulfilled' ? costsRes.value?.data || null : null);
        setBillingCredits(creditsRes.status === 'fulfilled' ? creditsRes.value?.data || null : null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const targetId = location.hash.replace(/^#/, '') || sectionFromPath(location.pathname);
    if (!targetId) return undefined;
    const timer = window.setTimeout(() => {
      const node = document.getElementById(targetId);
      node?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 50);
    return () => window.clearTimeout(timer);
  }, [location.hash, location.pathname]);

  const billingStats = useMemo(() => ([
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
  ]), [billingCosts?.current_month_total_usd, billingCredits?.balance, billingCredits?.remaining_credits, billingUsage?.assessments_this_month, billingUsage?.assessments_used]);

  const navigateToSection = (item) => {
    navigate(`${item.path}#${item.anchor}`);
  };

  const saveWorkspace = async () => {
    setSaving(true);
    try {
      const response = await organizationsApi.update({
        name: workspaceForm.name,
        allowed_email_domains: workspaceForm.domain
          ? [workspaceForm.domain.trim()]
          : [],
      });
      setOrgData(response?.data || null);
      showToast('Organization updated.', 'success');
    } catch {
      showToast('Failed to update organization.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const saveEnterprise = async () => {
    setSaving(true);
    try {
      const response = await organizationsApi.update({
        allowed_email_domains: enterpriseForm.allowedEmailDomains
          .split(',')
          .map((value) => value.trim())
          .filter(Boolean),
        sso_enforced: enterpriseForm.ssoEnforced,
        saml_enabled: enterpriseForm.samlEnabled,
        saml_metadata_url: enterpriseForm.samlMetadataUrl || null,
        candidate_feedback_enabled: enterpriseForm.candidateFeedbackEnabled,
      });
      setOrgData(response?.data || null);
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
      const response = await organizationsApi.update({
        default_assessment_duration_minutes: Number(preferencesForm.defaultAssessmentDurationMinutes || 30),
        invite_email_template: preferencesForm.inviteEmailTemplate,
      });
      setOrgData(response?.data || null);
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
      const refreshed = await teamApi.list();
      setTeamMembers(Array.isArray(refreshed?.data) ? refreshed.data : []);
      setInviteName('');
      setInviteEmail('');
      showToast('Invite sent.', 'success');
    } catch {
      showToast('Failed to invite teammate.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const addCredits = async () => {
    setCheckoutLoading(true);
    try {
      const base = `${window.location.origin}/settings/billing`;
      const response = await billingApi.createCheckoutSession({
        success_url: `${base}?payment=success`,
        cancel_url: base,
      });
      if (response?.data?.url) {
        window.location.href = response.data.url;
      }
    } catch {
      showToast('Failed to start checkout.', 'error');
    } finally {
      setCheckoutLoading(false);
    }
  };

  const currentPlanLabel = orgData?.workable_connected ? 'Connected ATS workflow' : 'Manual recruiting workspace';

  return (
    <AppShell currentPage="settings" onNavigate={onNavigate}>
      <div className="page">
        <div className="page-head">
          <div className="tally-bg" />
          <div>
            <div className="kicker">04 · RECRUITER WORKSPACE</div>
            <h1>Settings<em>.</em></h1>
            <p className="sub">Workspace, scoring policy, integrations, and access.</p>
          </div>
        </div>

        {loading ? (
          <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] px-6 py-8 text-sm text-[var(--mute)]">
            Loading settings...
          </div>
        ) : (
          <div className="grid gap-5 lg:grid-cols-[260px_minmax(0,1fr)]">
            <aside className="sticky top-[88px] self-start rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-[18px] shadow-[var(--shadow-sm)]">
              {SETTINGS_NAV.map((group) => (
                <div key={group.label} className="mb-4 last:mb-0">
                  <div className="mb-2 px-[10px] font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">
                    {group.label}
                  </div>
                  <div className="space-y-1">
                    {group.items.map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        className={`block w-full rounded-[8px] px-[10px] py-[8px] text-left text-[13.5px] transition ${
                          activeSection === item.id
                            ? 'bg-[var(--ink)] text-[var(--bg)]'
                            : 'text-[var(--ink-2)] hover:bg-[var(--bg-3)]'
                        }`.trim()}
                        onClick={() => navigateToSection(item)}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </aside>

            <main className="flex flex-col gap-5">
              <SectionPanel id="org" title={<>Organization<em>.</em></>} subtitle="How your workspace shows up to candidates.">
                <div className="grid gap-4 md:grid-cols-2">
                  <label className="field">
                    <span className="k">Workspace name</span>
                    <input value={workspaceForm.name} onChange={(event) => setWorkspaceForm((current) => ({ ...current, name: event.target.value }))} />
                  </label>
                  <label className="field">
                    <span className="k">Domain</span>
                    <input value={workspaceForm.domain} onChange={(event) => setWorkspaceForm((current) => ({ ...current, domain: event.target.value }))} />
                  </label>
                  <label className="field">
                    <span className="k">Candidate-facing brand</span>
                    <input value={workspaceForm.candidateBrand} onChange={(event) => setWorkspaceForm((current) => ({ ...current, candidateBrand: event.target.value }))} />
                  </label>
                  <label className="field">
                    <span className="k">Locale</span>
                    <input value={workspaceForm.locale} onChange={(event) => setWorkspaceForm((current) => ({ ...current, locale: event.target.value }))} />
                  </label>
                </div>
                <div className="row mt-5 justify-between">
                  <div className="text-[12.5px] text-[var(--mute)]">Current workspace mode: {currentPlanLabel}</div>
                  <button type="button" className="btn btn-purple btn-sm" onClick={saveWorkspace} disabled={saving}>
                    {saving ? 'Saving...' : 'Save organization'}
                  </button>
                </div>
              </SectionPanel>

              <SectionPanel id="scoring" title={<>Scoring <em>policy</em></>} subtitle="Turn dimensions on or off for this workspace. Changes apply to assessments created after today.">
                <div className="space-y-3">
                  <ToggleRow title="Prompt quality" body="Reward scoped, single-decision prompts. Penalize vague requests." checked={scoringToggles.promptQuality} onChange={(value) => setScoringToggles((current) => ({ ...current, promptQuality: value }))} />
                  <ToggleRow title="Error recovery" body="Credit candidates who flag or reject incorrect AI output." checked={scoringToggles.errorRecovery} onChange={(value) => setScoringToggles((current) => ({ ...current, errorRecovery: value }))} />
                  <ToggleRow title="Independence" body="Measure which parts of the final code were written by the human vs. the model." checked={scoringToggles.independence} onChange={(value) => setScoringToggles((current) => ({ ...current, independence: value }))} />
                  <ToggleRow title="Context utilization" body="Track how AI suggestions are reviewed before being accepted." checked={scoringToggles.contextUtilization} onChange={(value) => setScoringToggles((current) => ({ ...current, contextUtilization: value }))} />
                  <ToggleRow title="Design thinking" body="Credit decisions that connect the fix to its blast radius across the system." checked={scoringToggles.designThinking} onChange={(value) => setScoringToggles((current) => ({ ...current, designThinking: value }))} />
                  <ToggleRow title="Time-to-first-signal" body="Include this in the composite score for leveling-sensitive roles." checked={scoringToggles.timeToFirstSignal} onChange={(value) => setScoringToggles((current) => ({ ...current, timeToFirstSignal: value }))} />
                </div>
              </SectionPanel>

              <SectionPanel id="ai" title={<>AI <em>tooling</em></>} subtitle="What candidates can use inside the runtime and what recruiters see in reports.">
                <div className="space-y-3">
                  <ToggleRow title="Claude CLI + chat" body="Enabled across assessment sessions. Default AI tool for scoring-aware reviews." checked onChange={() => {}} disabled />
                  <ToggleRow title="Cursor / Copilot style tools" body="Disabled by default until workspace-level tooling presets are expanded." checked={false} onChange={() => {}} disabled />
                  <ToggleRow title="Candidate feedback" body="Show the candidate-facing summary after review is finalized." checked={enterpriseForm.candidateFeedbackEnabled} onChange={(value) => setEnterpriseForm((current) => ({ ...current, candidateFeedbackEnabled: value }))} />
                </div>
              </SectionPanel>

              <SectionPanel id="members" title={<>Members<em>.</em></>} subtitle="Invite recruiters, hiring managers, and reviewers.">
                <div className="grid gap-5 lg:grid-cols-[1.15fr_.85fr]">
                  <div className="space-y-3">
                    {teamMembers.length === 0 ? (
                      <div className="rounded-[12px] border border-[var(--line-2)] px-4 py-5 text-sm text-[var(--mute)]">
                        No teammates added yet.
                      </div>
                    ) : teamMembers.map((member) => (
                      <div key={member.id || member.email} className="grid grid-cols-[40px_1fr_auto_auto] items-center gap-3 rounded-[12px] border border-[var(--line-2)] px-4 py-3">
                        <div className="grid h-9 w-9 place-items-center rounded-full bg-[var(--purple-soft)] text-[13px] font-semibold text-[var(--purple)]">
                          {String(member.full_name || member.email || 'TA').slice(0, 2).toUpperCase()}
                        </div>
                        <div>
                          <div className="text-[14px] font-semibold">{member.full_name || member.email}</div>
                          <div className="font-[var(--font-mono)] text-[11.5px] text-[var(--mute)]">{member.email}</div>
                        </div>
                        <span className="chip">{member.role || 'member'}</span>
                        <span className="font-[var(--font-mono)] text-[11px] text-[var(--mute)]">active</span>
                      </div>
                    ))}
                  </div>

                  <div className="rounded-[12px] border border-[var(--line-2)] p-5">
                    <div className="kicker mb-2">Invite member</div>
                    <div className="space-y-4">
                      <label className="field">
                        <span className="k">Full name</span>
                        <input value={inviteName} onChange={(event) => setInviteName(event.target.value)} placeholder="Alex Weston" />
                      </label>
                      <label className="field">
                        <span className="k">Email</span>
                        <input type="email" value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} placeholder="alex@company.com" />
                      </label>
                      <button type="button" className="btn btn-purple btn-sm" onClick={inviteMember} disabled={saving}>
                        {saving ? 'Sending...' : 'Send invite'}
                      </button>
                    </div>
                  </div>
                </div>
              </SectionPanel>

              <SectionPanel id="roles" title={<>Roles & <em>access</em></>} subtitle="Workspace permissions for recruiters and hiring managers.">
                <div className="grid gap-3 md:grid-cols-3">
                  <div className="rounded-[12px] border border-[var(--line-2)] p-4">
                    <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Owners</div>
                    <p className="mt-2 text-[13px] leading-6 text-[var(--ink-2)]">Full billing, integrations, and member management access.</p>
                  </div>
                  <div className="rounded-[12px] border border-[var(--line-2)] p-4">
                    <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Admins</div>
                    <p className="mt-2 text-[13px] leading-6 text-[var(--ink-2)]">Can manage pipeline settings, Workable, and recruiter-facing workflows.</p>
                  </div>
                  <div className="rounded-[12px] border border-[var(--line-2)] p-4">
                    <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Members</div>
                    <p className="mt-2 text-[13px] leading-6 text-[var(--ink-2)]">Can review candidates and work inside assigned recruiting surfaces.</p>
                  </div>
                </div>
              </SectionPanel>

              <SectionPanel id="workable" title={null} subtitle={null}>
                <WorkablePanel
                  orgData={orgData}
                  onOrgDataChange={setOrgData}
                  currentUser={user}
                  active={activeSection === 'workable'}
                />
              </SectionPanel>

              <SectionPanel id="sso" title={<>SSO / <em>SAML</em></>} subtitle="Identity and domain controls for enterprise workspaces.">
                <div className="grid gap-4 md:grid-cols-2">
                  <label className="field">
                    <span className="k">Allowed email domains</span>
                    <input
                      value={enterpriseForm.allowedEmailDomains}
                      onChange={(event) => setEnterpriseForm((current) => ({ ...current, allowedEmailDomains: event.target.value }))}
                      placeholder="company.com, subsidiary.com"
                    />
                  </label>
                  <label className="field">
                    <span className="k">SAML metadata URL</span>
                    <input
                      value={enterpriseForm.samlMetadataUrl}
                      onChange={(event) => setEnterpriseForm((current) => ({ ...current, samlMetadataUrl: event.target.value }))}
                      placeholder="https://idp.example.com/metadata"
                    />
                  </label>
                </div>
                <div className="mt-5 grid gap-3 md:grid-cols-3">
                  <ToggleRow title="Enforce SSO" body="Require members to sign in through the workspace identity provider." checked={enterpriseForm.ssoEnforced} onChange={(value) => setEnterpriseForm((current) => ({ ...current, ssoEnforced: value }))} />
                  <ToggleRow title="Enable SAML" body="Turn on SAML metadata validation for enterprise logins." checked={enterpriseForm.samlEnabled} onChange={(value) => setEnterpriseForm((current) => ({ ...current, samlEnabled: value }))} />
                  <ToggleRow title="Candidate feedback" body="Allow candidates to see their finalized summary after review." checked={enterpriseForm.candidateFeedbackEnabled} onChange={(value) => setEnterpriseForm((current) => ({ ...current, candidateFeedbackEnabled: value }))} />
                </div>
                <div className="mt-5 flex justify-end">
                  <button type="button" className="btn btn-purple btn-sm" onClick={saveEnterprise} disabled={saving}>
                    {saving ? 'Saving...' : 'Save enterprise settings'}
                  </button>
                </div>
              </SectionPanel>

              <SectionPanel id="api" title={<>API <em>keys</em></>} subtitle="Workspace secrets and model configuration.">
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="rounded-[12px] border border-[var(--line-2)] p-5">
                    <div className="kicker mb-2">Claude API key</div>
                    <div className="text-[16px] font-semibold">{preferencesForm.hasCustomClaudeApiKey ? 'Configured' : 'Not configured'}</div>
                    <p className="mt-2 text-[13px] leading-6 text-[var(--mute)]">Recruiter runtime sessions use the workspace default unless a custom key is rotated here.</p>
                  </div>
                  <div className="rounded-[12px] border border-[var(--line-2)] p-5">
                    <div className="kicker mb-2">Webhook posture</div>
                    <div className="text-[16px] font-semibold">{orgData?.workable_connected ? 'Workable webhooks enabled' : 'No ATS webhooks connected'}</div>
                    <p className="mt-2 text-[13px] leading-6 text-[var(--mute)]">Keep secrets in Vercel and Railway, not inside the browser workspace.</p>
                  </div>
                </div>
              </SectionPanel>

              <SectionPanel id="billing" title={<>Billing<em>.</em></>} subtitle="Usage, credits, and workspace spend.">
                <div className="grid gap-4 md:grid-cols-3">
                  {billingStats.map((stat) => (
                    <SummaryStat key={stat.label} label={stat.label} value={stat.value} />
                  ))}
                </div>
                <div className="mt-5 flex justify-end">
                  <button type="button" className="btn btn-purple btn-sm" onClick={addCredits} disabled={checkoutLoading}>
                    {checkoutLoading ? 'Opening checkout...' : 'Add credits'}
                  </button>
                </div>
              </SectionPanel>

              <SectionPanel id="notifications" title={<>Notifications<em>.</em></>} subtitle="Invite defaults and recruiter-side communication templates.">
                <div className="grid gap-4 md:grid-cols-2">
                  <label className="field">
                    <span className="k">Default assessment minutes</span>
                    <input
                      type="number"
                      min="15"
                      max="180"
                      value={preferencesForm.defaultAssessmentDurationMinutes}
                      onChange={(event) => setPreferencesForm((current) => ({ ...current, defaultAssessmentDurationMinutes: event.target.value }))}
                    />
                  </label>
                  <div className="rounded-[12px] border border-[var(--line-2)] p-5">
                    <div className="kicker mb-2">Notification mode</div>
                    <div className="text-[16px] font-semibold">Recruiter inbox + email</div>
                    <p className="mt-2 text-[13px] leading-6 text-[var(--mute)]">Live candidate events appear in-product first, with email fallback for high-signal state changes.</p>
                  </div>
                </div>
                <label className="field mt-4">
                  <span className="k">Invite email template</span>
                  <textarea
                    className="min-h-[150px]"
                    value={preferencesForm.inviteEmailTemplate}
                    onChange={(event) => setPreferencesForm((current) => ({ ...current, inviteEmailTemplate: event.target.value }))}
                  />
                </label>
                <div className="mt-5 flex justify-end">
                  <button type="button" className="btn btn-purple btn-sm" onClick={savePreferences} disabled={saving}>
                    {saving ? 'Saving...' : 'Save preferences'}
                  </button>
                </div>
              </SectionPanel>
            </main>
          </div>
        )}
      </div>
    </AppShell>
  );
};

export default SettingsPage;
