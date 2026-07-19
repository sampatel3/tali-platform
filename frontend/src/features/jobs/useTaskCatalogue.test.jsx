import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { TASK_CATALOGUE_PAGE_SIZE, useTaskCatalogue } from './useTaskCatalogue';

describe('useTaskCatalogue', () => {
  it('loads one bounded page and fetches later pages only on request', async () => {
    const firstPage = Array.from({ length: TASK_CATALOGUE_PAGE_SIZE }, (_, index) => ({
      id: index + 1,
      name: `Task ${index + 1}`,
    }));
    const listTasks = vi.fn()
      .mockResolvedValueOnce({ data: firstPage })
      .mockResolvedValueOnce({ data: [{ id: 51, name: 'Task 51' }] });
    const { result } = renderHook(() => useTaskCatalogue({ enabled: true, listTasks }));

    await waitFor(() => expect(result.current.items).toHaveLength(TASK_CATALOGUE_PAGE_SIZE));
    expect(listTasks).toHaveBeenCalledTimes(1);
    expect(listTasks).toHaveBeenNthCalledWith(1, {
      limit: TASK_CATALOGUE_PAGE_SIZE,
      offset: 0,
    });
    expect(result.current.hasMore).toBe(true);

    await act(async () => {
      await result.current.loadMore();
    });

    expect(result.current.items).toHaveLength(51);
    expect(listTasks).toHaveBeenNthCalledWith(2, {
      limit: TASK_CATALOGUE_PAGE_SIZE,
      offset: TASK_CATALOGUE_PAGE_SIZE,
    });
    expect(result.current.hasMore).toBe(false);
  });

  it('debounces server search and resets it to page one', async () => {
    const listTasks = vi.fn()
      .mockResolvedValueOnce({ data: [{ id: 1, name: 'Initial task' }] })
      .mockResolvedValueOnce({ data: [{ id: 99, name: 'Remote React task' }] });
    const { result } = renderHook(() => useTaskCatalogue({ enabled: true, listTasks }));
    await waitFor(() => expect(result.current.items).toHaveLength(1));

    act(() => result.current.setQuery('Remote React'));
    expect(listTasks).toHaveBeenCalledTimes(1);

    await waitFor(() => expect(result.current.items).toEqual([
      { id: 99, name: 'Remote React task' },
    ]));
    expect(listTasks).toHaveBeenLastCalledWith({
      search: 'Remote React',
      limit: TASK_CATALOGUE_PAGE_SIZE,
      offset: 0,
    });
  });

  it('distinguishes a load failure from an empty catalogue and retries', async () => {
    const listTasks = vi.fn()
      .mockRejectedValueOnce({
        response: { data: { detail: 'Task library temporarily unavailable.' } },
      })
      .mockResolvedValueOnce({ data: [{ id: 7, name: 'Recovered task' }] });
    const { result } = renderHook(() => useTaskCatalogue({ enabled: true, listTasks }));

    await waitFor(() => expect(result.current.error).toMatch(/temporarily unavailable/i));
    expect(result.current.items).toEqual([]);

    act(() => result.current.retry());

    await waitFor(() => expect(result.current.items).toEqual([
      { id: 7, name: 'Recovered task' },
    ]));
    expect(result.current.error).toBe('');
    expect(listTasks).toHaveBeenCalledTimes(2);
  });
});
