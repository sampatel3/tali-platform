import { describe, expect, it } from 'vitest';

import { optimisticallyPauseRoleAgent, optimisticallyResumeRoleAgent } from './roleAgentStatusOptimism';

describe('role agent optimistic state beneath a workspace hold', () => {
  it('changes local desired state without replacing the effective workspace pause', () => {
    const status = {
      paused: true,
      pause_scope: 'workspace',
      paused_at: '2026-07-15T10:00:00Z',
      paused_by: { name: 'Aisha Khan' },
      workspace_paused: true,
      role_paused_at: null,
    };

    const paused = optimisticallyPauseRoleAgent(status, '2026-07-15T10:05:00Z');
    expect(paused).toMatchObject({
      pause_scope: 'workspace',
      paused_at: '2026-07-15T10:00:00Z',
      paused_by: { name: 'Aisha Khan' },
      role_paused_at: '2026-07-15T10:05:00Z',
      role_paused_by: { is_current_user: true },
    });

    const resumed = optimisticallyResumeRoleAgent(paused);
    expect(resumed).toMatchObject({
      paused: true,
      pause_scope: 'workspace',
      paused_at: '2026-07-15T10:00:00Z',
      role_paused_at: null,
      role_paused_by: null,
    });
  });

  it('updates both local and effective fields without a workspace hold', () => {
    const paused = optimisticallyPauseRoleAgent({ workspace_paused: false }, '2026-07-15T10:05:00Z');
    expect(paused).toMatchObject({
      paused: true,
      pause_scope: 'role',
      paused_at: '2026-07-15T10:05:00Z',
      role_paused_at: '2026-07-15T10:05:00Z',
    });
    expect(optimisticallyResumeRoleAgent(paused)).toMatchObject({
      paused: false,
      pause_scope: null,
      paused_at: null,
      role_paused_at: null,
    });
  });
});
