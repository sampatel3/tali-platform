import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useRoleOperationScope } from './useRoleOperationScope';

describe('useRoleOperationScope', () => {
  it('keeps pending state per role and suppresses commits outside its role', () => {
    const commit = vi.fn();
    const { result, rerender } = renderHook(
      ({ roleId }) => useRoleOperationScope(roleId),
      { initialProps: { roleId: 101 } },
    );
    const roleA = result.current.captureRoleScope(101);

    act(() => {
      expect(result.current.beginRoleOperation(roleA, 'lifecycle')).toBe(true);
    });
    expect(result.current.isRoleOperationPending('lifecycle')).toBe(true);

    rerender({ roleId: 202 });
    expect(result.current.isRoleOperationPending('lifecycle')).toBe(false);
    expect(result.current.commitRoleScope(roleA, commit)).toBe(false);
    expect(commit).not.toHaveBeenCalled();

    rerender({ roleId: 101 });
    expect(result.current.isRoleOperationPending('lifecycle')).toBe(true);
    expect(result.current.commitRoleScope(roleA, commit)).toBe(true);
    expect(commit).toHaveBeenCalledOnce();

    act(() => result.current.finishRoleOperation(roleA, 'lifecycle'));
    expect(result.current.isRoleOperationPending('lifecycle')).toBe(false);
  });
});
