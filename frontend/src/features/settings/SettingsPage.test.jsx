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

import { organizations as orgsApi, team as teamApi } from '../../shared/api';
import { SettingsPage } from './SettingsPage';

const baseOrgData = {
  id: 1,
  name: 'DeepLight AI',
  plan: 'pay_per_use',
  default_assessment_duration_minutes: 30,
  invite_email_template: 'Hi {{candidate_name}}, your TAALI assessment is ready: {{assessment_link}}',
  has_custom_claude_api_key: false,
  candidate_feedback_enabled: true,
  recruiter_workflow_v2_enabled: true,
  allowed_email_domains: [],
  sso_enforced: false,
  saml_enabled: false,
  saml_metadata_url: '',
  workable_connected: false,
  workspace_settings: {
    candidate_facing_brand: 'DeepLight · Engineering',
    primary_domain: 'deeplight.ai',
    locale: 'English (US)',
  },
  scoring_policy: {
    prompt_quality: true,
    error_recovery: true,
    independence: true,
    context_utilization: true,
    design_thinking: true,
    time_to_first_signal: false,
  },
  ai_tooling_config: {
    claude_enabled: true,
    cursor_inline_enabled: false,
    no_ai_baseline_enabled: true,
    claude_credit_per_candidate_usd: 12,
    session_timeout_minutes: 60,
  },
  notification_preferences: {
    candidate_updates: true,
    daily_digest: true,
    panel_reminders: true,
    sync_failures: true,
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

const renderSettingsRoute = (initialPath = '/settings/api') => render(
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
    teamApi.list.mockResolvedValue({ data: [] });
    teamApi.invite.mockResolvedValue({ data: { id: 22, email: 'new@deeplight.ai', full_name: 'New Recruiter' } });
  });

  it('renders the API keys section without crashing', async () => {
    renderSettingsRoute('/settings/api');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /API keys/i })).toBeInTheDocument();
    });

    expect(screen.getByText('Claude key')).toBeInTheDocument();
    expect(screen.getByText('Fireflies transcript ingestion')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save API key settings' })).toBeInTheDocument();
  });

  it('saves AI tooling settings from the AI section', async () => {
    renderSettingsRoute('/settings/ai');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /AI tooling/i })).toBeInTheDocument();
    });

    const durationInput = screen.getByLabelText(/Default assessment duration/i);
    fireEvent.change(durationInput, {
      target: { value: '45' },
    });
    await waitFor(() => {
      expect(durationInput).toHaveValue(45);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save AI tooling' }));

    await waitFor(() => {
      expect(orgsApi.update).toHaveBeenCalledWith({
        ai_tooling_config: {
          claude_enabled: true,
          cursor_inline_enabled: false,
          no_ai_baseline_enabled: true,
          claude_credit_per_candidate_usd: 12,
          session_timeout_minutes: 60,
        },
        default_assessment_duration_minutes: 45,
      });
    });
    expect(showToast).toHaveBeenCalledWith('AI tooling settings saved.', 'success');
  });

  it('saves scoring policy toggles', async () => {
    renderSettingsRoute('/settings/scoring');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Scoring policy/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getAllByRole('button', { pressed: true })[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Save scoring policy' }));

    await waitFor(() => {
      expect(orgsApi.update).toHaveBeenCalledWith({
        scoring_policy: expect.objectContaining({
          prompt_quality: false,
        }),
      });
    });
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

  it('blocks automated write-back settings when w_candidates scope is not granted', async () => {
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

    fireEvent.click(screen.getByRole('button', { name: /Workable hybrid/i }));
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

    renderSettingsRoute('/settings/api');

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

    renderSettingsRoute('/settings/api');

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
});
