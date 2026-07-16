import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useCollectionFilterOptions } from './useCollectionFilterOptions';

describe('useCollectionFilterOptions', () => {
  it('uses SQL total immediately and hydrates complete options only on demand', async () => {
    const tasks = Array.from({ length: 215 }, (_, index) => ({ id: index + 1, name: `Task ${index}` }));
    const roles = Array.from({ length: 235 }, (_, index) => ({ id: index + 1, name: `Role ${index}` }));
    const tasksApi = {
      list: vi.fn(({ limit, offset }) => Promise.resolve({ data: tasks.slice(offset, offset + limit) })),
    };
    const rolesApi = {
      list: vi.fn(({ limit, offset }) => Promise.resolve({
        data: roles.slice(offset, offset + limit),
        headers: { 'x-total-count': '235' },
      })),
    };

    const { result } = renderHook(() => useCollectionFilterOptions(tasksApi, rolesApi));
    await waitFor(() => expect(result.current.roles).toHaveLength(100));
    expect(result.current.tasks).toHaveLength(100);
    expect(result.current.rolesCount).toBe(235);
    expect(rolesApi.list).toHaveBeenCalledTimes(1);
    expect(tasksApi.list).toHaveBeenCalledTimes(1);

    await act(async () => {
      await Promise.all([
        result.current.loadAllRoles(),
        result.current.loadAllTasks(),
      ]);
    });

    expect(result.current.roles).toHaveLength(235);
    expect(result.current.tasks).toHaveLength(215);
    expect(rolesApi.list).toHaveBeenCalledWith({ limit: 100, offset: 200 });
    expect(tasksApi.list).toHaveBeenCalledWith({ limit: 100, offset: 200 });
  });
});
