import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../shared/api', () => ({
  agent: {
    pauseAll: vi.fn(),
    resumeAll: vi.fn(),
  },
}));

import { agent as agentApi } from '../../shared/api';
import { useWorkspaceAgentControl } from './useWorkspaceAgentControl';

const setup = (overrides = {}) => {
  const props = {
    loadDecisions: vi.fn().mockResolvedValue(undefined),
    loadRoles: vi.fn().mockResolvedValue(undefined),
    refetchOrgStatus: vi.fn().mockResolvedValue({
      data: { workspace_control_version: 8 },
    }),
    showToast: vi.fn(),
    workspaceControlVersion: 7,
    ...overrides,
  };
  const hook = renderHook(() => useWorkspaceAgentControl(props));
  return { ...hook, props };
};

describe('useWorkspaceAgentControl reconciliation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('reports a saved mutation honestly when only the status refresh fails', async () => {
    agentApi.pauseAll.mockResolvedValue({ data: { affected: 2, skipped: 0 } });
    // The shared poller absorbs request failures and reports them as null so
    // routine background polling never creates unhandled rejections.
    const refetchOrgStatus = vi.fn().mockResolvedValue(null);
    const { result, props } = setup({ refetchOrgStatus });

    await act(async () => {
      await result.current.pause();
    });

    expect(agentApi.pauseAll).toHaveBeenCalledWith(7);
    expect(props.showToast).toHaveBeenLastCalledWith(
      'The workspace change was saved, but the latest status could not be refreshed yet.',
      'info',
    );
    expect(props.showToast).not.toHaveBeenCalledWith(
      expect.stringMatching(/could not update/i),
      'error',
    );
    expect(props.loadDecisions).toHaveBeenCalledOnce();
    expect(props.loadRoles).toHaveBeenCalledOnce();
    expect(result.current.action).toBeNull();
  });

  it('keeps the original collaborator conflict when reconciliation also fails', async () => {
    agentApi.pauseAll.mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: {
            current: {
              changed_by: {
                action: 'paused',
                name: 'Aisha Khan',
                is_current_user: false,
              },
            },
          },
        },
      },
    });
    const refetchOrgStatus = vi.fn().mockRejectedValue(new Error('poll unavailable'));
    const { result, props } = setup({ refetchOrgStatus });

    await act(async () => {
      await result.current.pause();
    });

    expect(props.showToast).toHaveBeenLastCalledWith(
      expect.stringMatching(/paused by Aisha Khan/i),
      'error',
    );
    expect(props.loadDecisions).not.toHaveBeenCalled();
    expect(props.loadRoles).not.toHaveBeenCalled();
    expect(result.current.action).toBeNull();
  });

  it('shows a partial-resume warning without suppressing background refreshes', async () => {
    agentApi.resumeAll.mockResolvedValue({
      data: { affected: 2, enabled_count: 3, skipped: 1 },
    });
    const { result, props } = setup();

    await act(async () => {
      await result.current.resume();
    });

    expect(agentApi.resumeAll).toHaveBeenCalledWith(7);
    expect(props.showToast).toHaveBeenLastCalledWith(
      '2 roles resumed; 1 needs attention. Review role budgets and status, then retry.',
      'warning',
    );
    await waitFor(() => expect(props.loadDecisions).toHaveBeenCalledOnce());
    expect(props.loadRoles).toHaveBeenCalledOnce();
    expect(result.current.action).toBeNull();
  });
});
