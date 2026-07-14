import React from 'react';
import { act, render, screen, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../shared/api/outreachClient', () => ({
  outreach: {
    createCampaign: vi.fn(),
    addAudience: vi.fn(),
    generate: vi.fn(),
    getCampaign: vi.fn(),
    approveAndSend: vi.fn(),
    listCampaigns: vi.fn(),
  },
}));

import { outreach as outreachApi } from '../../shared/api/outreachClient';
import { ReachOutDialog } from './ReachOutDialog';

const APPS = [
  { id: 11, candidate_id: 101, candidate_name: 'Ada Lovelace', candidate_email: 'ada@example.com' },
  { id: 12, candidate_id: 102, candidate_name: 'Alan Turing', candidate_email: 'alan@example.com' },
];

describe('ReachOutDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    outreachApi.createCampaign.mockResolvedValue({ data: { id: 77, status: 'draft' } });
    outreachApi.addAudience.mockResolvedValue({ data: { added: 2, skipped: [] } });
    outreachApi.generate.mockImplementation((_id, confirm) =>
      confirm
        ? Promise.resolve({ data: { count: 2, status: 'generating' } })
        : Promise.resolve({ data: { count: 2, estimated_cost_usd: 0.01 } }));
    outreachApi.getCampaign.mockResolvedValue({ data: { id: 77, status: 'ready' } });
    outreachApi.approveAndSend.mockImplementation((_id, confirm) =>
      confirm
        ? Promise.resolve({ data: { status: 'sending' } })
        : Promise.resolve({ data: { sendable_count: 2, will_send: 2, suppressed_excluded: 0, rejected_excluded: 0, failed_excluded: 0 } }));
  });

  afterEach(() => vi.useRealTimers());

  it('runs select → campaign → cost estimate → single send HITL → done', async () => {
    vi.useFakeTimers();
    const onSent = vi.fn();
    const onCompleted = vi.fn();
    render(<ReachOutDialog open roleId={5} roleTitle="Backend" applications={APPS} onClose={() => {}} onCompleted={onCompleted} onSent={onSent} />);

    // Step 1: review lists the selected candidates.
    expect(screen.getByText('Reach out to 2 sourced candidates')).toBeInTheDocument();
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();

    // Prepare: create campaign, set audience by application_ids, get cost.
    await act(async () => { fireEvent.click(screen.getByText('Prepare campaign')); });
    expect(outreachApi.createCampaign).toHaveBeenCalledWith({ name: expect.stringContaining('Backend'), role_id: 5 });
    expect(outreachApi.addAudience).toHaveBeenCalledWith(77, { application_ids: [11, 12] });
    expect(screen.getByText(/reachable/)).toBeInTheDocument();
    // Cost estimate shown before any draft happens.
    expect(screen.getByText(/\$0\.01/)).toBeInTheDocument();

    // Draft: confirm generate, then poll clears 'generating'.
    await act(async () => { fireEvent.click(screen.getByText('Draft messages')); });
    expect(outreachApi.generate).toHaveBeenCalledWith(77, true);
    // Advance past one poll interval; the campaign reads back 'ready' and the
    // send estimate is fetched (no waitFor — it stalls under fake timers).
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });

    // The ONE HITL: the send confirmation states the outward action + count.
    expect(outreachApi.approveAndSend).toHaveBeenCalledWith(77, false);
    expect(screen.getByText(/Send 2 messages/)).toBeInTheDocument();

    // Confirm the send — this is the only auto-send trigger.
    await act(async () => { fireEvent.click(screen.getByText(/Send 2 messages/)); });
    expect(outreachApi.approveAndSend).toHaveBeenCalledWith(77, true);
    expect(screen.getByText('Outreach on its way')).toBeInTheDocument();
    expect(onCompleted).toHaveBeenCalledWith(77);

    // Done → performance handoff.
    await act(async () => { fireEvent.click(screen.getByText('View campaign performance')); });
    expect(onSent).toHaveBeenCalledWith(77);
  });

  it('does not send when all selected candidates are excluded', async () => {
    outreachApi.addAudience.mockResolvedValue({
      data: { added: 0, skipped: [{ email: 'ada@example.com', reason: 'open_application' }] },
    });
    render(<ReachOutDialog open roleId={5} roleTitle="Backend" applications={APPS} onClose={() => {}} onSent={() => {}} />);
    await act(async () => { fireEvent.click(screen.getByText('Prepare campaign')); });

    expect(screen.getByText(/None of the selected candidates can be reached/)).toBeInTheDocument();
    // No draft button, no generate call, nothing can be sent.
    expect(screen.queryByText('Draft messages')).not.toBeInTheDocument();
    expect(outreachApi.generate).not.toHaveBeenCalled();
    expect(outreachApi.approveAndSend).not.toHaveBeenCalled();
  });
});
