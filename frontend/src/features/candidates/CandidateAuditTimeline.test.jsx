import { act, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../shared/api', () => ({
  roles: {
    listApplicationEvents: vi.fn(),
  },
}));

import * as apiClient from '../../shared/api';
import { CandidateAuditTimeline } from './CandidateAuditTimeline';

const deferred = () => {
  let resolve;
  const promise = new Promise((settle) => { resolve = settle; });
  return { promise, resolve };
};

const event = (id, reason) => ({
  id,
  event_type: 'recruiter_note',
  actor_type: 'recruiter',
  reason,
  created_at: '2026-07-22T10:00:00Z',
});

describe('CandidateAuditTimeline role scope', () => {
  it('ignores a slower response from the previous logical role', async () => {
    const roleA = deferred();
    const roleB = deferred();
    apiClient.roles.listApplicationEvents
      .mockReturnValueOnce(roleA.promise)
      .mockReturnValueOnce(roleB.promise);

    const { rerender } = render(
      <CandidateAuditTimeline applicationId={77} roleId={31} />,
    );
    rerender(<CandidateAuditTimeline applicationId={77} roleId={135} />);

    await act(async () => {
      roleB.resolve({ data: [event(2, 'Related-role history')] });
      await roleB.promise;
    });
    expect(await screen.findByText('Related-role history')).toBeInTheDocument();

    await act(async () => {
      roleA.resolve({ data: [event(1, 'Owner-role history')] });
      await roleA.promise;
    });

    expect(screen.getByText('Related-role history')).toBeInTheDocument();
    expect(screen.queryByText('Owner-role history')).not.toBeInTheDocument();
    expect(apiClient.roles.listApplicationEvents).toHaveBeenNthCalledWith(2, 77, {
      limit: 100,
      role_id: 135,
    });
  });

  it('removes the previous role history before the next role resolves', async () => {
    apiClient.roles.listApplicationEvents.mockReset();
    const roleB = deferred();
    apiClient.roles.listApplicationEvents
      .mockResolvedValueOnce({ data: [event(1, 'Owner-role history')] })
      .mockReturnValueOnce(roleB.promise);

    const { rerender } = render(
      <CandidateAuditTimeline applicationId={77} roleId={31} />,
    );
    expect(await screen.findByText('Owner-role history')).toBeInTheDocument();

    rerender(<CandidateAuditTimeline applicationId={77} roleId={135} />);

    expect(screen.queryByText('Owner-role history')).not.toBeInTheDocument();
    expect(screen.getByText('Loading…')).toBeInTheDocument();

    await act(async () => {
      roleB.resolve({ data: [event(2, 'Related-role history')] });
      await roleB.promise;
    });
    expect(await screen.findByText('Related-role history')).toBeInTheDocument();
  });
});
