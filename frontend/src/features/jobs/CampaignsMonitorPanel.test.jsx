import React from 'react';
import { act, render, screen, waitFor, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../shared/api/outreachClient', () => ({
  outreach: { listCampaigns: vi.fn() },
}));

import { outreach as outreachApi } from '../../shared/api/outreachClient';
import { CampaignsMonitorPanel } from './CampaignsMonitorPanel';

describe('CampaignsMonitorPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    outreachApi.listCampaigns.mockResolvedValue({
      data: {
        campaigns: [
          {
            id: 3,
            name: 'Sourced outreach · Backend',
            status: 'sent',
            counts: {
              audience: 10, drafted: 10, approved: 9, sent: 9,
              delivered: 8, opened: 5, clicked: 3, interested: 2,
              bounced: 1, failed: 0,
            },
          },
        ],
      },
    });
  });

  afterEach(() => vi.useRealTimers());

  it('lists role campaigns and expands to the full funnel counts', async () => {
    render(<CampaignsMonitorPanel roleId={5} defaultOpen />);

    // Loads scoped to the role.
    await waitFor(() => expect(outreachApi.listCampaigns).toHaveBeenCalledWith(
      5,
      { limit: 50, offset: 0 },
    ));
    await waitFor(() => expect(screen.getByText('Sourced outreach · Backend')).toBeInTheDocument());

    // Compact summary reads off the rollup counts.
    expect(screen.getByText(/9 sent · 5 opened · 2 interested/)).toBeInTheDocument();

    // Expand → the full send-order funnel is shown.
    await act(async () => { fireEvent.click(screen.getByText('Sourced outreach · Backend')); });
    ['Audience', 'Drafted', 'Approved', 'Sent', 'Delivered', 'Opened', 'Clicked', 'Interested'].forEach((label) => {
      expect(screen.getByText(label)).toBeInTheDocument();
    });
    expect(screen.getByText(/1 bounced/)).toBeInTheDocument();
  });

  it('auto-opens and focuses a campaign after a reach-out send', async () => {
    render(<CampaignsMonitorPanel roleId={5} focusCampaignId={3} />);
    await waitFor(() => expect(outreachApi.listCampaigns).toHaveBeenCalledWith(
      5,
      { limit: 50, offset: 0 },
    ));
    // Focused campaign renders its funnel expanded without a click.
    await waitFor(() => expect(screen.getByText('Audience')).toBeInTheDocument());
  });
});
