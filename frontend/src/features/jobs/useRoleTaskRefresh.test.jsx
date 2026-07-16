import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useRoleTaskRefresh } from './useRoleTaskRefresh';

describe('useRoleTaskRefresh', () => {
  it('uses the bounded role shell and preserves fields outside that shell', async () => {
    const role = {
      id: 101,
      version: 7,
      role_kind: 'standard',
      criteria: [{ id: 4, text: 'Preserve me' }],
    };
    const shellRole = {
      id: 101,
      version: 8,
      role_kind: 'standard',
      assessment_task_provisioning: {
        activation_intent: { status: 'pending' },
      },
    };
    const rolesApi = {
      getShell: vi.fn().mockResolvedValue({ data: shellRole }),
      get: vi.fn(),
      listTasks: vi.fn().mockResolvedValue({
        data: [{ id: 7, name: 'Approved task', is_active: true }],
      }),
    };
    const setters = {
      setAssessmentContextTasks: vi.fn(),
      setAssessmentContextTasksFetchKnown: vi.fn(),
      setAssessmentContextTasksLoadError: vi.fn(),
      setRole: vi.fn(),
      setRoleTasks: vi.fn(),
      setRoleTasksFetchKnown: vi.fn(),
      setRoleTasksLoadError: vi.fn(),
    };
    const { result } = renderHook(() => useRoleTaskRefresh({
      numericRoleId: 101,
      role,
      rolesApi,
      ...setters,
      taskLoadSeqRef: { current: 0 },
    }));

    await act(async () => {
      await result.current.refreshRoleAndTasks();
    });

    expect(rolesApi.getShell).toHaveBeenCalledWith(101);
    expect(rolesApi.get).not.toHaveBeenCalled();
    expect(rolesApi.listTasks).toHaveBeenCalledWith(101);
    const applyShell = setters.setRole.mock.calls.at(-1)[0];
    expect(applyShell(role)).toEqual({
      ...role,
      ...shellRole,
      criteria: role.criteria,
    });
    expect(setters.setRoleTasks).toHaveBeenCalledWith([
      { id: 7, name: 'Approved task', is_active: true },
    ]);
  });

  it('refreshes only the original-role task context for a related scoring role', async () => {
    const role = {
      id: 101,
      version: 7,
      role_kind: 'sister',
      ats_owner_role_id: 77,
    };
    const originalTasks = [{ id: 9, name: 'Original assessment', is_active: true }];
    const rolesApi = {
      listTasks: vi.fn().mockResolvedValue({ data: originalTasks }),
    };
    const setters = {
      setAssessmentContextTasks: vi.fn(),
      setAssessmentContextTasksFetchKnown: vi.fn(),
      setAssessmentContextTasksLoadError: vi.fn(),
      setRole: vi.fn(),
      setRoleTasks: vi.fn(),
      setRoleTasksFetchKnown: vi.fn(),
      setRoleTasksLoadError: vi.fn(),
    };
    const { result } = renderHook(() => useRoleTaskRefresh({
      numericRoleId: 101,
      role,
      rolesApi,
      ...setters,
      taskLoadSeqRef: { current: 0 },
    }));

    await act(async () => {
      await result.current.refreshAssessmentTasks();
    });

    expect(rolesApi.listTasks).toHaveBeenCalledTimes(1);
    expect(rolesApi.listTasks).toHaveBeenCalledWith(77);
    expect(rolesApi.listTasks).not.toHaveBeenCalledWith(101);
    expect(setters.setRoleTasks).toHaveBeenCalledWith([]);
    expect(setters.setRoleTasksFetchKnown).toHaveBeenLastCalledWith(true);
    expect(setters.setAssessmentContextTasks).toHaveBeenCalledWith(originalTasks);
    expect(setters.setAssessmentContextTasksFetchKnown).toHaveBeenLastCalledWith(true);
  });
});
