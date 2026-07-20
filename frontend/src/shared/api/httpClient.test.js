import { describe, it, expect, beforeEach, vi } from 'vitest';

import api, {
  isPublicPath,
  getFreshSessionAuth,
  shouldRefreshToken,
  setAccessToken,
  clearAccessToken,
  isUserActive,
  REFRESH_TOKEN_AFTER_MS,
  USER_IDLE_CUTOFF_MS,
} from './httpClient';
import {
  initializeSessionBoundary,
  SESSION_BOUNDARY_STORAGE_KEY,
} from '../auth/sessionBoundary';

// Regression: a stale/expired JWT in localStorage + the auth bootstrap 401
// used to hard-redirect PUBLIC marketing pages to /login, because the 401
// interceptor kept its own public-path list and /blog and /developers were
// missing from it.
describe('httpClient isPublicPath (401 interceptor guard)', () => {
  it('treats the blog index and posts as public', () => {
    expect(isPublicPath('/blog')).toBe(true);
    expect(isPublicPath('/blog/ai-native-coding-and-knowledge-work')).toBe(true);
  });

  it('treats the developer portal as public', () => {
    expect(isPublicPath('/developers')).toBe(true);
  });

  it('keeps marketing/candidate routes public', () => {
    expect(isPublicPath('/')).toBe(true);
    expect(isPublicPath('/demo')).toBe(true);
    expect(isPublicPath('/showcase')).toBe(true);
    expect(isPublicPath('/careers/acme')).toBe(true);
    expect(isPublicPath('/assess/tok123')).toBe(true);
    expect(isPublicPath('/submittal/sub_abc')).toBe(true);
    expect(isPublicPath('/unsubscribe/tok_abc')).toBe(true);
  });

  it('still bounces recruiter routes', () => {
    expect(isPublicPath('/jobs')).toBe(false);
    expect(isPublicPath('/home')).toBe(false);
    expect(isPublicPath('/candidates')).toBe(false);
    expect(isPublicPath('/settings/billing')).toBe(false);
  });

  it('keeps recruiter-only /chat, /tasks/*, /admin non-public (kept in sync with the AppShell guard)', () => {
    expect(isPublicPath('/chat')).toBe(false);
    expect(isPublicPath('/chat/agents')).toBe(false);
    expect(isPublicPath('/tasks/42/preview')).toBe(false);
    expect(isPublicPath('/admin/decision-policy/org-1')).toBe(false);
  });

  it('keeps the showcase iframe bypass', () => {
    expect(isPublicPath('/jobs', '?showcase=1&demo=1')).toBe(true);
    expect(isPublicPath('/jobs', '?showcase=1')).toBe(false);
  });
});

// Regression: a dropped connection used to hang requests forever (no timeout),
// freezing "Working…" states with locked composers. The shared client now has
// a sane default; long-poll/streaming callers pass their own larger override.
describe('httpClient default timeout', () => {
  it('sets a 60s default request timeout', () => {
    expect(api.defaults.timeout).toBe(60000);
  });
});

// Sliding session: active users must not be silently logged out when the
// 30-minute access token expires — the client swaps the token for a fresh one
// once it's REFRESH_TOKEN_AFTER_MS old.
describe('httpClient sliding token refresh', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('refreshes once the token is older than the threshold', () => {
    const now = Date.now();
    expect(shouldRefreshToken(String(now), now)).toBe(false);
    expect(shouldRefreshToken(String(now - REFRESH_TOKEN_AFTER_MS + 1000), now)).toBe(false);
    expect(shouldRefreshToken(String(now - REFRESH_TOKEN_AFTER_MS - 1000), now)).toBe(true);
  });

  it('treats a missing or garbled issued-at stamp as stale (pre-feature sessions)', () => {
    expect(shouldRefreshToken(null)).toBe(true);
    expect(shouldRefreshToken(undefined)).toBe(true);
    expect(shouldRefreshToken('')).toBe(true);
    expect(shouldRefreshToken('not-a-number')).toBe(true);
  });

  it('only slides the session for a recently active user (unattended tabs idle out)', () => {
    const now = Date.now();
    expect(isUserActive(now, now)).toBe(true);
    expect(isUserActive(now - USER_IDLE_CUTOFF_MS + 1000, now)).toBe(true);
    expect(isUserActive(now - USER_IDLE_CUTOFF_MS - 1000, now)).toBe(false);
    expect(isUserActive(undefined, now)).toBe(false);
  });

  it('setAccessToken stamps the issue time; clearAccessToken removes both keys', () => {
    setAccessToken('tok-123');
    expect(localStorage.getItem('taali_access_token')).toBe('tok-123');
    const issuedAt = Number(localStorage.getItem('taali_token_issued_at'));
    expect(Number.isFinite(issuedAt)).toBe(true);
    expect(Math.abs(Date.now() - issuedAt)).toBeLessThan(5000);

    clearAccessToken();
    expect(localStorage.getItem('taali_access_token')).toBe(null);
    expect(localStorage.getItem('taali_token_issued_at')).toBe(null);
  });
});

