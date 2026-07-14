import React from 'react';
import { act, render, screen, waitFor, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../shared/api/outreachClient', () => ({
  outreach: {
    listCampaigns: vi.fn(),
    getCampaign: vi.fn(),
    approveAndSend: vi.fn(),
  },
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
    await waitFor(() => expect(outreachApi.listCampaigns).toHaveBeenCalledWith(5));
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
    await waitFor(() => expect(outreachApi.listCampaigns).toHaveBeenCalledWith(5));
    // Focused campaign renders its funnel expanded without a click.
    await waitFor(() => expect(screen.getByText('Audience')).toBeInTheDocument());
  });

  it('surfaces an agent-prepared campaign and keeps the outbound send behind one counted approval', async () => {
    outreachApi.listCampaigns.mockResolvedValue({
      data: {
        campaigns: [
          {
            id: 41,
            name: 'Agent sourced outreach · Backend',
            origin: 'agent',
            status: 'ready',
            counts: {
              audience: 5,
              drafted: 4,
              approved: 0,
              sent: 0,
              opened: 0,
              interested: 0,
            },
          },
        ],
      },
    });
    outreachApi.approveAndSend.mockImplementation((_id, confirm) => (
      confirm
        ? Promise.resolve({ data: { status: 'sending' } })
        : Promise.resolve({
          data: {
            sendable_count: 4,
            will_send: 4,
            suppressed_excluded: 1,
            rejected_excluded: 0,
            failed_excluded: 0,
            review_token: 'review-41',
          },
        })
    ));
    outreachApi.getCampaign.mockResolvedValue({
      data: {
        id: 41,
        status: 'ready',
        messages: [1, 2, 3, 4].map((id) => ({
          id,
          status: 'draft',
          recipient_name: `Candidate ${id}`,
          email: `candidate${id}@example.com`,
          subject: `Backend opportunity ${id}`,
          body: `Personalised draft ${id}`,
        })),
      },
    });

    render(<CampaignsMonitorPanel roleId={5} />);

    // A ready campaign prepared by the role agent opens itself: there is no
    // recruiter candidate-selection step before the one outbound HITL gate.
    await waitFor(() => expect(screen.getByText(/Prepared by Taali/)).toBeInTheDocument());
    expect(screen.getByText(/no candidate selection required/)).toBeInTheDocument();
    expect(screen.getByText('LinkedIn RSC').closest('span')).toHaveTextContent(
      'LinkedIn RSC · partner access required; one-click export only',
    );
    expect(outreachApi.approveAndSend).toHaveBeenCalledWith(41, false);
    expect(screen.getByText('candidate1@example.com')).toBeInTheDocument();
    expect(screen.getByText('Backend opportunity 1')).toBeInTheDocument();
    expect(screen.getByText('Personalised draft 1')).toBeInTheDocument();

    const sendButton = await screen.findByRole('button', { name: /Approve & send 4/i });
    await act(async () => { fireEvent.click(sendButton); });

    // The previewed count is sent back as a stale-audience guard. The agent
    // may prepare the batch, but only this explicit approval sends it.
    expect(outreachApi.approveAndSend).toHaveBeenCalledWith(
      41,
      true,
      4,
      'review-41',
    );
  });
});
