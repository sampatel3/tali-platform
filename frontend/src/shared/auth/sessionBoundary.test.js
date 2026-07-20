import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  activateSessionBoundary,
  beginSessionTransition,
  captureStoredSessionBoundary,
  getCurrentSessionBoundary,
  getCurrentSessionSnapshot,
  getStoredSessionSnapshot,
  initializeSessionBoundary,
  isRequestSessionCurrent,
  isSessionBoundaryCurrent,
  revokeSessionBoundary,
  SESSION_BOUNDARY_STORAGE_KEY,
  SESSION_CREDENTIALS_PREFIX,
  SESSION_MIGRATION_BOUNDARY_STORAGE_KEY,
  SESSION_PROFILE_PREFIX,
  SESSION_REVOKED_PREFIX,
  storeSessionProfile,
  updateSessionAccessToken,
} from './sessionBoundary';

const LEGACY_TOKEN_KEY = 'taali_access_token';
const BATCH_KEY = 'tali_tracked_batch_roles';
const FETCH_KEY = 'tali_tracked_fetch_roles';
const PRESCREEN_KEY = 'tali_tracked_pre_screen_roles';
const PROCESS_KEY = 'tali_tracked_process_roles';

const scopedJobsKey = (boundary, baseKey) => (
  `taali_session_jobs:${encodeURIComponent(boundary)}:${baseKey}`
);

