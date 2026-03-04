import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

vi.mock('../../shared/api', () => ({
  organizations: {
    get: vi.fn(),
    update: vi.fn(),
    getWorkableAuthorizeUrl: vi.fn(),
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

import { organizations as orgsApi } from '../../shared/api';
import { SettingsPage } from './SettingsPage';

const baseOrgData = {
  id: 1,
  name: 'DeepLight AI',
  plan: 'pay_per_use',
  default_assessment_duration_minutes: 30,
  invite_email_template: 'Hi {{candidate_name}}, your TAALI assessment is ready: {{assessment_link}}',
  has_custom_claude_api_key: false,
  candidate_feedback_enabled: true,
  allowed_email_domains: [],
  sso_enforced: false,
  saml_enabled: false,
  saml_metadata_url: '',
  workable_connected: false,
};

const renderSettingsRoute = (initialPath = '/settings/preferences') => render(
  <MemoryRouter initialEntries={[initialPath]}>
    <Routes>
      <Route path="/settings/:tab" element={<SettingsPage onNavigate={vi.fn()} />} />
    </Routes>
  </MemoryRouter>
);

describe('SettingsPage preferences', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    orgsApi.get.mockResolvedValue({ data: baseOrgData });
    orgsApi.update.mockResolvedValue({
      data: {
        ...baseOrgData,
        default_assessment_duration_minutes: 45,
        invite_email_template: 'Hello {{candidate_name}}, here is your TAALI assessment: {{assessment_link}}',
        has_custom_claude_api_key: true,
      },
    });
  });

  it('renders the preferences route without crashing', async () => {
    renderSettingsRoute();

    await waitFor(() => {
      expect(screen.getByText('Display Preferences')).toBeInTheDocument();
    });

    expect(screen.getByText('Assessment Defaults')).toBeInTheDocument();
    expect(screen.getByText('Invite Email Template Preview')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save preferences' })).toBeInTheDocument();
  });

  it('saves preferences from the preferences route', async () => {
    renderSettingsRoute();

    await waitFor(() => {
      expect(screen.getByText('Display Preferences')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/template body/i), {
      target: { value: 'Hello {{candidate_name}}, here is your TAALI assessment: {{assessment_link}}' },
    });
    fireEvent.change(screen.getByLabelText(/default assessment duration/i), {
      target: { value: '45' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Save preferences' }));

    await waitFor(() => {
      expect(orgsApi.update).toHaveBeenCalledWith({
        default_assessment_duration_minutes: 45,
        invite_email_template: 'Hello {{candidate_name}}, here is your TAALI assessment: {{assessment_link}}',
      });
    });
    expect(showToast).toHaveBeenCalledWith('Preferences saved.', 'success');
  });
});
