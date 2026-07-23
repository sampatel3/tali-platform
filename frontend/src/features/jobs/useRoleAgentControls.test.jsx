import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useRoleAgentControls } from './useRoleAgentControls';
import { useRoleOperationScope } from './useRoleOperationScope';

const deferred = () => {
  let resolve;
  const promise = new Promise((settle) => { resolve = settle; });
  return { promise, resolve };
};

const useHarness = ({ roleId, mutateAgentStatus }) => {
  const roleScope = useRoleOperationScope(roleId);
  return useRoleAgentControls({
    ...roleScope,
    roleId,
    role: { id: roleId, version: 7 },
    agentStatus: { paused: false },
    canControlAgent: true,
    mutateAgentStatus,
    setAgentStatus: vi.fn(),
    setRole: vi.fn(),
    loadRoleWorkspace: vi.fn(),
    handleRoleVersionConflict: vi.fn(() => false),
    showToast: vi.fn(),
  });
};

describe('useRoleAgentControls', () => {
  it('clears a completed role operation even when A resolves on B before returning to A', async () => {
    const pendingPause = deferred();
    const mutateAgentStatus = vi.fn(() => pendingPause.promise);
    const { result, rerender } = renderHook(useHarness, {
      initialProps: { roleId: 101, mutateAgentStatus },
    });

    let completion;
    act(() => {
      completion = result.current.pauseAgent();
    });
    expect(result.current.controlAction).toBe('pause');

    rerender({ roleId: 202, mutateAgentStatus });
    expect(result.current.controlAction).toBeNull();

    await act(async () => {
      pendingPause.resolve({ data: { agentic_mode_enabled: true } });
      await completion;
    });
    expect(result.current.controlAction).toBeNull();

    rerender({ roleId: 101, mutateAgentStatus });
    expect(result.current.controlAction).toBeNull();
  });
});