describe('sessionBoundary storage ordering', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('ignores a queued pointer event after this tab publishes a newer boundary', () => {
    const ownedBoundary = beginSessionTransition();
    activateSessionBoundary(ownedBoundary, 'owned-token');

    window.dispatchEvent(new StorageEvent('storage', {
      key: SESSION_BOUNDARY_STORAGE_KEY,
      newValue: JSON.stringify({ version: 2, marker: 'older-boundary' }),
    }));

    expect(captureStoredSessionBoundary()).toBe(ownedBoundary);
    expect(getCurrentSessionBoundary()).toBe(ownedBoundary);
    expect(isRequestSessionCurrent()).toBe(true);
  });

  it('selects credentials only from the latest transition marker', () => {
    const older = beginSessionTransition();
    const newer = beginSessionTransition();
    activateSessionBoundary(newer, 'newer-token');
    localStorage.setItem(`${SESSION_CREDENTIALS_PREFIX}${older}`, JSON.stringify({
      version: 1,
      token: 'older-token',
      issuedAt: Date.now(),
    }));

    expect(getStoredSessionSnapshot()).toMatchObject({
      boundary: newer,
      token: 'newer-token',
    });
  });

  it('removes superseded scoped state and rejects a late activation', () => {
    const older = beginSessionTransition();
    activateSessionBoundary(older, 'older-token');
    storeSessionProfile(older, { id: 1, email: 'older@example.com' });
    localStorage.setItem(scopedJobsKey(older, BATCH_KEY), '[42]');

    const newer = beginSessionTransition();

    expect(activateSessionBoundary(older, 'late-token')).toBe(false);
    expect(captureStoredSessionBoundary()).toBe(newer);
    expect(localStorage.getItem(`${SESSION_CREDENTIALS_PREFIX}${older}`)).toBeNull();
    expect(localStorage.getItem(`${SESSION_PROFILE_PREFIX}${older}`)).toBeNull();
    expect(localStorage.getItem(scopedJobsKey(older, BATCH_KEY))).toBeNull();
  });

  it('updates only the expected scoped token and never republishes legacy mirrors', () => {
    const boundary = beginSessionTransition();
    activateSessionBoundary(boundary, 'token-t1', 123);
    storeSessionProfile(boundary, { id: 2, email: 'user@example.com' });

    expect(updateSessionAccessToken(boundary, 'wrong-update', {
      expectedToken: 'not-t1',
    })).toBe(false);
    expect(updateSessionAccessToken(boundary, 'token-t2', {
      expectedToken: 'token-t1',
      issuedAt: 456,
    })).toBe(true);

    expect(getStoredSessionSnapshot()).toMatchObject({
      boundary,
      token: 'token-t2',
      issuedAt: 456,
      profile: { id: 2, email: 'user@example.com' },
    });
    expect(localStorage.getItem(LEGACY_TOKEN_KEY)).toBeNull();
    expect(localStorage.getItem('taali_token_issued_at')).toBeNull();
    expect(localStorage.getItem('taali_user')).toBeNull();
  });

  it('migrates one production legacy session without trusting its cached profile', () => {
    localStorage.setItem(LEGACY_TOKEN_KEY, 'legacy-account-a-token');
    localStorage.setItem('taali_token_issued_at', '123');
    localStorage.setItem('taali_user', JSON.stringify({ id: 1, email: 'stale@example.com' }));
    localStorage.setItem(BATCH_KEY, '[42]');
    localStorage.setItem(FETCH_KEY, 'not-json');
    localStorage.setItem(PRESCREEN_KEY, '[7]');

    const boundary = initializeSessionBoundary();

    expect(getStoredSessionSnapshot()).toMatchObject({
      boundary,
      token: 'legacy-account-a-token',
      issuedAt: 123,
      profile: null,
    });
    expect(JSON.parse(localStorage.getItem(SESSION_MIGRATION_BOUNDARY_STORAGE_KEY))).toMatchObject({
      version: 2,
      marker: boundary,
      migration: { version: 1 },
    });
    expect(localStorage.getItem(scopedJobsKey(boundary, BATCH_KEY))).toBe('[42]');
    expect(localStorage.getItem(scopedJobsKey(boundary, FETCH_KEY))).toBe('[]');
    expect(localStorage.getItem(scopedJobsKey(boundary, PRESCREEN_KEY))).toBe('[7]');
    expect(localStorage.getItem(scopedJobsKey(boundary, PROCESS_KEY))).toBe('[]');
    // Compatibility is copy-only. Still-open old code keeps its own state,
    // while v2 never reads these shared keys again.
    expect(localStorage.getItem(LEGACY_TOKEN_KEY)).toBe('legacy-account-a-token');
    expect(localStorage.getItem(BATCH_KEY)).toBe('[42]');
    expect(localStorage.getItem('taali_user')).toContain('stale@example.com');
  });

  it('preserves a malformed pointer and fails closed', () => {
    localStorage.setItem(SESSION_BOUNDARY_STORAGE_KEY, 'malformed-boundary');
    localStorage.setItem(LEGACY_TOKEN_KEY, 'legacy-token');

    expect(initializeSessionBoundary()).toBe('malformed-boundary');
    expect(getStoredSessionSnapshot()).toBeNull();
    expect(localStorage.getItem(SESSION_BOUNDARY_STORAGE_KEY)).toBe('malformed-boundary');
    expect(localStorage.getItem(SESSION_MIGRATION_BOUNDARY_STORAGE_KEY)).toBeNull();
    expect(localStorage.getItem(LEGACY_TOKEN_KEY)).toBe('legacy-token');
  });

  it('preserves an unsupported structured pointer and fails closed', () => {
    const futureValue = JSON.stringify({ version: 3, marker: 'future-session' });
    localStorage.setItem(SESSION_BOUNDARY_STORAGE_KEY, futureValue);
    localStorage.setItem(LEGACY_TOKEN_KEY, 'legacy-token');

    expect(initializeSessionBoundary()).toBe('future-session');
    expect(getStoredSessionSnapshot()).toBeNull();
    expect(localStorage.getItem(SESSION_BOUNDARY_STORAGE_KEY)).toBe(futureValue);
    expect(localStorage.getItem(LEGACY_TOKEN_KEY)).toBe('legacy-token');
  });

  it('ignores every later legacy write or removal after the v2 cutover', () => {
    localStorage.setItem(LEGACY_TOKEN_KEY, 'account-a-token');
    localStorage.setItem(BATCH_KEY, '[42]');
    const boundary = initializeSessionBoundary();

    localStorage.setItem(LEGACY_TOKEN_KEY, 'account-b-token');
    localStorage.setItem('taali_user', JSON.stringify({ id: 2, email: 'b@example.com' }));
    localStorage.setItem(BATCH_KEY, '[99]');
    initializeSessionBoundary();
    localStorage.removeItem(LEGACY_TOKEN_KEY);
    initializeSessionBoundary();

    expect(getStoredSessionSnapshot()).toMatchObject({
      boundary,
      token: 'account-a-token',
      profile: null,
    });
    expect(localStorage.getItem(scopedJobsKey(boundary, BATCH_KEY))).toBe('[42]');
  });

  it('creates an inactive v2 boundary without claiming leftover jobs when no token exists', () => {
    localStorage.setItem(BATCH_KEY, '[42]');

    const boundary = initializeSessionBoundary();

    expect(boundary).toBeTruthy();
    expect(getStoredSessionSnapshot()).toBeNull();
    expect(localStorage.getItem(scopedJobsKey(boundary, BATCH_KEY))).toBe('[]');
    localStorage.setItem(LEGACY_TOKEN_KEY, 'late-old-bundle-token');
    initializeSessionBoundary();
    expect(getStoredSessionSnapshot()).toBeNull();
  });

  it('fails closed when the legacy token changes across the cutover', () => {
    localStorage.setItem(LEGACY_TOKEN_KEY, 'account-a-token');
    const originalSetItem = Storage.prototype.setItem;
    let interleaved = false;
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function set(key, value) {
      originalSetItem.call(this, key, value);
      if (!interleaved
        && key === SESSION_MIGRATION_BOUNDARY_STORAGE_KEY
        && String(value).includes('"migration"')) {
        interleaved = true;
        originalSetItem.call(this, LEGACY_TOKEN_KEY, 'account-b-token');
      }
    });

    try {
      initializeSessionBoundary();
    } finally {
      setItem.mockRestore();
    }

    expect(getStoredSessionSnapshot()).toBeNull();
    expect(isRequestSessionCurrent()).toBe(false);
    expect(localStorage.getItem(LEGACY_TOKEN_KEY)).toBe('account-b-token');
    const failedBoundary = captureStoredSessionBoundary();
    expect(localStorage.getItem(`${SESSION_CREDENTIALS_PREFIX}${failedBoundary}`)).toBeNull();
    expect(localStorage.getItem(`taali_session_migration:${failedBoundary}`)).toBeNull();
  });

  it('recovers a verified pending migration after a partial storage failure', () => {
    localStorage.setItem(LEGACY_TOKEN_KEY, 'legacy-token');
    localStorage.setItem(BATCH_KEY, '[42]');
    const originalSetItem = Storage.prototype.setItem;
    let failed = false;
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function set(key, value) {
      if (!failed && String(key).startsWith('taali_session_jobs:')) {
        failed = true;
        throw new DOMException('Quota exceeded', 'QuotaExceededError');
      }
      originalSetItem.call(this, key, value);
    });

    let boundary;
    try {
      expect(() => { boundary = initializeSessionBoundary(); }).not.toThrow();
      expect(getStoredSessionSnapshot()).toBeNull();
    } finally {
      setItem.mockRestore();
    }

    initializeSessionBoundary();
    expect(getStoredSessionSnapshot()).toMatchObject({ boundary, token: 'legacy-token' });
    expect(localStorage.getItem(scopedJobsKey(boundary, BATCH_KEY))).toBe('[42]');
  });

  it('does not let a migration completion overwrite a newer login', () => {
    localStorage.setItem(LEGACY_TOKEN_KEY, 'legacy-account-a-token');
    const originalSetItem = Storage.prototype.setItem;
    let accountBBoundary = null;
    let interleaved = false;
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function set(key, value) {
      originalSetItem.call(this, key, value);
      if (!interleaved
        && key === SESSION_MIGRATION_BOUNDARY_STORAGE_KEY
        && String(value).includes('"migration"')) {
        interleaved = true;
        accountBBoundary = beginSessionTransition();
        activateSessionBoundary(accountBBoundary, 'account-b-token');
      }
    });

    try {
      initializeSessionBoundary();
    } finally {
      setItem.mockRestore();
    }

    expect(getStoredSessionSnapshot()).toMatchObject({
      boundary: accountBBoundary,
      token: 'account-b-token',
    });
    expect(localStorage.getItem(SESSION_MIGRATION_BOUNDARY_STORAGE_KEY)).toBeNull();
  });

  it('adopts a completed credential only for a migration observed pending at initialization', () => {
    const boundary = 'pending-migration';
    const token = 'legacy-token';
    // tokenId is taken from a real migration receipt so this test stays
    // independent of the internal fingerprint implementation.
    localStorage.setItem(LEGACY_TOKEN_KEY, token);
    const generatedBoundary = initializeSessionBoundary();
    const pointer = JSON.parse(localStorage.getItem(SESSION_MIGRATION_BOUNDARY_STORAGE_KEY));
    const tokenId = pointer.migration.tokenId;

    localStorage.clear();
    const pointerValue = JSON.stringify({
      version: 2,
      marker: boundary,
      migration: { version: 1, tokenId },
    });
    localStorage.setItem(SESSION_MIGRATION_BOUNDARY_STORAGE_KEY, pointerValue);
    initializeSessionBoundary();
    expect(getCurrentSessionSnapshot()).toBeNull();

    const credentialsValue = JSON.stringify({
      version: 1,
      token,
      issuedAt: 0,
      migrationTokenId: tokenId,
      migrationComplete: true,
    });
    localStorage.setItem(`${SESSION_CREDENTIALS_PREFIX}${boundary}`, credentialsValue);
    window.dispatchEvent(new StorageEvent('storage', {
      key: `${SESSION_CREDENTIALS_PREFIX}${boundary}`,
      oldValue: null,
      newValue: credentialsValue,
    }));

    expect(generatedBoundary).toBeTruthy();
    expect(getCurrentSessionSnapshot()).toMatchObject({ boundary, token });
  });

  it('requires the request token and retains a marker-scoped tombstone on revocation', () => {
    const boundary = beginSessionTransition();
    activateSessionBoundary(boundary, 'expired-token');
    storeSessionProfile(boundary, { id: 3, email: 'expired@example.com' });
    localStorage.setItem(LEGACY_TOKEN_KEY, 'old-bundle-token');

    expect(revokeSessionBoundary(boundary)).toBe(false);
    expect(revokeSessionBoundary(boundary, { expectedToken: 'expired-token' })).toBe(true);

    expect(getStoredSessionSnapshot()).toBeNull();
    expect(localStorage.getItem(`${SESSION_CREDENTIALS_PREFIX}${boundary}`)).toBeNull();
    expect(localStorage.getItem(`${SESSION_PROFILE_PREFIX}${boundary}`)).toBeNull();
    expect(localStorage.getItem(`${SESSION_REVOKED_PREFIX}${boundary}`)).toBeTruthy();
    expect(localStorage.getItem(LEGACY_TOKEN_KEY)).toBe('old-bundle-token');
  });

  it('keeps revocation when a concurrent refresh removes its token after the tombstone', () => {
    const boundary = beginSessionTransition();
    activateSessionBoundary(boundary, 'token-t1');
    const credentialsStorageKey = `${SESSION_CREDENTIALS_PREFIX}${boundary}`;
    const revocationStorageKey = `${SESSION_REVOKED_PREFIX}${boundary}`;
    const originalSetItem = Storage.prototype.setItem;
    const originalRemoveItem = Storage.prototype.removeItem;
    let interleaved = false;
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function set(key, value) {
      originalSetItem.call(this, key, value);
      if (!interleaved && key === revocationStorageKey) {
        interleaved = true;
        originalSetItem.call(this, credentialsStorageKey, JSON.stringify({
          version: 1,
          token: 'token-t2',
          issuedAt: Date.now(),
        }));
        originalRemoveItem.call(this, credentialsStorageKey);
      }
    });

    try {
      expect(revokeSessionBoundary(boundary, { expectedToken: 'token-t1' })).toBe(true);
    } finally {
      setItem.mockRestore();
    }

    expect(isRequestSessionCurrent()).toBe(false);
    expect(localStorage.getItem(credentialsStorageKey)).toBeNull();
    expect(localStorage.getItem(revocationStorageKey)).toBeTruthy();
  });

  it('fails closed locally when the revocation tombstone cannot be written', () => {
    const boundary = beginSessionTransition();
    activateSessionBoundary(boundary, 'expired-token');
    const credentialsStorageKey = `${SESSION_CREDENTIALS_PREFIX}${boundary}`;
    const revocationStorageKey = `${SESSION_REVOKED_PREFIX}${boundary}`;
    const originalSetItem = Storage.prototype.setItem;
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function set(key, value) {
      if (key === revocationStorageKey) {
        throw new DOMException('Quota exceeded', 'QuotaExceededError');
      }
      originalSetItem.call(this, key, value);
    });

    try {
      expect(revokeSessionBoundary(boundary, { expectedToken: 'expired-token' })).toBe(true);
    } finally {
      setItem.mockRestore();
    }

    expect(getCurrentSessionSnapshot()).toBeNull();
    expect(localStorage.getItem(credentialsStorageKey)).toBeNull();
    expect(localStorage.getItem(revocationStorageKey)).toBeNull();
  });

  it('does not revive the prior session when a transition pointer write fails', () => {
    const boundary = beginSessionTransition();
    activateSessionBoundary(boundary, 'account-a-token');
    const credentialsStorageKey = `${SESSION_CREDENTIALS_PREFIX}${boundary}`;
    const originalSetItem = Storage.prototype.setItem;
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function set(key, value) {
      if (key === SESSION_BOUNDARY_STORAGE_KEY) {
        throw new DOMException('Quota exceeded', 'QuotaExceededError');
      }
      originalSetItem.call(this, key, value);
    });

    try {
      expect(beginSessionTransition()).toBeNull();
    } finally {
      setItem.mockRestore();
    }

    expect(getCurrentSessionSnapshot()).toBeNull();
    expect(localStorage.getItem(credentialsStorageKey)).toBeNull();
    initializeSessionBoundary();
    expect(getStoredSessionSnapshot()).toBeNull();
  });

  it('lets a newer scoped token win a stale revocation', () => {
    const boundary = beginSessionTransition();
    activateSessionBoundary(boundary, 'token-t1');
    const credentialsStorageKey = `${SESSION_CREDENTIALS_PREFIX}${boundary}`;
    const revocationStorageKey = `${SESSION_REVOKED_PREFIX}${boundary}`;
    const originalSetItem = Storage.prototype.setItem;
    let interleaved = false;
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function set(key, value) {
      if (!interleaved && key === revocationStorageKey) {
        interleaved = true;
        originalSetItem.call(this, credentialsStorageKey, JSON.stringify({
          version: 1,
          token: 'token-t2',
          issuedAt: Date.now(),
        }));
      }
      originalSetItem.call(this, key, value);
    });

    try {
      expect(revokeSessionBoundary(boundary, { expectedToken: 'token-t1' })).toBe(false);
    } finally {
      setItem.mockRestore();
    }

    expect(getCurrentSessionSnapshot()).toMatchObject({ boundary, token: 'token-t2' });
    expect(localStorage.getItem(revocationStorageKey)).toBeNull();
  });

  it('lets a stale 401 clean only its superseded marker', () => {
    const older = beginSessionTransition();
    activateSessionBoundary(older, 'older-token');
    const newer = beginSessionTransition();
    activateSessionBoundary(newer, 'newer-token');

    expect(revokeSessionBoundary(older, { expectedToken: 'older-token' })).toBe(false);
    expect(isSessionBoundaryCurrent(newer)).toBe(true);
    expect(getStoredSessionSnapshot()).toMatchObject({
      boundary: newer,
      token: 'newer-token',
    });
  });
});
