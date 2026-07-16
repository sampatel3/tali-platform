import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  RelatedRoleContextBanner,
  shouldRefreshRelatedRoleWorkspace,
  useEffectiveRelatedAgentResume,
} from './relatedRoleScoringUi';

const makeResumeProps = (overrides = {}) => ({
  agentStatus: {
    workspace_paused: true,
    workspace_control_version: 7,
  },
  canResumeWorkspace: true,
  onResumeRole: vi.fn(),
  refetchAgentStatus: vi.fn().mockResolvedValue({
    workspace_paused: true,
    workspace_control_version: 7,
  }),
  resumeWorkspace: vi.fn().mockResolvedValue({
    data: { affected: 0, skipped: 0 },
  }),
  reloadRole: vi.fn().mockResolvedValue(undefined),
  setPollingVersion: vi.fn(),
  showToast: vi.fn(),
  ...overrides,
});

const setupResume = (overrides) => {
  const props = makeResumeProps(overrides);
  const hook = renderHook(() => useEffectiveRelatedAgentResume(props));
  return { ...hook, props };
};

describe('related-role scoring workspace refresh', () => {
  it('refreshes after scored work pauses or any active state becomes terminal', () => {
    expect(shouldRefreshRelatedRoleWorkspace('running', 'waiting')).toBe(true);
    expect(shouldRefreshRelatedRoleWorkspace('running', 'retrying')).toBe(true);
    expect(shouldRefreshRelatedRoleWorkspace('waiting', 'completed')).toBe(true);
    expect(shouldRefreshRelatedRoleWorkspace('retrying', 'error')).toBe(true);
    expect(shouldRefreshRelatedRoleWorkspace('waiting', 'running')).toBe(false);
    expect(shouldRefreshRelatedRoleWorkspace('retrying', 'running')).toBe(false);
    expect(shouldRefreshRelatedRoleWorkspace(null, 'completed')).toBe(false);
    expect(shouldRefreshRelatedRoleWorkspace('waiting', null)).toBe(false);
  });
});

describe('related-role legacy workspace recovery', () => {
  it('keeps the bulk recovery owner-only and uses bulk-control language', () => {
    const onResumeWorkspace = vi.fn();
    const view = render(
      <RelatedRoleContextBanner
        role={{ ats_owner_role_name: 'Original engineering role' }}
        providerLabel="Workable"
        status={{ status: 'waiting', waiting_reason: 'workspace_paused' }}
        agentStatus={{ workspace_paused: true }}
        canResumeWorkspace={false}
        onResumeWorkspace={onResumeWorkspace}
        onOpenOriginal={vi.fn()}
      />,
    );

    expect(screen.getByText(/legacy workspace-wide agent hold is blocking scoring/i))
      .toBeInTheDocument();
    const resume = screen.getByRole('button', { name: 'Resume eligible paused agents' });
    expect(resume).toBeDisabled();
    expect(resume).toHaveAttribute(
      'title',
      'Only workspace owners can resume eligible paused agents.',
    );
    fireEvent.click(resume);
    expect(onResumeWorkspace).not.toHaveBeenCalled();

    view.rerender(
      <RelatedRoleContextBanner
        role={{ ats_owner_role_name: 'Original engineering role' }}
        providerLabel="Workable"
        status={{ status: 'waiting', waiting_reason: 'workspace_paused' }}
        agentStatus={{ workspace_paused: true }}
        canResumeWorkspace
        onResumeWorkspace={onResumeWorkspace}
        onOpenOriginal={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'Resume eligible paused agents' }))
      .toBeEnabled();
  });

  it('leaves ordinary role resume available to role-authorized members', async () => {
    const onResumeRole = vi.fn().mockResolvedValue('role resumed');
    const { result, props } = setupResume({
      agentStatus: { workspace_paused: false },
      canResumeWorkspace: false,
      onResumeRole,
    });

    let outcome;
    await act(async () => {
      outcome = await result.current();
    });

    expect(outcome).toBe('role resumed');
    expect(onResumeRole).toHaveBeenCalledOnce();
    expect(props.resumeWorkspace).not.toHaveBeenCalled();
  });

  it('guards the legacy bulk mutation even when invoked outside the disabled UI', async () => {
    const { result, props } = setupResume({ canResumeWorkspace: false });

    await act(async () => {
      expect(await result.current()).toBe(false);
    });

    expect(props.refetchAgentStatus).not.toHaveBeenCalled();
    expect(props.resumeWorkspace).not.toHaveBeenCalled();
    expect(props.showToast).toHaveBeenCalledWith(
      'Only a workspace owner can resume eligible paused agents.',
      'error',
    );
  });

  it('does not turn a stale cleared-overlay click into a new bulk resume', async () => {
    const { result, props } = setupResume({
      refetchAgentStatus: vi.fn().mockResolvedValue({
        workspace_paused: false,
        workspace_control_version: 8,
      }),
    });

    await act(async () => {
      expect(await result.current()).toBe(true);
    });

    expect(props.resumeWorkspace).not.toHaveBeenCalled();
    expect(props.refetchAgentStatus).toHaveBeenCalledOnce();
    expect(props.setPollingVersion).toHaveBeenCalledWith(expect.any(Function));
    expect(props.reloadRole).toHaveBeenCalledOnce();
    expect(props.showToast).toHaveBeenCalledWith(
      'The legacy workspace hold was already cleared. Related-role status is refreshing.',
      'info',
    );
  });

  it('uses the viewed version safely when the preflight refresh returns null', async () => {
    const { result, props } = setupResume({
      refetchAgentStatus: vi.fn().mockResolvedValue({ data: null }),
    });

    await act(async () => {
      await result.current();
    });

    expect(props.refetchAgentStatus).toHaveBeenCalledTimes(2);
    expect(props.resumeWorkspace).toHaveBeenCalledWith(7);
    expect(props.setPollingVersion).toHaveBeenCalledWith(expect.any(Function));
    expect(props.reloadRole).toHaveBeenCalledOnce();
    expect(props.showToast).toHaveBeenCalledWith(
      'The legacy workspace hold was cleared, but related-role status could not be refreshed yet.',
      'info',
    );
  });

  it('reports partial bulk resume results without promising scoring resumed', async () => {
    const refetchAgentStatus = vi.fn()
      .mockResolvedValueOnce({
        data: { workspace_paused: true, workspace_control_version: 9 },
      })
      .mockResolvedValueOnce({ workspace_paused: false, workspace_control_version: 10 });
    const { result, props } = setupResume({
      refetchAgentStatus,
      resumeWorkspace: vi.fn().mockResolvedValue({
        data: { affected: 1, skipped: 2 },
      }),
    });

    await act(async () => {
      await result.current();
    });

    expect(props.resumeWorkspace).toHaveBeenCalledWith(9);
    expect(props.showToast).toHaveBeenCalledWith(
      '1 role resumed; 2 need attention. Review role budgets and status, then retry.',
      'warning',
    );
    expect(props.setPollingVersion).toHaveBeenCalledWith(expect.any(Function));
    expect(props.reloadRole).toHaveBeenCalledOnce();
  });

  it('fails closed when neither the refresh nor viewed state has a version', async () => {
    const { result, props } = setupResume({
      agentStatus: { workspace_paused: true },
      refetchAgentStatus: vi.fn().mockResolvedValue(null),
    });

    await act(async () => {
      expect(await result.current()).toBe(false);
    });

    expect(props.resumeWorkspace).not.toHaveBeenCalled();
    expect(props.showToast).toHaveBeenCalledWith(
      'Workspace control state is still loading. Try again.',
      'error',
    );
  });
});
