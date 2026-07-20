import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
  agent: {
    status: vi.fn(),
    orgStatus: vi.fn(),
  },
}));

import { AgentBar, useAgentStatus, useAgentStatusOrg } from './AgentBar';
import { agent } from '../api';
import {
  activateSessionBoundary,
  beginSessionTransition,
  storeSessionProfile,
} from '../auth/sessionBoundary';

const setRecruiterSession = (profile) => {
  const boundary = beginSessionTransition();
  activateSessionBoundary(boundary, `token-${profile.id}`);
  storeSessionProfile(boundary, profile);
  return boundary;
};

describe('AgentBar — org rollup', () => {
  beforeEach(() => {
    agent.status.mockReset();
    agent.orgStatus.mockReset();
    localStorage.clear();
  });

  it('renders nothing when no roles have the agent enabled', async () => {
    // org-status reports zero enabled roles (no running, none paused).
    agent.orgStatus.mockResolvedValue({ data: {
      active_role_count: 0,
      paused_role_count: 0,
      pending_decisions: 0,
      org_budget_spent_cents: 0,
      org_budget_cap_cents: 0,
    } });

    const { container } = render(<AgentBar />);
    await waitFor(() => {
      expect(container.firstChild).toBeNull();
    });
    // The bar reads the single org aggregate — never the per-role fan-out.
    expect(agent.status).not.toHaveBeenCalled();
  });

  it('renders the org budget + pending totals from the aggregate', async () => {
    // Two running enabled roles; org-status already sums spend/budget/pending.
    agent.orgStatus.mockResolvedValue({ data: {
      active_role_count: 2,
      paused_role_count: 0,
      pending_decisions: 3,
      org_budget_spent_cents: 2300,
      org_budget_cap_cents: 7500,
      last_activity: { summary: 'Rejected Alex P · Role B', created_at: '2026-05-06T11:30:00Z' },
    } });

    render(<AgentBar />);

    // $2300 cents → "$23.00 / $75.00"
    await screen.findByText(/\$23\.00 \/ \$75\.00/);
    expect(screen.getByText(/3 awaiting your review/)).toBeInTheDocument();
    // last_activity.summary drives the tick line.
    expect(screen.getByText(/Rejected Alex P · Role B/)).toBeInTheDocument();
    // Single org aggregate call — no per-role fan-out.
    expect(agent.status).not.toHaveBeenCalled();
  });

  it('flips amber when monthly spend crosses 80% of budget', async () => {
    agent.orgStatus.mockResolvedValue({ data: {
      active_role_count: 1,
      paused_role_count: 0,
      pending_decisions: 0,
      org_budget_spent_cents: 4500,
      org_budget_cap_cents: 5000,
    } });

    const { container } = render(<AgentBar />);
    await waitFor(() => {
      const bar = container.querySelector('.mc-agent-bar');
      expect(bar).not.toBeNull();
      expect(bar.className).toContain('is-amber');
    });
  });

  it('reads "Agent mode paused" when every enabled role is paused', async () => {
    agent.orgStatus.mockResolvedValue({ data: {
      active_role_count: 0,
      paused_role_count: 2,
      pending_decisions: 0,
      org_budget_spent_cents: 0,
      org_budget_cap_cents: 5000,
    } });

    render(<AgentBar />);
    await screen.findByText(/Agent mode paused/);
  });

  it('deduplicates the org poll across simultaneous consumers', async () => {
    agent.orgStatus.mockResolvedValue({ data: {
      active_role_count: 1,
      paused_role_count: 0,
      pending_decisions: 7,
      org_budget_spent_cents: 100,
      org_budget_cap_cents: 5000,
    } });

    render(<><AgentBar /><AgentBar /></>);

    expect(await screen.findAllByText(/7 awaiting your review/)).toHaveLength(2);
    expect(agent.orgStatus).toHaveBeenCalledTimes(1);
  });

  it('forces a post-mutation org read past an older in-flight poll', async () => {
    setRecruiterSession({ id: 31, organization_id: 931 });
    let resolveOldPoll;
    agent.orgStatus
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveOldPoll = resolve;
      }))
      .mockResolvedValueOnce({ data: {
        active_role_count: 0,
        paused_role_count: 2,
        pending_decisions: 9,
        workspace_paused: true,
        workspace_control_version: 6,
      } });

    const { result } = renderHook(() => useAgentStatusOrg(true));
    await waitFor(() => expect(agent.orgStatus).toHaveBeenCalledTimes(1));

    await act(async () => {
      await result.current.refetch({ force: true });
    });
    expect(agent.orgStatus).toHaveBeenCalledTimes(2);
    expect(result.current.status).toMatchObject({
      workspace_paused: true,
      workspace_control_version: 6,
      pending_decisions: 9,
    });

    // The request that began before the workspace write can still resolve, but
    // its generation is obsolete and must not replace the fresh snapshot.
    await act(async () => {
      resolveOldPoll({ data: {
        active_role_count: 2,
        paused_role_count: 0,
        pending_decisions: 1,
        workspace_paused: false,
        workspace_control_version: 5,
      } });
    });
    expect(result.current.status).toMatchObject({
      workspace_paused: true,
      workspace_control_version: 6,
      pending_decisions: 9,
    });
  });

  it('protects role optimism from an earlier poll and reconciles after the mutation', async () => {
    let resolveOldPoll;
    agent.status
      .mockResolvedValueOnce({ data: { paused: false, paused_at: null, pending_decisions: 3 } })
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveOldPoll = resolve;
      }))
      .mockResolvedValueOnce({ data: {
        paused: true,
        paused_at: '2026-07-15T15:00:00Z',
        paused_reason: 'paused by recruiter',
        pending_decisions: 3,
      } });
    let resolveMutation;
    const request = vi.fn(() => new Promise((resolve) => {
      resolveMutation = resolve;
    }));

    const { result } = renderHook(() => useAgentStatus(44));
    await waitFor(() => expect(result.current.status?.paused).toBe(false));

    let oldPollPromise;
    act(() => {
      oldPollPromise = result.current.refetch();
    });
    await waitFor(() => expect(agent.status).toHaveBeenCalledTimes(2));

    let mutationPromise;
    act(() => {
      mutationPromise = result.current.mutateStatus({
        optimistic: (current) => ({
          ...current,
          paused: true,
          paused_at: 'saving',
          paused_reason: 'Saving…',
        }),
        request,
      });
    });
    expect(result.current.status).toMatchObject({ paused: true, paused_at: 'saving' });

    await act(async () => {
      resolveOldPoll({ data: { paused: false, paused_at: null, pending_decisions: 3 } });
      await oldPollPromise;
    });
    expect(result.current.status).toMatchObject({ paused: true, paused_at: 'saving' });

    await act(async () => {
      resolveMutation({ data: { version: 8 } });
      await mutationPromise;
    });
    expect(request).toHaveBeenCalledTimes(1);
    expect(agent.status).toHaveBeenCalledTimes(3);
    expect(result.current.status).toMatchObject({
      paused: true,
      paused_at: '2026-07-15T15:00:00Z',
      paused_reason: 'paused by recruiter',
    });
  });

  it('never exposes the previous role status while the next role is loading', async () => {
    let resolveNextRole;
    agent.status
      .mockResolvedValueOnce({ data: {
        paused: false,
        paused_at: null,
        pending_decisions: 7,
      } })
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveNextRole = resolve;
      }));

    const { result, rerender } = renderHook(
      ({ roleId }) => useAgentStatus(roleId),
      { initialProps: { roleId: 44 } },
    );

    expect(result.current.phase).toBe('loading');
    await waitFor(() => expect(result.current.phase).toBe('ready'));
    expect(result.current.status).toMatchObject({ paused: false, pending_decisions: 7 });

    rerender({ roleId: 45 });

    expect(result.current.phase).toBe('loading');
    expect(result.current.status).toBeNull();
    await waitFor(() => expect(agent.status).toHaveBeenCalledTimes(2));

    await act(async () => {
      resolveNextRole({ data: {
        paused: true,
        paused_at: '2026-07-15T18:00:00Z',
        pending_decisions: 2,
      } });
    });

    await waitFor(() => expect(result.current.phase).toBe('ready'));
    expect(result.current.status).toMatchObject({ paused: true, pending_decisions: 2 });
  });

  it('treats an empty role response as unavailable instead of loading forever', async () => {
    agent.status.mockResolvedValueOnce({ data: null });

    const { result } = renderHook(() => useAgentStatus(46));

    await waitFor(() => expect(result.current.phase).toBe('error'));
    expect(result.current.status).toBeNull();
    expect(result.current.error).toHaveProperty(
      'message',
      'Agent status response did not include a payload.',
    );
  });

  it('does not render the legacy role bar until its status is authoritative', async () => {
    let resolveStatus;
    agent.status.mockImplementationOnce(() => new Promise((resolve) => {
      resolveStatus = resolve;
    }));

    const { container } = render(<AgentBar roleId={47} />);

    expect(container.firstChild).toBeNull();

    await act(async () => {
      resolveStatus({ data: {
        paused: false,
        pending_decisions: 2,
        monthly_spent_cents: 100,
        monthly_budget_cents: 5000,
      } });
    });

    expect(await screen.findByText('Agent mode is ON')).toBeInTheDocument();
    expect(screen.getByText('2 awaiting your review')).toBeInTheDocument();
  });

  it('does not issue an opposite role mutation while the first control is saving', async () => {
    agent.status
      .mockResolvedValueOnce({ data: { paused: false, paused_at: null } })
      .mockResolvedValueOnce({ data: { paused: true, paused_at: '2026-07-15T15:00:00Z' } });
    let resolvePause;
    const pauseRequest = vi.fn(() => new Promise((resolve) => { resolvePause = resolve; }));
    const resumeRequest = vi.fn();
    const { result } = renderHook(() => useAgentStatus(45));
    await waitFor(() => expect(result.current.status?.paused).toBe(false));

    let pausePromise;
    act(() => {
      pausePromise = result.current.mutateStatus({
        optimistic: (current) => ({ ...current, paused: true, paused_at: 'saving' }),
        request: pauseRequest,
      });
    });

    let overlappingResult;
    await act(async () => {
      overlappingResult = await result.current.mutateStatus({
        optimistic: (current) => ({ ...current, paused: false, paused_at: null }),
        request: resumeRequest,
      });
    });
    expect(overlappingResult).toBeNull();
    expect(resumeRequest).not.toHaveBeenCalled();
    expect(result.current.status).toMatchObject({ paused: true, paused_at: 'saving' });

    await act(async () => {
      resolvePause({ data: { version: 8 } });
      await pausePromise;
    });
    expect(pauseRequest).toHaveBeenCalledTimes(1);
    expect(result.current.status).toMatchObject({
      paused: true,
      paused_at: '2026-07-15T15:00:00Z',
    });
  });

  it('does not reuse viewer-specific attribution after switching users in one org', async () => {
    setRecruiterSession({ id: 1, organization_id: 10 });
    agent.orgStatus.mockResolvedValueOnce({ data: {
      active_role_count: 0,
      paused_role_count: 1,
      workspace_paused: true,
      workspace_control_version: 3,
      workspace_paused_by: { user_id: 1, name: 'Sam', is_current_user: true },
    } });
    const first = renderHook(() => useAgentStatusOrg(true));
    await waitFor(() => expect(first.result.current.status?.workspace_paused_by).toMatchObject({
      name: 'Sam',
      is_current_user: true,
    }));
    first.unmount();

    let resolveNext;
    agent.orgStatus.mockImplementationOnce(() => new Promise((resolve) => { resolveNext = resolve; }));
    setRecruiterSession({ id: 2, organization_id: 10 });
    const second = renderHook(() => useAgentStatusOrg(true));
    expect(second.result.current.status).toBeNull();

    await act(async () => {
      resolveNext({ data: {
        active_role_count: 0,
        paused_role_count: 1,
        workspace_paused: true,
        workspace_control_version: 3,
        workspace_paused_by: { user_id: 1, name: 'Sam', is_current_user: false },
      } });
    });
    await waitFor(() => expect(second.result.current.status?.workspace_paused_by).toMatchObject({
      name: 'Sam',
      is_current_user: false,
    }));
  });

  it('does not reveal a warm snapshot after the signed-in org changes', async () => {
    setRecruiterSession({ id: 1, organization_id: 10 });
    agent.orgStatus.mockResolvedValueOnce({ data: {
      active_role_count: 1,
      paused_role_count: 0,
      pending_decisions: 2,
      org_budget_spent_cents: 1100,
      org_budget_cap_cents: 5000,
    } });
    const first = render(<AgentBar />);
    await screen.findByText(/\$11\.00 \/ \$50\.00/);
    first.unmount();

    let resolveNext;
    agent.orgStatus.mockImplementationOnce(() => new Promise((resolve) => {
      resolveNext = resolve;
    }));
    setRecruiterSession({ id: 2, organization_id: 20 });
    const second = render(<AgentBar />);

    expect(second.container).not.toHaveTextContent('$11.00 / $50.00');

    await act(async () => {
      resolveNext({ data: {
        active_role_count: 1,
        paused_role_count: 0,
        pending_decisions: 1,
        org_budget_spent_cents: 300,
        org_budget_cap_cents: 6000,
      } });
    });
    await screen.findByText(/\$3\.00 \/ \$60\.00/);
  });
});
