import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../shared/api/prospectsClient', () => ({
  prospects: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    archive: vi.fn(),
    importCsv: vi.fn(),
  },
}));

vi.mock('../../shared/api/outreachClient', () => ({
  outreach: {
    createCampaign: vi.fn(),
    addAudience: vi.fn(),
  },
}));

const showToast = vi.fn();
vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast }),
}));

vi.mock('../sourcing/CampaignsPanel', () => ({
  default: ({ initialCampaignId }) => (
    <div>Campaigns panel {initialCampaignId || 'list'}</div>
  ),
}));

import { prospects as prospectsApi } from '../../shared/api/prospectsClient';
import { outreach as outreachApi } from '../../shared/api/outreachClient';
import ProspectsPage from './ProspectsPage';

const sampleProspects = [
  {
    id: 1,
    full_name: 'Dana Source',
    email: 'dana@x.test',
    position: 'ML Engineer',
    status: 'new',
    created_at: '2026-01-10T10:00:00Z',
    candidate_id: null,
    suppressed: null,
  },
  {
    id: 2,
    full_name: 'Blocked Person',
    email: 'blocked@x.test',
    status: 'contacted',
    created_at: '2026-01-11T10:00:00Z',
    candidate_id: 55,
    suppressed: 'unsubscribed',
  },
];

const listResponse = (prospects = sampleProspects) => ({
  data: { prospects, total: prospects.length },
});

describe('ProspectsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    prospectsApi.list.mockResolvedValue(listResponse());
    prospectsApi.create.mockResolvedValue({ data: { id: 9 } });
    outreachApi.createCampaign.mockResolvedValue({ data: { id: 77 } });
    outreachApi.addAudience.mockResolvedValue({ data: {} });
  });

  it('lists org prospects with the suppression badge', async () => {
    render(<ProspectsPage onNavigate={vi.fn()} />);
    expect(await screen.findByText('Dana Source')).toBeInTheDocument();
    expect(screen.getByText('Blocked Person')).toBeInTheDocument();
    expect(screen.getByText('unsubscribed')).toBeInTheDocument();
  });

  it('reaches out: creates a campaign seeded with the selected prospects and opens Campaigns', async () => {
    render(<ProspectsPage onNavigate={vi.fn()} />);
    await screen.findByText('Dana Source');

    fireEvent.click(screen.getByRole('checkbox', { name: /Select Dana Source/i }));
    fireEvent.click(screen.getByRole('button', { name: /^Reach out$/i }));

    await waitFor(() => expect(outreachApi.createCampaign).toHaveBeenCalledTimes(1));
    expect(outreachApi.addAudience).toHaveBeenCalledWith(77, { prospect_ids: [1] });
    expect(await screen.findByText(/Campaigns panel 77/)).toBeInTheDocument();
  });

  it('does not allow selecting a suppressed prospect', async () => {
    render(<ProspectsPage onNavigate={vi.fn()} />);
    await screen.findByText('Blocked Person');
    expect(screen.getByRole('checkbox', { name: /Select Blocked Person/i })).toBeDisabled();
  });

  it('links a converted prospect through to its candidate', async () => {
    const onNavigate = vi.fn();
    render(<ProspectsPage onNavigate={onNavigate} />);
    await screen.findByText('Blocked Person');
    fireEvent.click(screen.getByRole('button', { name: /Candidate linked/i }));
    expect(onNavigate).toHaveBeenCalledWith('candidate-report', { candidateApplicationId: 55 });
  });

  it('opens the Campaigns management view from the header action', async () => {
    render(<ProspectsPage onNavigate={vi.fn()} />);
    await screen.findByText('Dana Source');
    fireEvent.click(screen.getByRole('button', { name: /Campaigns/i }));
    expect(await screen.findByText(/Campaigns panel list/)).toBeInTheDocument();
  });

  it('adds a prospect through the form', async () => {
    render(<ProspectsPage onNavigate={vi.fn()} />);
    await screen.findByText('Dana Source');
    fireEvent.click(screen.getByRole('button', { name: /Add prospect/i }));

    const form = screen.getByRole('form', { name: /Add prospect/i });
    fireEvent.change(within(form).getByLabelText('Full name'), { target: { value: 'New Lead' } });
    fireEvent.change(within(form).getByLabelText('Email'), { target: { value: 'new@x.test' } });
    fireEvent.click(within(form).getByRole('button', { name: /Save prospect/i }));

    await waitFor(() => expect(prospectsApi.create).toHaveBeenCalledTimes(1));
    expect(prospectsApi.create).toHaveBeenCalledWith(
      expect.objectContaining({ full_name: 'New Lead', email: 'new@x.test' }),
    );
  });
});
