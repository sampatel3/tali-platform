import { describe, expect, it, vi } from 'vitest';

import { reconcileRoleVersionConflict } from './roleConcurrency';

describe('reconcileRoleVersionConflict', () => {
  it('names the collaborator whose newer role change won', () => {
    const setRole = vi.fn((updater) => updater({ id: 44, version: 2, name: 'Old' }));
    const showToast = vi.fn();
    const error = {
      response: {
        status: 409,
        data: {
          detail: {
            code: 'ROLE_VERSION_CONFLICT',
            message: 'This job changed after you opened it.',
            current_version: 3,
            current_role: { id: 44, version: 3, name: 'Current' },
            changed_by: { name: 'Aisha Khan' },
          },
        },
      },
    };

    expect(reconcileRoleVersionConflict(error, setRole, showToast)).toBe(true);
    expect(showToast).toHaveBeenCalledWith(
      expect.stringContaining('Changed by Aisha Khan.'),
      'error',
    );
  });
});
