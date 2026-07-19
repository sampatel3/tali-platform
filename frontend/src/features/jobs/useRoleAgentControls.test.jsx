import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useRoleAgentControls } from './useRoleAgentControls';

const deferred = () => {
  let resolve;
  const promise = new Promise((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
};

describe('useRoleAgentControls route scope', () => {
  it('clears old busy state and discards a pause response after navigation', async () => {
    const pauseRequest = deferred();
    const setRole = vi.fn();
    const showToast = vi.fn();
    const baseProps = {
      agentStatus: { paused: false },
      canControlAgent: true,
      handleRoleVersionConflict: vi.fn(() => false),
      loadRoleWorkspace: vi.fn(),
      mutateAgentStatus: vi.fn(() => pauseRequest.promise),
      setAgentStatus: vi.fn(),
      setRole,
      showToast,
    };
    const { result, rerender } = renderHook(
      ({ roleId, role }) => useRoleAgentControls({ ...baseProps, roleId, role }),
      { initialProps: { roleId: 101, role: { id: 101, version: 7 } } },
    );

    let pause;
    act(() => { pause = result.current.pauseAgent(); });
    expect(result.current.controlAction).toBe('pause');

    rerender({ roleId: 202, role: { id: 202, version: 3 } });
    expect(result.current.controlAction).toBeNull();

    await act(async () => {
      pauseRequest.resolve({ data: { id: 101, version: 8 } });
      await pause;
    });

    expect(setRole).not.toHaveBeenCalled();
    expect(showToast).not.toHaveBeenCalled();
    expect(result.current.controlAction).toBeNull();
  });
});
