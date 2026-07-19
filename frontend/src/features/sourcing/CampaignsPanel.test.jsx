import React from 'react';
import { act, render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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
    approveAndSend: vi.fn(),
  },
}));

vi.mock('../../shared/api/prospectsClient', () => ({
  prospects: { list: vi.fn(() => Promise.resolve({ data: { prospects: [] } })) },
}));

vi.mock('../../shared/api/rolesClient', () => ({
  roles: { list: vi.fn(() => Promise.resolve({ data: { roles: [] } })) },
}));

import { outreach as outreachApi } from '../../shared/api/outreachClient';
import { prospects as prospectsApi } from '../../shared/api/prospectsClient';
import { roles as rolesApi } from '../../shared/api/rolesClient';
import CampaignsPanel from './CampaignsPanel';

describe('CampaignsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    outreachApi.listCampaigns.mockResolvedValue({ data: { campaigns: [] } });
  });

  afterEach(() => vi.useRealTimers());

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
    expect(outreachApi.listCampaigns).toHaveBeenCalledWith(
      null,
      { limit: 50, offset: 0 },
    );
  });

  it('loads later campaign pages without dropping the first page', async () => {
    outreachApi.listCampaigns
      .mockResolvedValueOnce({
        data: { campaigns: [{ id: 2, name: 'Newest', status: 'draft', counts: {} }], total: 2 },
      })
      .mockResolvedValueOnce({
        data: { campaigns: [{ id: 1, name: 'Older', status: 'sent', counts: {} }], total: 2 },
      });

    render(<CampaignsPanel />);
    await screen.findByText('Newest');
    fireEvent.click(screen.getByRole('button', { name: /Load more/ }));

    await screen.findByText('Older');
    expect(screen.getByText('Newest')).toBeInTheDocument();
    expect(outreachApi.listCampaigns).toHaveBeenLastCalledWith(
      null,
      { limit: 50, offset: 1 },
    );
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

  it('navigates every bounded message page without auto-fetching or retaining prior pages', async () => {
    const makeMessages = (start, end, firstStatus = 'pending') => Array.from(
      { length: end - start + 1 },
      (_, index) => {
        const id = start + index;
        return {
          id,
          email: `person-${id}@x.com`,
          recipient_name: `Person ${id}`,
          status: index === 0 ? firstStatus : 'pending',
        };
      },
    );
    const counts = {
      audience: 101,
      drafted: 2,
      pending: 99,
      draft: 1,
      approved: 1,
    };
    const response = (messages, offset, responseCounts = counts) => ({
      data: {
        id: 31,
        name: 'Paged Wave',
        status: 'ready',
        brief: '',
        counts: responseCounts,
        messages,
        messages_total: 101,
        messages_limit: 50,
        messages_offset: offset,
      },
    });
    const firstPage = makeMessages(1, 50);
    const secondPage = makeMessages(51, 100, 'draft');
    const refreshedSecondPage = [
      { ...secondPage[0], status: 'approved' },
      ...secondPage.slice(1),
    ];
    const refreshedCounts = { ...counts, draft: 0, approved: 2 };
    outreachApi.getCampaign
      .mockResolvedValueOnce(response(firstPage, 0))
      .mockResolvedValueOnce(response(secondPage, 50))
      .mockResolvedValueOnce(response(refreshedSecondPage, 50, refreshedCounts))
      .mockResolvedValueOnce(response(makeMessages(101, 101, 'approved'), 100, refreshedCounts))
      .mockResolvedValueOnce(response(refreshedSecondPage, 50, refreshedCounts));
    outreachApi.send.mockResolvedValue({ data: { approved_count: 1 } });
    outreachApi.approve.mockResolvedValue({ data: { approved: 1 } });

    render(<CampaignsPanel initialCampaignId={31} />);

    expect(await screen.findByTestId('message-1')).toBeInTheDocument();
    expect(outreachApi.getCampaign).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('message-51')).not.toBeInTheDocument();

    // Campaign-wide controls use exact server rollups even when the actionable
    // messages happen to be on a page that has not been loaded yet.
    expect(screen.getByRole('button', { name: 'Generate drafts (99)' })).toBeEnabled();
    expect(screen.getByTestId('approve-send-all')).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Approve all drafts' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Send approved (1)' })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: 'Send approved (1)' }));
    await waitFor(() => expect(outreachApi.send).toHaveBeenCalledWith(31, false));
    expect(screen.getByText('Send 1 approved message?')).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));

    fireEvent.click(screen.getByRole('button', { name: 'Next message page' }));
    expect(await screen.findByTestId('message-51')).toBeInTheDocument();
    expect(screen.queryByTestId('message-1')).not.toBeInTheDocument();
    expect(screen.getByText('Showing 51–100 of 101 messages. Page 2 of 3.')).toBeInTheDocument();

    // A per-message action refreshes the same server page, not page one.
    fireEvent.click(within(screen.getByTestId('message-51')).getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(outreachApi.approve).toHaveBeenCalledWith(31, { message_ids: [51] }));
    await waitFor(() => expect(outreachApi.getCampaign).toHaveBeenCalledTimes(3));

    fireEvent.click(screen.getByRole('button', { name: 'Next message page' }));
    expect(await screen.findByTestId('message-101')).toBeInTheDocument();
    expect(screen.queryByTestId('message-51')).not.toBeInTheDocument();
    expect(screen.getByText('Showing 101–101 of 101 messages. Page 3 of 3.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next message page' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Previous message page' }));
    expect(await screen.findByTestId('message-51')).toBeInTheDocument();
    expect(outreachApi.getCampaign).toHaveBeenNthCalledWith(1, 31, {
      limit: 50,
      offset: 0,
    });
    expect(outreachApi.getCampaign).toHaveBeenNthCalledWith(2, 31, {
      limit: 50,
      offset: 50,
    });
    expect(outreachApi.getCampaign).toHaveBeenNthCalledWith(3, 31, {
      limit: 50,
      offset: 50,
    });
    expect(outreachApi.getCampaign).toHaveBeenNthCalledWith(4, 31, {
      limit: 50,
      offset: 100,
    });
    expect(outreachApi.getCampaign).toHaveBeenNthCalledWith(5, 31, {
      limit: 50,
      offset: 50,
    });
  });

  it('uses exact actionable counts instead of treating completed lifecycle rows as drafts', async () => {
    outreachApi.getCampaign.mockResolvedValue({
      data: {
        id: 32,
        name: 'Completed Wave',
        status: 'ready',
        brief: '',
        counts: {
          audience: 3,
          drafted: 2,
          pending: 1,
          draft: 0,
          approved: 0,
          failed: 1,
          sent: 1,
        },
        messages: [
          { id: 1, email: 'pending@x.com', recipient_name: 'Pending', status: 'pending' },
        ],
        messages_total: 3,
      },
    });

    render(<CampaignsPanel initialCampaignId={32} />);

    expect(await screen.findByText('Completed Wave')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Generate drafts (1)' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Approve all drafts' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('approve-send-all')).not.toBeInTheDocument();
  });

  it('approve & send all: one HITL confirms the batch and enqueues the send', async () => {
    outreachApi.getCampaign.mockResolvedValue({
      data: {
        id: 30,
        name: 'Batch Wave',
        status: 'ready',
        brief: '',
        counts: {},
        messages: [
          { id: 1, email: 'a@x.com', recipient_name: 'A', status: 'draft' },
          { id: 2, email: 'b@x.com', recipient_name: 'B', status: 'draft' },
          { id: 3, email: 'c@x.com', recipient_name: 'C', status: 'approved' },
          { id: 4, email: 'r@x.com', recipient_name: 'R', status: 'pending' },
        ],
      },
    });
    outreachApi.approveAndSend.mockImplementation((id, confirm) =>
      confirm
        ? Promise.resolve({ data: { status: 'sending', will_send: 3 } })
        : Promise.resolve({
            data: {
              sendable_count: 3,
              will_send: 3,
              suppressed_excluded: 0,
              rejected_excluded: 1,
              failed_excluded: 0,
            },
          }),
    );

    render(<CampaignsPanel initialCampaignId={30} />);
    await waitFor(() => expect(screen.getByText('Batch Wave')).toBeInTheDocument());

    // The batch control counts both drafts (2) and the pre-approved (1) = 3.
    fireEvent.click(screen.getByTestId('approve-send-all'));
    // estimate call (confirm=false)
    await waitFor(() => expect(outreachApi.approveAndSend).toHaveBeenCalledWith(30, false));
    // confirmation is honest about the outward action + excluded rejected
    await waitFor(() =>
      expect(screen.getByText(/Send 3 messages to 3 prospects\?/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/1 rejected excluded/)).toBeInTheDocument();

    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Approve & send all' }));
    await waitFor(() => expect(outreachApi.approveAndSend).toHaveBeenCalledWith(30, true));
    expect(await screen.findByText('sending')).toBeInTheDocument();
  });

  it('polls a generating campaign until it reaches a stable state', async () => {
    vi.useFakeTimers();
    outreachApi.getCampaign
      .mockResolvedValueOnce({
        data: { id: 12, name: 'Polling Wave', status: 'generating', brief: '', messages: [] },
      })
      .mockResolvedValueOnce({
        data: {
          id: 12,
          name: 'Polling Wave',
          status: 'ready',
          brief: '',
          messages: [{ id: 8, email: 'ready@x.com', recipient_name: 'Ready', status: 'draft' }],
        },
      });

    await act(async () => {
      render(<CampaignsPanel initialCampaignId={12} />);
      await Promise.resolve();
    });
    expect(screen.getByText('generating')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Generate drafts/ })).toBeDisabled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(outreachApi.getCampaign).toHaveBeenCalledTimes(2);
    expect(screen.getByText('ready')).toBeInTheDocument();
    expect(screen.getByTestId('message-8')).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(outreachApi.getCampaign).toHaveBeenCalledTimes(2);
  });

  it('bounds polling when a background job never leaves its active state', async () => {
    vi.useFakeTimers();
    outreachApi.getCampaign.mockResolvedValue({
      data: { id: 13, name: 'Long Wave', status: 'sending', brief: '', messages: [] },
    });

    await act(async () => {
      render(<CampaignsPanel initialCampaignId={13} />);
      await Promise.resolve();
    });

    for (let attempt = 0; attempt < 30; attempt += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
    }

    expect(outreachApi.getCampaign).toHaveBeenCalledTimes(31);
    expect(screen.getByText(/taking longer than expected/i)).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    expect(outreachApi.getCampaign).toHaveBeenCalledTimes(31);
  });

  it('shows backend action errors inline instead of replacing the campaign', async () => {
    outreachApi.getCampaign.mockResolvedValue({
      data: { id: 14, name: 'Error Wave', status: 'ready', brief: 'Pitch', messages: [] },
    });
    outreachApi.patchCampaign.mockRejectedValue({
      response: { data: { detail: 'Brief is locked by another update' } },
    });

    render(<CampaignsPanel initialCampaignId={14} />);
    await screen.findByText('Error Wave');
    fireEvent.click(screen.getByRole('button', { name: 'Save brief' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Brief is locked by another update');
    expect(screen.getByText('Error Wave')).toBeInTheDocument();
  });

  it('offers only sourceable roles when creating a campaign', async () => {
    rolesApi.list.mockResolvedValue({
      data: {
        roles: [
          { id: 1, name: 'Open Role', job_status: 'open' },
          { id: 2, name: 'Manual Role' },
          { id: 3, name: 'Filled Role', job_status: 'filled' },
          { id: 4, name: 'Cancelled Role', job_status: 'cancelled' },
          { id: 5, name: 'Archived Role', workable_job_state: 'archived' },
        ],
      },
    });

    render(<CampaignsPanel />);
    fireEvent.click(screen.getByRole('button', { name: 'New campaign' }));

    expect(await screen.findByRole('option', { name: 'Open Role' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Manual Role' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Filled Role' })).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Cancelled Role' })).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Archived Role' })).not.toBeInTheDocument();
  });

  it('archives a stable campaign after explicit confirmation', async () => {
    outreachApi.getCampaign
      .mockResolvedValueOnce({
        data: { id: 18, name: 'Archive Wave', status: 'ready', brief: '', messages: [] },
      })
      .mockResolvedValueOnce({
        data: { id: 18, name: 'Archive Wave', status: 'archived', brief: '', messages: [] },
      });
    outreachApi.archiveCampaign.mockResolvedValue({ data: { id: 18, status: 'archived' } });

    render(<CampaignsPanel initialCampaignId={18} />);
    await screen.findByText('Archive Wave');
    fireEvent.click(screen.getByRole('button', { name: 'Archive' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Archive' }));

    await waitFor(() => expect(outreachApi.archiveCampaign).toHaveBeenCalledWith(18));
    expect(await screen.findByText('archived')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Archive' })).not.toBeInTheDocument();
  });

  it('reports campaign drill-in and back navigation for URL synchronization', async () => {
    const onCampaignChange = vi.fn();
    outreachApi.listCampaigns.mockResolvedValue({
      data: { campaigns: [{ id: 21, name: 'Linked Wave', status: 'draft', counts: {} }] },
    });
    outreachApi.getCampaign.mockResolvedValue({
      data: { id: 21, name: 'Linked Wave', status: 'draft', brief: '', messages: [] },
    });

    render(<CampaignsPanel onCampaignChange={onCampaignChange} />);
    await screen.findByText('Linked Wave');
    fireEvent.click(screen.getByRole('button', { name: 'Open' }));
    expect(onCampaignChange).toHaveBeenLastCalledWith(21);

    fireEvent.click(await screen.findByRole('button', { name: /Back to campaigns/ }));
    expect(onCampaignChange).toHaveBeenLastCalledWith(null);
    expect(await screen.findByRole('button', { name: 'Open' })).toBeInTheDocument();
  });

  it('paginates the campaign audience picker instead of truncating prospects', async () => {
    outreachApi.getCampaign.mockResolvedValue({
      data: { id: 24, name: 'Audience Wave', status: 'ready', brief: '', messages: [] },
    });
    prospectsApi.list.mockResolvedValue({
      data: {
        prospects: [{ id: 1, full_name: 'Page Prospect', email: 'page@example.com', status: 'new' }],
        total: 75,
      },
    });

    render(<CampaignsPanel initialCampaignId={24} />);
    await screen.findByText('Audience Wave');
    fireEvent.click(screen.getByRole('button', { name: 'Add from prospects' }));

    await waitFor(() => expect(prospectsApi.list).toHaveBeenCalledWith({
      status: 'new',
      limit: 50,
      offset: 0,
    }));
    fireEvent.click(await screen.findByRole('button', { name: 'Next' }));
    await waitFor(() => expect(prospectsApi.list).toHaveBeenLastCalledWith({
      status: 'new',
      limit: 50,
      offset: 50,
    }));
    expect(screen.getByText('Page 2 of 2')).toBeInTheDocument();
  });
});
