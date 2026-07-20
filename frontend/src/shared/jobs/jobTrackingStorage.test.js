import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  activateSessionBoundary,
  beginSessionTransition,
  captureStoredSessionBoundary,
} from '../auth/sessionBoundary';
import {
  clearJobTrackingStorage,
  loadJobTrackingIds,
  persistJobTrackingIds,
} from './jobTrackingStorage';

const BATCH_KEY = 'tali_tracked_batch_roles';

describe('jobTrackingStorage', () => {
  beforeEach(() => {
    localStorage.clear();
    const boundary = beginSessionTransition();
    activateSessionBoundary(boundary, 'account-a-token');
  });

  it('ignores unscoped legacy values after v2 is active', () => {
    const boundary = captureStoredSessionBoundary();
    localStorage.setItem(BATCH_KEY, '[1]');
    localStorage.setItem('taali_theme', 'dark');

    expect(loadJobTrackingIds(BATCH_KEY, boundary)).toEqual([]);
    expect(localStorage.getItem(BATCH_KEY)).toBe('[1]');
    expect(localStorage.getItem('taali_theme')).toBe('dark');
  });

  it('does not let an old provider read or write the next session', () => {
    const accountABoundary = captureStoredSessionBoundary();
    persistJobTrackingIds(BATCH_KEY, accountABoundary, new Set([42]));
    const accountBBoundary = beginSessionTransition();
    activateSessionBoundary(accountBBoundary, 'account-b-token');

    expect(persistJobTrackingIds(BATCH_KEY, accountABoundary, new Set([99]))).toBe(false);
    expect(loadJobTrackingIds(BATCH_KEY, accountBBoundary)).toEqual([]);
    expect(persistJobTrackingIds(BATCH_KEY, accountBBoundary, new Set([7]))).toBe(true);
    expect(loadJobTrackingIds(BATCH_KEY, accountBBoundary)).toEqual([7]);
  });

  it('clears one session without deleting another session or preferences', () => {
    const accountABoundary = captureStoredSessionBoundary();
    persistJobTrackingIds(BATCH_KEY, accountABoundary, new Set([42]));
    const accountBBoundary = beginSessionTransition();
    activateSessionBoundary(accountBBoundary, 'account-b-token');
    persistJobTrackingIds(BATCH_KEY, accountBBoundary, new Set([7]));
    localStorage.setItem('taali_theme', 'dark');

    clearJobTrackingStorage(accountABoundary);

    expect(loadJobTrackingIds(BATCH_KEY, accountBBoundary)).toEqual([7]);
    expect(localStorage.getItem('taali_theme')).toBe('dark');
  });

  it('treats browser storage failures as best-effort instead of crashing the UI', () => {
    const boundary = captureStoredSessionBoundary();
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Quota exceeded', 'QuotaExceededError');
    });
    try {
      expect(persistJobTrackingIds(BATCH_KEY, boundary, new Set([42]))).toBe(false);
    } finally {
      setItem.mockRestore();
    }

    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('Storage denied', 'SecurityError');
    });
    try {
      expect(loadJobTrackingIds(BATCH_KEY, boundary)).toEqual([]);
    } finally {
      getItem.mockRestore();
    }
  });
});
