import { beforeEach, describe, expect, it } from 'vitest';

import {
  announceSessionBoundary,
  getCurrentSessionBoundary,
  initializeSessionBoundary,
  isRequestSessionCurrent,
  SESSION_BOUNDARY_STORAGE_KEY,
} from './sessionBoundary';

describe('sessionBoundary storage ordering', () => {
  beforeEach(() => {
    localStorage.clear();
    initializeSessionBoundary();
  });

  it('ignores an old queued storage event after this tab publishes a newer boundary', () => {
    const olderExternalBoundary = 'older-external-boundary';
    const ownedBoundary = announceSessionBoundary({ active: true });

    window.dispatchEvent(new StorageEvent('storage', {
      key: SESSION_BOUNDARY_STORAGE_KEY,
      newValue: olderExternalBoundary,
    }));

    expect(localStorage.getItem(SESSION_BOUNDARY_STORAGE_KEY)).toBe(ownedBoundary);
    expect(getCurrentSessionBoundary()).toBe(ownedBoundary);
    expect(isRequestSessionCurrent()).toBe(true);
  });
});
