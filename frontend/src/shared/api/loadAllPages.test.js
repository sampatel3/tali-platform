import { describe, expect, it, vi } from 'vitest';

import { loadAllPages } from './loadAllPages';

describe('loadAllPages', () => {
  it('retrieves arbitrary later pages only when invoked', async () => {
    const rows = Array.from({ length: 235 }, (_, index) => ({ id: index + 1 }));
    const fetchPage = vi.fn(({ limit, offset }) => Promise.resolve({
      data: rows.slice(offset, offset + limit),
    }));

    expect(fetchPage).not.toHaveBeenCalled();
    const loaded = await loadAllPages(fetchPage, {
      initialItems: rows.slice(0, 100),
      pageSize: 100,
    });

    expect(loaded).toHaveLength(235);
    expect(fetchPage).toHaveBeenNthCalledWith(1, { limit: 100, offset: 100 });
    expect(fetchPage).toHaveBeenNthCalledWith(2, { limit: 100, offset: 200 });
  });
});
