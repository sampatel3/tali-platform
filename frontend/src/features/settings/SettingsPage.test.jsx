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
  },
}));

const showToast = vi.fn();

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 1,
      email: 'admin@taali.ai',
      full_name: 'Sam Patel',
      organization: { name: 'DeepLight AI' },
    },
  }),
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
  candidate_feedback_enabled: true,
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
    orgsApi.get.mockResolvedValue({ data: baseOrgData });
    orgsApi.update.mockResolvedValue({ data: baseOrgData });
    orgsApi.getWorkableSyncStatus.mockResolvedValue({ data: { sync_in_progress: false } });
    orgsApi.getWorkableSyncJobs.mockResolvedValue({ data: { jobs: [] } });
    orgsApi.getWorkableMembers.mockResolvedValue({ data: { members: [] } });
    orgsApi.getWorkableDisqualificationReasons.mockResolvedValue({ data: { disqualification_reasons: [] } });
    orgsApi.getWorkableStages.mockResolvedValue({ data: { stages: [] } });
    billingApi.usage.mockResolvedValue({ data: { usage: [], total_cost: 0 } });
    billingApi.costs.mockResolvedValue({ data: { total_cost_usd: 0 } });
    billingApi.credits.mockResolvedValue({ data: { credits_balance: 0, packs: [] } });
    billingApi.usageBreakdown.mockResolvedValue({ data: { by_feature: [] } });
    billingApi.usageEvents.mockResolvedValue({ data: { events: [] } });
    teamApi.list.mockResolvedValue({ data: [] });
    teamApi.invite.mockResolvedValue({ data: { id: 22, email: 'new@deeplight.ai', full_name: 'New Recruiter' } });
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

  it('saves the three workspace defaults from the AI agent tab', async () => {
    renderSettingsRoute('/settings/agent');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /AI agent/i })).toBeInTheDocument();
    });
    // Wait for the seeded list to land in the form so the save click sees
    // the same values as the user.
    await waitFor(() => {
      expect(screen.getByDisplayValue('5+ years backend')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Save agent defaults' }));

    await waitFor(() => {
      expect(orgsApi.update).toHaveBeenCalledWith({
        default_role_requirements: ['5+ years backend', 'Strong SQL'],
        default_role_budget_cents: 20000,
        default_score_threshold: 70,
      });
    });
    expect(showToast).toHaveBeenCalledWith('Agent defaults saved.', 'success');
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
        { id: 7, email: 'iris@deeplight.ai', full_name: 'Iris Park', is_email_verified: true, role: 'Recruiter' },
      ],
    });
    renderSettingsRoute('/settings/members');

    await waitFor(() => {
      expect(screen.getAllByRole('heading', { name: /Members/i }).length).toBeGreaterThan(0);
    });
    await waitFor(() => {
      expect(screen.getByText('Iris Park')).toBeInTheDocument();
    });
    expect(screen.getByText('Recruiter')).toBeInTheDocument();
  });

  it('shows write-back settings from granted scopes and requires an actor member before saving', async () => {
    orgsApi.get.mockResolvedValueOnce({
      data: {
        ...baseOrgData,
        workable_connected: true,
        workable_subdomain: 'acme',
        workable_config: {
          email_mode: 'manual_taali',
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
    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'Hiring Lead' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Save Workable Settings' }));

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith(
        'Choose the Workable member account that should perform Workable invite, reject, and reopen actions.',
        'error'
      );
    });
    expect(orgsApi.update).not.toHaveBeenCalled();
  });

  it('blocks two-way Workable mode when w_candidates scope is not granted', async () => {
    orgsApi.get.mockResolvedValueOnce({
      data: {
        ...baseOrgData,
        workable_connected: true,
        workable_subdomain: 'acme',
        workable_config: {
          email_mode: 'manual_taali',
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

    // The mode card was renamed hybrid|manual → two_way|read_only.
    fireEvent.click(screen.getByRole('button', { name: /Two-way/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Save Workable Settings' }));

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith(
        'Reconnect Workable with `w_candidates` scope to enable Workable invite, reject, and reopen actions.',
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
});
