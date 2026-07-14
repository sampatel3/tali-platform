import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

vi.mock('../../shared/api', () => ({
  organizations: {
    get: vi.fn(),
    update: vi.fn(),
    getWorkableAuthorizeUrl: vi.fn(),
    connectWorkableToken: vi.fn(),
    getWorkableSyncStatus: vi.fn(),
    getWorkableSyncJobs: vi.fn(),
    getWorkableMembers: vi.fn(),
    getWorkableDisqualificationReasons: vi.fn(),
    getWorkableStages: vi.fn(),
    syncWorkable: vi.fn(),
    cancelWorkableSync: vi.fn(),
    clearWorkableData: vi.fn(),
    listCriteria: vi.fn(),
    createCriterion: vi.fn(),
    updateCriterion: vi.fn(),
    deleteCriterion: vi.fn(),
  },
  billing: {
    usage: vi.fn(),
    costs: vi.fn(),
    credits: vi.fn(),
    usageBreakdown: vi.fn(),
    usageEvents: vi.fn(),
    createCheckoutSession: vi.fn(),
  },
  team: {
    list: vi.fn(),
    invite: vi.fn(),
    setRole: vi.fn(),
    resendInvite: vi.fn(),
    inviteLink: vi.fn(),
    remove: vi.fn(),
  },
}));

const showToast = vi.fn();

// Mutable so individual tests can flip the signed-in user between owner and
// member; beforeEach resets it to the owner default.
const authState = vi.hoisted(() => ({ user: null }));

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: authState.user }),
}));

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast }),
}));

import { billing as billingApi, organizations as orgsApi, team as teamApi } from '../../shared/api';
import { SettingsPage } from './SettingsPage';

const baseOrgData = {
  id: 1,
  name: 'DeepLight AI',
  plan: 'pay_per_use',
  default_assessment_duration_minutes: 30,
  invite_email_template: 'Hi {{candidate_name}}, your TAALI assessment is ready: {{assessment_link}}',
  allowed_email_domains: [],
  sso_enforced: false,
  saml_enabled: false,
  saml_metadata_url: '',
  workable_connected: false,
  workable_mode: 'read_only',
  workspace_settings: {
    candidate_facing_brand: 'DeepLight · Engineering',
    primary_domain: 'deeplight.ai',
    locale: 'English (US)',
  },
  // Settings redesign — replaces the old scoring/ai-tooling configuration
  // surface. Tests below assert this is the only place in the UI where
  // these three workspace defaults can be set.
  default_role_requirements: ['5+ years backend', 'Strong SQL'],
  default_role_budget_cents: 20000,
  default_score_threshold: 70,
  ai_tooling_config: {
    provider_setting: 'keep-me',
    agent_defaults: {
      enabled: false,
      budget_cents: 20000,
      threshold_mode: 'manual',
      auto_send_assessment: false,
      auto_resend_assessment: true,
      auto_advance: false,
      auto_reject_pre_screen: false,
      auto_skip_assessment: false,
      agent_token_budget_per_cycle: 12000,
    },
  },
  monthly_spend_cap_cents: null,
  notification_preferences: {
    candidate_updates: true,
    daily_digest: true,
    panel_reminders: true,
    sync_failures: true,
    spend_over_budget: true,
    agent_paused: true,
  },
  fireflies_config: {
    connected: false,
    has_api_key: false,
    webhook_secret_configured: false,
    owner_email: '',
    invite_email: '',
    single_account_mode: true,
  },
};

const renderSettingsRoute = (initialPath = '/settings/email') => render(
  <MemoryRouter initialEntries={[initialPath]}>
    <Routes>
      <Route path="/settings/*" element={<SettingsPage onNavigate={vi.fn()} />} />
    </Routes>
  </MemoryRouter>
);

