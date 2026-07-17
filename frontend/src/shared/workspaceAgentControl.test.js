import { describe, expect, it, vi } from 'vitest';

import {
  resolveWorkspaceControlVersion,
  workspaceControlConflictMessage,
  workspaceControlVersion,
} from './workspaceAgentControl';

describe('workspace control version resolution', () => {
  it('uses a valid rendered version without refetching', async () => {
    const refetch = vi.fn();

    await expect(resolveWorkspaceControlVersion(7, refetch)).resolves.toBe(7);
    expect(refetch).not.toHaveBeenCalled();
  });

  it('refreshes an incomplete rolling-deploy snapshot after the click', async () => {
    const refetch = vi.fn().mockResolvedValue({ workspace_control_version: 8 });

    await expect(resolveWorkspaceControlVersion(null, refetch)).resolves.toBe(8);
    expect(refetch).toHaveBeenCalledWith({ force: true });
  });

  it('fails promptly instead of sending an unsafe unversioned command', async () => {
    const refetch = vi.fn().mockResolvedValue({ workspace_control_version: null });

    await expect(resolveWorkspaceControlVersion(undefined, refetch)).rejects.toThrow(
      'Workspace controls could not be refreshed.',
    );
    expect(workspaceControlVersion(0)).toBeNull();
  });
});

describe('workspaceControlConflictMessage', () => {
  it('names the collaborator who won a workspace pause race', () => {
    const error = {
      response: {
        data: {
          detail: {
            current: {
              changed_by: { action: 'paused', name: 'Aisha Khan', is_current_user: false },
            },
          },
        },
      },
    };
    expect(workspaceControlConflictMessage(error)).toBe(
      'All agents were paused by Aisha Khan in another session. The latest state is shown — review it and try again.',
    );
  });

  it('makes a same-account second-tab change explicit', () => {
    const error = {
      response: {
        data: {
          detail: {
            current: {
              changed_by: { action: 'resumed', name: 'Sam Patel', is_current_user: true },
            },
          },
        },
      },
    };
    expect(workspaceControlConflictMessage(error)).toContain('resumed by Sam Patel (you)');
  });
});
