import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../shared/api/outreachClient', () => ({
  outreach: {
    listCampaigns: vi.fn(),
    getCampaign: vi.fn(),
    createCampaign: vi.fn(),
    patchCampaign: vi.fn(),
    archiveCampaign: vi.fn(),
    addAudience: vi.fn(),
    generate: vi.fn(),
    editMessage: vi.fn(),
    approve: vi.fn(),
    reject: vi.fn(),
    send: vi.fn(),
  },
}));

vi.mock('../../shared/api/prospectsClient', () => ({
  prospects: { list: vi.fn(() => Promise.resolve({ data: { prospects: [] } })) },
}));

vi.mock('../../shared/api/rolesClient', () => ({
  roles: { list: vi.fn(() => Promise.resolve({ data: { roles: [] } })) },
}));

import { outreach as outreachApi } from '../../shared/api/outreachClient';
import CampaignsPanel from './CampaignsPanel';

describe('CampaignsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the campaign list with rollup counts', async () => {
    outreachApi.listCampaigns.mockResolvedValue({
      data: {
        campaigns: [
          {
            id: 1,
            name: 'Backend Wave',
            status: 'sent',
            counts: { sent: 5, opened: 3, clicked: 2, interested: 1 },
          },
        ],
      },
    });
    render(<CampaignsPanel />);
    await waitFor(() => expect(screen.getByText('Backend Wave')).toBeInTheDocument());
    expect(screen.getByText('sent')).toBeInTheDocument();
    const table = screen.getByTestId('campaigns-table');
    expect(table.textContent).toContain('5');
  });

  it('cost-confirm flow: estimate then confirm generate', async () => {
    outreachApi.getCampaign.mockResolvedValue({
      data: {
        id: 9,
        name: 'Wave',
        status: 'draft',
        brief: 'b',
        counts: {},
        messages: [
          { id: 1, email: 'a@x.com', recipient_name: 'A', status: 'pending' },
        ],
      },
    });
    outreachApi.generate.mockImplementation((id, confirm) =>
      confirm
        ? Promise.resolve({ data: { count: 1, estimated_cost_usd: 0.01, status: 'generating' } })
        : Promise.resolve({ data: { count: 1, estimated_cost_usd: 0.01 } }),
    );

    render(<CampaignsPanel initialCampaignId={9} />);
    await waitFor(() => expect(screen.getByText('Wave')).toBeInTheDocument());

    fireEvent.click(screen.getByText(/Generate drafts \(1\)/));
    // estimate call (confirm=false)
    await waitFor(() => expect(outreachApi.generate).toHaveBeenCalledWith(9, false));
    // cost-confirm dialog shows the estimate
    await waitFor(() => expect(screen.getByText(/Estimated cost/)).toBeInTheDocument());

    fireEvent.click(screen.getByText('Generate'));
    await waitFor(() => expect(outreachApi.generate).toHaveBeenCalledWith(9, true));
  });

  it('draft edit + approve on a message row', async () => {
    outreachApi.getCampaign.mockResolvedValue({
      data: {
        id: 3,
        name: 'Wave',
        status: 'ready',
        brief: '',
        counts: {},
        messages: [
          { id: 55, email: 'd@x.com', recipient_name: 'D', subject: 'Hi', body: 'Body {{cta_url}}', status: 'draft' },
        ],
      },
    });
    outreachApi.approve.mockResolvedValue({ data: { approved: 1 } });

    render(<CampaignsPanel initialCampaignId={3} />);
    await waitFor(() => expect(screen.getByTestId('message-55')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Approve'));
    await waitFor(() =>
      expect(outreachApi.approve).toHaveBeenCalledWith(3, { message_ids: [55] }),
    );
  });
});
