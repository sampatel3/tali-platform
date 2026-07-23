import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useRoleAssessmentTasks } from './useRoleAssessmentTasks';

const deferred = () => {
  let resolve;
  const promise = new Promise((settle) => { resolve = settle; });
  return { promise, resolve };
};

describe('useRoleAssessmentTasks role scope', () => {
  it('does not reload or toast the previous role after navigation', async () => {
    const addTaskResponse = deferred();
    const rolesApi = {
      addTask: vi.fn().mockReturnValue(addTaskResponse.promise),
      removeTask: vi.fn(),
    };
    const currentRoleIdRef = { current: 101 };
    const loadRoleA = vi.fn();
    const loadRoleB = vi.fn();
    const showToast = vi.fn();
    const shared = {
      activeView: 'role-fit',
      currentRoleIdRef,
      handleRoleVersionConflict: vi.fn().mockReturnValue(false),
      roleTasks: [],
      rolesApi,
      setRefreshTick: vi.fn(),
      showToast,
      tasksApi: null,
    };

    const { result, rerender } = renderHook((props) => useRoleAssessmentTasks(props), {
      initialProps: {
        ...shared,
        loadRoleWorkspace: loadRoleA,
        numericRoleId: 101,
        role: { id: 101, version: 7 },
      },
    });

    let mutation;
    await act(async () => {
      mutation = result.current.assignAssessmentTasks([42]);
      await Promise.resolve();
    });
    expect(result.current.savingAssessmentTask).toBe(true);

    currentRoleIdRef.current = 202;
    rerender({
      ...shared,
      loadRoleWorkspace: loadRoleB,
      numericRoleId: 202,
      role: { id: 202, version: 3 },
    });
    expect(result.current.savingAssessmentTask).toBe(false);

    await act(async () => {
      addTaskResponse.resolve({ data: { version: 8 } });
      await mutation;
    });

    expect(loadRoleA).not.toHaveBeenCalled();
    expect(loadRoleB).not.toHaveBeenCalled();
    expect(showToast).not.toHaveBeenCalled();
    expect(result.current.savingAssessmentTask).toBe(false);
  });
});