describe('httpClient cross-tab session boundary', () => {
  beforeEach(() => {
    localStorage.clear();
    initializeSessionBoundary();
    setAccessToken('account-a-token');
  });

  it('refuses a protected request after another tab changes account', async () => {
    const oldBoundary = localStorage.getItem(SESSION_BOUNDARY_STORAGE_KEY);
    const externalBoundary = 'account-b-boundary';
    localStorage.setItem(SESSION_BOUNDARY_STORAGE_KEY, externalBoundary);
    localStorage.setItem('taali_access_token', 'account-b-token');
    window.dispatchEvent(new StorageEvent('storage', {
      key: SESSION_BOUNDARY_STORAGE_KEY,
      oldValue: oldBoundary,
      newValue: externalBoundary,
    }));
    const adapter = vi.fn().mockResolvedValue({
      data: {},
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {},
    });

    await expect(api.get('/roles', { adapter })).rejects.toMatchObject({
      code: 'ERR_CANCELED',
    });
    expect(adapter).not.toHaveBeenCalled();
  });

  it('also refuses the direct-fetch authentication path after an account change', async () => {
    const oldBoundary = localStorage.getItem(SESSION_BOUNDARY_STORAGE_KEY);
    const externalBoundary = 'account-b-stream-boundary';
    localStorage.setItem(SESSION_BOUNDARY_STORAGE_KEY, externalBoundary);
    localStorage.setItem('taali_access_token', 'account-b-token');
    window.dispatchEvent(new StorageEvent('storage', {
      key: SESSION_BOUNDARY_STORAGE_KEY,
      oldValue: oldBoundary,
      newValue: externalBoundary,
    }));

    await expect(getFreshSessionAuth()).rejects.toMatchObject({
      code: 'ERR_CANCELED',
    });
  });

  it('cancels when another tab changes the boundary while the token is being read', async () => {
    const originalGetItem = Storage.prototype.getItem;
    let tokenReads = 0;
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function read(key) {
      const value = originalGetItem.call(this, key);
      if (key === 'taali_access_token' && (tokenReads += 1) === 2) {
        this.setItem(SESSION_BOUNDARY_STORAGE_KEY, 'interleaved-account-boundary');
      }
      return value;
    });
    const adapter = vi.fn().mockResolvedValue({
      data: {},
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {},
    });

    try {
      await expect(api.get('/roles', { adapter })).rejects.toMatchObject({
        code: 'ERR_CANCELED',
      });
      expect(adapter).not.toHaveBeenCalled();
    } finally {
      getItem.mockRestore();
    }
  });

  it('rejects a successful old-account response after the boundary changes', async () => {
    let resolveRequest;
    const adapter = vi.fn((config) => new Promise((resolve) => {
      resolveRequest = () => resolve({
        data: { private: 'account-a-data' },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      });
    }));
    const request = api.get('/roles', { adapter });
    await vi.waitFor(() => expect(adapter).toHaveBeenCalledTimes(1));

    localStorage.setItem(SESSION_BOUNDARY_STORAGE_KEY, 'account-b-success-boundary');
    localStorage.setItem('taali_access_token', 'account-b-token');
    resolveRequest();

    await expect(request).rejects.toMatchObject({ code: 'ERR_CANCELED' });
  });

  it('does not let a stale 401 overwrite a replacement boundary while its token is in transition', async () => {
    let rejectRequest;
    const adapter = vi.fn((config) => new Promise((resolve, reject) => {
      rejectRequest = () => reject({
        config,
        response: { status: 401 },
      });
    }));
    const request = api.get('/roles', { adapter });
    await vi.waitFor(() => expect(adapter).toHaveBeenCalledTimes(1));

    const oldBoundary = localStorage.getItem(SESSION_BOUNDARY_STORAGE_KEY);
    const externalBoundary = 'account-b-boundary-during-switch';
    localStorage.setItem(SESSION_BOUNDARY_STORAGE_KEY, externalBoundary);
    clearAccessToken();
    window.dispatchEvent(new StorageEvent('storage', {
      key: SESSION_BOUNDARY_STORAGE_KEY,
      oldValue: oldBoundary,
      newValue: externalBoundary,
    }));

    rejectRequest();
    await expect(request).rejects.toMatchObject({
      response: { status: 401 },
    });
    expect(localStorage.getItem(SESSION_BOUNDARY_STORAGE_KEY)).toBe(externalBoundary);
  });
});
