import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useRoleAutonomyChange } from './useRoleAutonomyChange';

const family = {
  owner: { id: 12, name: 'Platform Engineer' },
  related: [{ id: 18, name: 'AI Platform Engineer' }],
};

describe('useRoleAutonomyChange family authority', () => {
  it('binds pre-screen auto-reject enablement to the confirmed family without changing scored rejection', async () => {
    const rolesApi = {
      update: vi.fn().mockResolvedValue({ data: { version: 5 } }),
      get: vi.fn(),
    };
    const setRole = vi.fn();
    const showToast = vi.fn();
    const role = { id: 12, version: 4, role_family: family };
    const { result } = renderHook(() => useRoleAutonomyChange({
      numericRoleId: 12,
      role,
      rolesApi,
      setRole,
      showToast,
    }));

    await act(async () => {
      await result.current('auto_reject_pre_screen', true, {
        expectedRoleFamily: family,
      });
    });

    expect(rolesApi.update).toHaveBeenCalledWith(12, {
      expected_version: 4,
      expected_role_family: family,
      auto_reject_pre_screen: true,
    });
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('auto-reject on'), 'success');
  });

  it('refreshes instead of replaying when the linked family changes', async () => {
    const latestRole = { id: 12, version: 5, role_family: family };
    const rolesApi = {
      update: vi.fn().mockRejectedValue({
        response: { status: 409, data: { detail: { code: 'ROLE_FAMILY_CHANGED' } } },
      }),
      get: vi.fn().mockResolvedValue({ data: latestRole }),
    };
    const setRole = vi.fn();
    const showToast = vi.fn();
    const { result } = renderHook(() => useRoleAutonomyChange({
      numericRoleId: 12,
      role: { id: 12, version: 4, role_family: family },
      rolesApi,
      setRole,
      showToast,
    }));

    await act(async () => {
      await result.current('auto_reject', true, {
        expectedRoleFamily: family,
      });
    });

    expect(rolesApi.update).toHaveBeenCalledTimes(1);
    expect(rolesApi.get).toHaveBeenCalledWith(12);
    expect(setRole).toHaveBeenCalledWith(latestRole);
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('Linked roles changed'), 'warning');
  });
});
