import { describe, expect, it } from 'vitest';

import { workspaceControlConflictMessage } from './workspaceAgentControl';

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
      'The workspace agent was paused by Aisha Khan in another session. The latest state is shown — review it and try again.',
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
