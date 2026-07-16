import { describe, expect, it } from 'vitest';

import { shouldRefreshRelatedRoleWorkspace } from './relatedRoleScoringUi';

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