describe('SettingsPage recruiter surface', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.user = {
      id: 1,
      email: 'admin@taali.ai',
      full_name: 'Sam Patel',
      role: 'owner',
      organization: { name: 'DeepLight AI' },
    };
    orgsApi.get.mockResolvedValue({ data: baseOrgData });
    orgsApi.update.mockResolvedValue({ data: baseOrgData });
    orgsApi.getWorkableSyncStatus.mockResolvedValue({ data: { sync_in_progress: false } });
    orgsApi.getWorkableSyncJobs.mockResolvedValue({ data: { jobs: [] } });
    orgsApi.getWorkableMembers.mockResolvedValue({ data: { members: [] } });
    orgsApi.getWorkableDisqualificationReasons.mockResolvedValue({ data: { disqualification_reasons: [] } });
    orgsApi.getWorkableStages.mockResolvedValue({ data: { stages: [] } });
    orgsApi.listCriteria.mockResolvedValue({ data: [] });
    billingApi.usage.mockResolvedValue({ data: { usage: [], total_cost: 0 } });
    billingApi.costs.mockResolvedValue({ data: { total_cost_usd: 0 } });
    billingApi.credits.mockResolvedValue({ data: { credits_balance: 0, packs: [] } });
    billingApi.usageBreakdown.mockResolvedValue({ data: { by_feature: [] } });
    billingApi.usageEvents.mockResolvedValue({ data: { events: [] } });
    teamApi.list.mockResolvedValue({ data: [] });
    teamApi.invite.mockResolvedValue({ data: { id: 22, email: 'new@deeplight.ai', full_name: 'New Recruiter', status: 'invited', email_sent: true } });
    teamApi.resendInvite.mockResolvedValue({ data: { email_sent: true } });
    teamApi.inviteLink.mockResolvedValue({ data: { accept_link: 'https://app.taali.ai/accept-invite?token=abc123' } });
    teamApi.remove.mockResolvedValue({ status: 204 });
  });

  it('renders the Email & transcripts section without crashing', async () => {
    renderSettingsRoute('/settings/email');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Email & transcripts/i })).toBeInTheDocument();
    });

    expect(screen.getByText('Invite template')).toBeInTheDocument();
    expect(screen.getByText('Fireflies transcript ingestion')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save invite template' })).toBeInTheDocument();
  });

  it('separates operational assessment estimates from the AI credit ledger', async () => {
    billingApi.costs.mockResolvedValue({
      data: {
        costs: [
          { cost_usd: { e2b: 0.75, email: 0.10, storage: 0.05, claude: 0.40 } },
          { cost_usd: { e2b: 0.25, email: 0.02, storage: 0.03, claude: 0.20 } },
        ],
        summary: { tenant_total_usd: 1.8, completed_assessments: 2 },
      },
    });

    renderSettingsRoute('/settings/billing');

    // The billing panel exists for one render before its load effect raises the
    // spinner. Wait for the mocked values too, so we assert the stable loaded
    // panel rather than retaining a heading element that was just unmounted.
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Operational assessment estimates/i })).toBeInTheDocument();
      expect(screen.getByText('Sandbox runtime').parentElement).toHaveTextContent('$1.00');
      expect(screen.getByText('Delivery email').parentElement).toHaveTextContent('$0.12');
      expect(screen.getByText('Retained data').parentElement).toHaveTextContent('$0.08');
      expect(screen.getByText(/not usage charges/i)).toBeInTheDocument();
      expect(screen.getByText(/do not debit a role's AI-usage cap/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/^Monthly spend cap$/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Save spend cap/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/^Spend over budget$/i)).not.toBeInTheDocument();
  });

  it('saves the complete workspace automation policy without turning roles on', async () => {
    // Criteria chips save inline; the metered budget, threshold strategy, and
    // action-level autonomy defaults are one auditable policy save.
    renderSettingsRoute('/settings/agent');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Default role criteria/i })).toBeInTheDocument();
    });

    expect(screen.getByText(/never turn an agent on by themselves/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resend assessment invites automatically' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.change(screen.getByLabelText('Default threshold strategy'), { target: { value: 'auto' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send assessments automatically' }));
    fireEvent.click(screen.getByRole('button', { name: 'Advance on-policy candidates automatically' }));
    fireEvent.click(screen.getByRole('button', { name: 'Reject deterministic screening failures automatically' }));

    const saveButton = await screen.findByRole('button', { name: 'Save agent defaults' });
    expect(saveButton).toBeEnabled();
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(orgsApi.update).toHaveBeenCalledWith({
        default_role_budget_cents: 20000,
        default_score_threshold: 70,
        ai_tooling_config: {
          provider_setting: 'keep-me',
          agent_defaults: {
            enabled: false,
            budget_cents: 20000,
            threshold_mode: 'auto',
            auto_send_assessment: true,
            auto_resend_assessment: true,
            auto_advance: true,
            auto_reject_pre_screen: true,
            auto_skip_assessment: false,
            agent_token_budget_per_cycle: 12000,
          },
        },
      });
    });
    expect(showToast).toHaveBeenCalledWith('Agent defaults saved.', 'success');
  });

  it('shows the complete reversible platform policy when a workspace has no saved agent defaults', async () => {
    orgsApi.get.mockResolvedValueOnce({
      data: {
        ...baseOrgData,
        ai_tooling_config: { provider_setting: 'keep-me' },
      },
    });

    renderSettingsRoute('/settings/agent');

    expect(await screen.findByRole('button', { name: 'Send assessments automatically' }))
      .toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Resend assessment invites automatically' }))
      .toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Advance on-policy candidates automatically' }))
      .toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Reject deterministic screening failures automatically' }))
      .toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: 'Skip the assessment stage' }))
      .toHaveAttribute('aria-pressed', 'false');
  });

  it('legacy /settings/ai and /settings/scoring deep links land on the agent tab', async () => {
    renderSettingsRoute('/settings/ai');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /AI agent/i })).toBeInTheDocument();
    });
  });

  it('renders the new Members tab heading and role chips', async () => {
    teamApi.list.mockResolvedValue({
      data: [
        { id: 1, email: 'admin@taali.ai', full_name: 'Sam Patel', is_email_verified: true, role: 'owner' },
        { id: 7, email: 'iris@deeplight.ai', full_name: 'Iris Park', is_email_verified: true, role: 'member' },
      ],
    });
    renderSettingsRoute('/settings/members');

    await waitFor(() => {
      expect(screen.getAllByRole('heading', { name: /Members/i }).length).toBeGreaterThan(0);
    });
    await waitFor(() => {
      expect(screen.getByText('Iris Park')).toBeInTheDocument();
    });
    expect(screen.getByText('Owner')).toBeInTheDocument();
    expect(screen.getByText('Member')).toBeInTheDocument();
  });

  it('lets an owner promote a member to owner', async () => {
    teamApi.list.mockResolvedValue({
      data: [
        { id: 1, email: 'admin@taali.ai', full_name: 'Sam Patel', is_email_verified: true, role: 'owner' },
        { id: 7, email: 'iris@deeplight.ai', full_name: 'Iris Park', is_email_verified: true, role: 'member' },
      ],
    });
    teamApi.setRole.mockResolvedValue({
      data: { id: 7, email: 'iris@deeplight.ai', full_name: 'Iris Park', is_email_verified: true, role: 'owner' },
    });
    renderSettingsRoute('/settings/members');

    const promoteButton = await screen.findByRole('button', { name: 'Make owner' });
    fireEvent.click(promoteButton);

    await waitFor(() => {
      expect(teamApi.setRole).toHaveBeenCalledWith(7, 'owner');
    });
    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith('Iris Park is now an owner.', 'success');
    });
    // Chip flips to Owner and the row action becomes a demote.
    expect(await screen.findByRole('button', { name: 'Make member' })).toBeInTheDocument();
  });

  it('hides invite + member management from non-owners', async () => {
    authState.user = { ...authState.user, role: 'member' };
    teamApi.list.mockResolvedValue({
      data: [
        { id: 1, email: 'owner@deeplight.ai', full_name: 'Org Owner', is_email_verified: true, role: 'owner' },
        { id: 2, email: 'admin@taali.ai', full_name: 'Sam Patel', is_email_verified: true, role: 'member' },
      ],
    });
    renderSettingsRoute('/settings/members');

    await waitFor(() => {
      expect(screen.getByText('Only a workspace owner can invite members.')).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: /Invite member/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Make owner|Make member/i })).not.toBeInTheDocument();
    // Access settings save is owner-only too.
    expect(screen.getByRole('button', { name: 'Save access settings' })).toBeDisabled();
    expect(screen.getByText('Only a workspace owner can change access settings.')).toBeInTheDocument();
  });

  it('appends the new row and toasts success after inviting a member', async () => {
    renderSettingsRoute('/settings/members');

    await waitFor(() => {
      expect(screen.getAllByRole('heading', { name: /Members/i }).length).toBeGreaterThan(0);
    });

    fireEvent.change(screen.getByPlaceholderText('Alex Weston'), { target: { value: 'New Recruiter' } });
    fireEvent.change(screen.getByPlaceholderText('alex@company.com'), { target: { value: 'new@deeplight.ai' } });
    fireEvent.click(screen.getByRole('button', { name: /Invite member/i }));

    await waitFor(() => {
      expect(teamApi.invite).toHaveBeenCalledWith({ email: 'new@deeplight.ai', full_name: 'New Recruiter' });
      expect(screen.getByText('New Recruiter')).toBeInTheDocument();
      expect(showToast).toHaveBeenCalledWith('Invite sent.', 'success');
    });
  });

  it('warns when the invite is created but the email could not be sent', async () => {
    teamApi.invite.mockResolvedValueOnce({
      data: { id: 23, email: 'nomail@deeplight.ai', full_name: 'No Mail', status: 'invited', email_sent: false },
    });
    renderSettingsRoute('/settings/members');

    await waitFor(() => {
      expect(screen.getAllByRole('heading', { name: /Members/i }).length).toBeGreaterThan(0);
    });

    fireEvent.change(screen.getByPlaceholderText('Alex Weston'), { target: { value: 'No Mail' } });
    fireEvent.change(screen.getByPlaceholderText('alex@company.com'), { target: { value: 'nomail@deeplight.ai' } });
    fireEvent.click(screen.getByRole('button', { name: /Invite member/i }));

    await waitFor(() => {
      expect(screen.getByText('No Mail')).toBeInTheDocument();
      expect(showToast).toHaveBeenCalledWith('Invite created, but the email could not be sent. Use Resend invite.', 'warning');
    });
  });

  it('toasts "Member restored." (no email warning) when re-inviting a removed verified member', async () => {
    // A previously removed VERIFIED member is restored directly: the invite
    // response carries status 'active' and email_sent false (no email goes
    // out — they already have a password).
    teamApi.invite.mockResolvedValueOnce({
      data: { id: 24, email: 'back@deeplight.ai', full_name: 'Come Back', status: 'active', email_sent: false },
    });
    renderSettingsRoute('/settings/members');

    await waitFor(() => {
      expect(screen.getAllByRole('heading', { name: /Members/i }).length).toBeGreaterThan(0);
    });

    fireEvent.change(screen.getByPlaceholderText('Alex Weston'), { target: { value: 'Come Back' } });
    fireEvent.change(screen.getByPlaceholderText('alex@company.com'), { target: { value: 'back@deeplight.ai' } });
    fireEvent.click(screen.getByRole('button', { name: /Invite member/i }));

    await waitFor(() => {
      expect(screen.getByText('Come Back')).toBeInTheDocument();
      expect(showToast).toHaveBeenCalledWith('Member restored.', 'success');
    });
    expect(showToast).not.toHaveBeenCalledWith(expect.stringContaining('could not be sent'), 'warning');
  });

  it('resends an invite for a pending member and toasts', async () => {
    teamApi.list.mockResolvedValue({
      data: [{ id: 8, email: 'pending@deeplight.ai', full_name: 'Pending Person', status: 'invited' }],
    });
    renderSettingsRoute('/settings/members');

    await waitFor(() => {
      expect(screen.getByText('Pending Person')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Resend invite' }));

    await waitFor(() => {
      expect(teamApi.resendInvite).toHaveBeenCalledWith(8);
      expect(showToast).toHaveBeenCalledWith('Invite resent.', 'success');
    });
  });

  it('copies the invite link for a pending member and toasts success', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    teamApi.list.mockResolvedValue({
      data: [{ id: 8, email: 'pending@deeplight.ai', full_name: 'Pending Person', status: 'invited' }],
    });
    renderSettingsRoute('/settings/members');

    await waitFor(() => {
      expect(screen.getByText('Pending Person')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Copy link' }));

    await waitFor(() => {
      expect(teamApi.inviteLink).toHaveBeenCalledWith(8);
      expect(writeText).toHaveBeenCalledWith('https://app.taali.ai/accept-invite?token=abc123');
      expect(showToast).toHaveBeenCalledWith('Invite link copied.', 'success');
    });
  });

  it('revokes a pending invite only after inline confirm', async () => {
    teamApi.list.mockResolvedValue({
      data: [{ id: 8, email: 'pending@deeplight.ai', full_name: 'Pending Person', status: 'invited' }],
    });
    renderSettingsRoute('/settings/members');

    await waitFor(() => {
      expect(screen.getByText('Pending Person')).toBeInTheDocument();
    });

    // First click arms the confirm — no request yet.
    fireEvent.click(screen.getByRole('button', { name: 'Revoke' }));
    expect(teamApi.remove).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() => {
      expect(teamApi.remove).toHaveBeenCalledWith(8);
      expect(showToast).toHaveBeenCalledWith('Invite revoked.', 'success');
      expect(screen.queryByText('Pending Person')).not.toBeInTheDocument();
    });
  });

  it('removes an active member (not the current user) after inline confirm', async () => {
    teamApi.list.mockResolvedValue({
      data: [
        { id: 1, email: 'admin@taali.ai', full_name: 'Sam Patel', status: 'active', role: 'Owner' },
        { id: 9, email: 'iris@deeplight.ai', full_name: 'Iris Park', status: 'active', role: 'Recruiter' },
      ],
    });
    renderSettingsRoute('/settings/members');

    await waitFor(() => {
      expect(screen.getByText('Iris Park')).toBeInTheDocument();
    });

    // Only the non-self active row exposes a Remove action.
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    expect(teamApi.remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() => {
      expect(teamApi.remove).toHaveBeenCalledWith(9);
      expect(showToast).toHaveBeenCalledWith('Member removed.', 'success');
      expect(screen.queryByText('Iris Park')).not.toBeInTheDocument();
    });
  });

  it('shows write-back settings from granted scopes and requires an actor member before saving', async () => {
    orgsApi.get.mockResolvedValueOnce({
      data: {
        ...baseOrgData,
        workable_connected: true,
        workable_subdomain: 'acme',
        workable_config: {
          workable_writeback: false,
          default_sync_mode: 'full',
          granted_scopes: ['r_jobs', 'r_candidates', 'w_candidates'],
          workable_actor_member_id: '',
          workable_disqualify_reason_id: '',
          auto_reject_enabled: false,
          auto_reject_note_template: '',
        },
      },
    });
    orgsApi.getWorkableMembers.mockResolvedValueOnce({
      data: {
        members: [{ id: 'member-1', name: 'Hiring Lead' }],
      },
    });

    renderSettingsRoute('/settings/workable');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Workable integration/i })).toBeInTheDocument();
    });
    // The actor-member control is now the styled portal dropdown, so its
    // options only mount once it's opened. Open it to confirm the member
    // loaded, then close without selecting so Save is still blocked.
    const actorField = (await screen.findByText('Workable actor member')).closest('label');
    fireEvent.click(actorField.querySelector('button'));
    expect(await screen.findByRole('option', { name: 'Hiring Lead' })).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: 'Escape' });

    fireEvent.click(screen.getByRole('button', { name: 'Save Workable Settings' }));

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith(
        'Choose the Workable member account that should perform Workable invite, advance, reject, and reopen actions.',
        'error'
      );
    });
    expect(orgsApi.update).not.toHaveBeenCalled();
  });

  it('saves the Workable interview handoff stage used by autonomous advances', async () => {
    orgsApi.get.mockResolvedValueOnce({
      data: {
        ...baseOrgData,
        workable_connected: true,
        workable_subdomain: 'acme',
        workable_config: {
          workable_writeback: true,
          default_sync_mode: 'full',
          granted_scopes: ['r_jobs', 'r_candidates', 'w_candidates'],
          invite_stage_name: 'Assessment invited',
          interview_stage_name: 'Hiring manager interview',
          workable_actor_member_id: 'member-1',
          workable_disqualify_reason_id: '',
          auto_reject_enabled: false,
          auto_reject_note_template: '',
        },
      },
    });
    orgsApi.getWorkableMembers.mockResolvedValueOnce({
      data: { members: [{ id: 'member-1', name: 'Hiring Lead' }] },
    });
    renderSettingsRoute('/settings/workable');

    const handoffInput = await screen.findByLabelText(/Interview handoff stage name/i);
    await waitFor(() => expect(handoffInput).toHaveValue('Hiring manager interview'));
    expect(screen.getByText(/agent-driven advances land in the correct Workable stage/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save Workable Settings' }));

    await waitFor(() => expect(orgsApi.update).toHaveBeenCalledWith({
      workable_config: expect.objectContaining({
        workable_writeback: true,
        invite_stage_name: 'Assessment invited',
        interview_stage_name: 'Hiring manager interview',
        workable_actor_member_id: 'member-1',
      }),
    }));
  });

  it('blocks two-way Workable mode when w_candidates scope is not granted', async () => {
    orgsApi.get.mockResolvedValueOnce({
      data: {
        ...baseOrgData,
        workable_connected: true,
        workable_subdomain: 'acme',
        workable_config: {
          workable_writeback: false,
          default_sync_mode: 'full',
          granted_scopes: ['r_jobs', 'r_candidates'],
          workable_actor_member_id: '',
          workable_disqualify_reason_id: '',
          auto_reject_enabled: false,
          auto_reject_note_template: '',
        },
      },
    });

    renderSettingsRoute('/settings/workable');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Workable integration/i })).toBeInTheDocument();
    });

    // The mode card is now the write-back binary (write back / read-only).
    fireEvent.click(screen.getByRole('button', { name: /Write back to Workable/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Save Workable Settings' }));

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith(
        'Reconnect Workable with the "Write candidates" (w_candidates) permission to enable invite, advance, reject, and reopen actions.',
        'error'
      );
    });
    expect(orgsApi.update).not.toHaveBeenCalled();
  });

  it('keeps existing Fireflies credentials unless the user explicitly clears them', async () => {
    orgsApi.get.mockResolvedValueOnce({
      data: {
        ...baseOrgData,
        fireflies_config: {
          connected: true,
          has_api_key: true,
          webhook_secret_configured: true,
          owner_email: 'recruiter@deeplight.ai',
          invite_email: 'taali@fireflies.ai',
          single_account_mode: true,
        },
      },
    });

    renderSettingsRoute('/settings/email');

    await waitFor(() => {
      expect(screen.getByText('Fireflies transcript ingestion')).toBeInTheDocument();
    });

    const ownerEmailInput = screen.getByLabelText(/Owner email/i);
    fireEvent.change(ownerEmailInput, {
      target: { value: 'ops@deeplight.ai' },
    });
    await waitFor(() => {
      expect(ownerEmailInput).toHaveValue('ops@deeplight.ai');
    });

    fireEvent.click(screen.getByRole('button', { name: 'Save Fireflies Settings' }));

    await waitFor(() => {
      expect(orgsApi.update).toHaveBeenCalledWith({
        fireflies_config: {
          owner_email: 'ops@deeplight.ai',
          invite_email: 'taali@fireflies.ai',
          single_account_mode: true,
        },
      });
    });
  });

  it('can explicitly clear stored Fireflies credentials', async () => {
    orgsApi.get.mockResolvedValueOnce({
      data: {
        ...baseOrgData,
        fireflies_config: {
          connected: true,
          has_api_key: true,
          webhook_secret_configured: true,
          owner_email: 'recruiter@deeplight.ai',
          invite_email: 'taali@fireflies.ai',
          single_account_mode: true,
        },
      },
    });

    renderSettingsRoute('/settings/email');

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Clear stored API key' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Clear webhook secret' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Clear stored API key' }));
    fireEvent.click(screen.getByRole('button', { name: 'Clear webhook secret' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save Fireflies Settings' }));

    await waitFor(() => {
      expect(orgsApi.update).toHaveBeenCalledWith({
        fireflies_config: {
          api_key: '',
          webhook_secret: '',
          owner_email: 'recruiter@deeplight.ai',
          invite_email: 'taali@fireflies.ai',
          single_account_mode: true,
        },
      });
    });
  });

  it('renders the Security tab with SAML, 2FA and audit log entry', async () => {
    renderSettingsRoute('/settings/security');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Security/i })).toBeInTheDocument();
    });
    expect(screen.getAllByText(/Two-factor authentication/i).length).toBeGreaterThan(0);
    expect(screen.getByRole('link', { name: /Open audit log/i })).toBeInTheDocument();
    expect(screen.getAllByText(/SAML SSO/i).length).toBeGreaterThan(0);
  });

  it('lands legacy /settings/workable and /settings/bullhorn deep links on the unified Integrations tab', async () => {
    renderSettingsRoute('/settings/bullhorn');
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /^Integrations\.?$/i })).toBeInTheDocument();
    });
    // The Workable card still renders under the unified surface.
    expect(screen.getByRole('heading', { name: /Workable integration/i })).toBeInTheDocument();
  });

  it('hides the Bullhorn card when bullhorn_enabled is off (default)', async () => {
    renderSettingsRoute('/settings/workable');
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Workable integration/i })).toBeInTheDocument();
    });
    expect(screen.queryByRole('heading', { name: /Bullhorn integration/i })).not.toBeInTheDocument();
  });

  it('shows the Bullhorn card only when bullhorn_enabled is on', async () => {
    orgsApi.get.mockResolvedValue({ data: { ...baseOrgData, bullhorn_enabled: true } });
    renderSettingsRoute('/settings/workable');
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Bullhorn integration/i })).toBeInTheDocument();
    });
  });

  it('shows the Active ATS indicator from active_ats', async () => {
    orgsApi.get.mockResolvedValue({ data: { ...baseOrgData, active_ats: 'standalone' } });
    renderSettingsRoute('/settings/integrations');
    await waitFor(() => {
      expect(screen.getByText('Active ATS')).toBeInTheDocument();
    });
    expect(screen.getByText(/Taali runs standalone/i)).toBeInTheDocument();
  });
});
