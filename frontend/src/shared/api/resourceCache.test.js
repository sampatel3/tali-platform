import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  MAX_RESOURCE_CACHE_ENTRIES,
  clearCache,
  readCache,
  writeCache,
} from './resourceCache';

describe('resourceCache', () => {
  beforeEach(() => {
    clearCache();
    vi.useRealTimers();
  });

  it('evicts the least-recently-used entry at its fixed bound', () => {
    for (let index = 0; index < MAX_RESOURCE_CACHE_ENTRIES; index += 1) {
      writeCache(`key:${index}`, index);
    }
    expect(readCache('key:0')?.data).toBe(0);

    writeCache('key:overflow', 'new');

    expect(readCache('key:1')).toBeNull();
    expect(readCache('key:0')?.data).toBe(0);
    expect(readCache('key:overflow')?.data).toBe('new');
  });

  it('refreshes an overwritten key without evicting another entry early', () => {
    for (let index = 0; index < MAX_RESOURCE_CACHE_ENTRIES; index += 1) {
      writeCache(`key:${index}`, index);
    }
    writeCache('key:0', 'refreshed');
    writeCache('key:overflow', 'new');

    expect(readCache('key:0')?.data).toBe('refreshed');
    expect(readCache('key:1')).toBeNull();
    expect(readCache('key:2')?.data).toBe(2);
  });

  it('retains stale-while-revalidate metadata after an LRU touch', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    writeCache('short-lived', { ok: true }, 100);
    vi.advanceTimersByTime(101);

    expect(readCache('short-lived')).toEqual({ data: { ok: true }, isStale: true });
  });
});
