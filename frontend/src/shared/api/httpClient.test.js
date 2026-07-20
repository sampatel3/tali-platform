import { describe, it, expect, beforeEach, vi } from 'vitest';

import api, {
  getFreshSessionAuth,
  shouldRefreshToken,
  isUserActive,
  REFRESH_TOKEN_AFTER_MS,
  USER_IDLE_CUTOFF_MS,
} from './httpClient';
import {
  activateSessionBoundary,
  beginSessionTransition,
  captureStoredSessionBoundary,
  getCurrentSessionSnapshot,
  getStoredSessionSnapshot,
  initializeSessionBoundary,
  revokeSessionBoundary,
  SESSION_BOUNDARY_STORAGE_KEY,
  SESSION_CREDENTIALS_PREFIX,
  updateSessionAccessToken,
} from '../auth/sessionBoundary';

const deferred = () => {
  let resolve;
  const promise = new Promise((settle) => { resolve = settle; });
  return { promise, resolve };
};

const response = (config, data = {}) => ({
  data,
  status: 200,
  statusText: 'OK',
  headers: {},
  config,
  request: {},
});

const legacyJwt = (subject, issuedAt) => {
  const encode = (value) => btoa(JSON.stringify(value))
    .replace(/=/g, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  return `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode({
    sub: subject,
    aud: ['fastapi-users:auth'],
    iat: issuedAt,
  })}.signature`;
};

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

  it('stores credentials under the boundary and removes them on revocation', () => {
    const boundary = beginSessionTransition();
    activateSessionBoundary(boundary, 'tok-123');
    const snapshot = getStoredSessionSnapshot();
    expect(snapshot?.token).toBe('tok-123');
    expect(Math.abs(Date.now() - snapshot.issuedAt)).toBeLessThan(5000);

    revokeSessionBoundary(boundary, { expectedToken: 'tok-123' });
    expect(getStoredSessionSnapshot()).toBeNull();
  });
});

describe('httpClient cross-tab session boundary', () => {
  beforeEach(() => {
    localStorage.clear();
    const boundary = beginSessionTransition();
    activateSessionBoundary(boundary, 'account-a-token');
  });

  it('refuses a protected request after another tab changes account', async () => {
    const oldBoundary = localStorage.getItem(SESSION_BOUNDARY_STORAGE_KEY);
    const externalBoundary = 'account-b-boundary';
    localStorage.setItem(SESSION_BOUNDARY_STORAGE_KEY, externalBoundary);
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
    const boundary = captureStoredSessionBoundary();
    const credentialsKey = `${SESSION_CREDENTIALS_PREFIX}${boundary}`;
    let credentialReads = 0;
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function read(key) {
      const value = originalGetItem.call(this, key);
      if (key === credentialsKey && (credentialReads += 1) === 1) {
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

  it('does not adopt a newer account after awaiting the older account refresh', async () => {
    const accountABoundary = captureStoredSessionBoundary();
    updateSessionAccessToken(accountABoundary, 'account-a-token', {
      expectedToken: 'account-a-token',
      issuedAt: 1,
    });
    const refresh = deferred();
    const originalAdapter = api.defaults.adapter;
    const adapter = vi.fn((config) => (
      config.url === '/auth/jwt/refresh'
        ? refresh.promise.then(() => response(config, { access_token: 'account-a-refreshed' }))
        : Promise.resolve(response(config))
    ));
    api.defaults.adapter = adapter;
    let accountARequest;

    try {
      accountARequest = api.get('/roles');
      await vi.waitFor(() => {
        expect(adapter.mock.calls.some(([config]) => config.url === '/auth/jwt/refresh')).toBe(true);
      });
      const accountBBoundary = beginSessionTransition();
      activateSessionBoundary(accountBBoundary, 'account-b-token');
      refresh.resolve();

      await expect(accountARequest).rejects.toMatchObject({ code: 'ERR_CANCELED' });
      expect(adapter.mock.calls.some(([config]) => config.url === '/roles')).toBe(false);
      expect(getStoredSessionSnapshot()).toMatchObject({
        boundary: accountBBoundary,
        token: 'account-b-token',
      });
    } finally {
      refresh.resolve();
      await accountARequest?.catch(() => {});
      api.defaults.adapter = originalAdapter;
    }
  });

  it('does not make the new account wait for an older account refresh', async () => {
    const accountABoundary = captureStoredSessionBoundary();
    updateSessionAccessToken(accountABoundary, 'account-a-token', {
      expectedToken: 'account-a-token',
      issuedAt: 1,
    });
    const refresh = deferred();
    const originalAdapter = api.defaults.adapter;
    const adapter = vi.fn((config) => (
      config.url === '/auth/jwt/refresh'
        ? refresh.promise.then(() => response(config, { access_token: 'account-a-refreshed' }))
        : Promise.resolve(response(config, { ok: true }))
    ));
    api.defaults.adapter = adapter;
    let accountARequest;

    try {
      accountARequest = api.get('/account-a');
      await vi.waitFor(() => {
        expect(adapter.mock.calls.some(([config]) => config.url === '/auth/jwt/refresh')).toBe(true);
      });
      const accountBBoundary = beginSessionTransition();
      activateSessionBoundary(accountBBoundary, 'account-b-token');

      await expect(api.get('/account-b')).resolves.toMatchObject({ data: { ok: true } });
      const accountBCall = adapter.mock.calls.find(([config]) => config.url === '/account-b')?.[0];
      expect(accountBCall?.headers?.Authorization).toBe('Bearer account-b-token');

      refresh.resolve();
      await expect(accountARequest).rejects.toMatchObject({ code: 'ERR_CANCELED' });
    } finally {
      refresh.resolve();
      await accountARequest?.catch(() => {});
      api.defaults.adapter = originalAdapter;
    }
  });

  it('binds direct-fetch authentication before awaiting refresh', async () => {
    const accountABoundary = captureStoredSessionBoundary();
    updateSessionAccessToken(accountABoundary, 'account-a-token', {
      expectedToken: 'account-a-token',
      issuedAt: 1,
    });
    const refresh = deferred();
    const originalAdapter = api.defaults.adapter;
    const adapter = vi.fn((config) => (
      refresh.promise.then(() => response(config, { access_token: 'account-a-refreshed' }))
    ));
    api.defaults.adapter = adapter;
    let directAuth;

    try {
      directAuth = getFreshSessionAuth();
      await vi.waitFor(() => expect(adapter).toHaveBeenCalledTimes(1));
      const accountBBoundary = beginSessionTransition();
      activateSessionBoundary(accountBBoundary, 'account-b-token');
      refresh.resolve();

      await expect(directAuth).rejects.toMatchObject({ code: 'ERR_CANCELED' });
      expect(getStoredSessionSnapshot()).toMatchObject({
        boundary: accountBBoundary,
        token: 'account-b-token',
      });
    } finally {
      refresh.resolve();
      await directAuth?.catch(() => {});
      api.defaults.adapter = originalAdapter;
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

    const externalBoundary = beginSessionTransition();
    activateSessionBoundary(externalBoundary, 'account-b-token');

    rejectRequest();
    await expect(request).rejects.toMatchObject({
      code: 'ERR_CANCELED',
    });
    expect(getStoredSessionSnapshot()).toMatchObject({
      boundary: externalBoundary,
      token: 'account-b-token',
    });
  });

  it('does not expose a stale non-2xx response body to the next account', async () => {
    let rejectRequest;
    const adapter = vi.fn((config) => new Promise((resolve, reject) => {
      rejectRequest = () => reject({
        config,
        response: {
          status: 409,
          data: { detail: 'private account A detail' },
        },
      });
    }));
    const request = api.get('/roles', { adapter });
    await vi.waitFor(() => expect(adapter).toHaveBeenCalledTimes(1));
    const accountBBoundary = beginSessionTransition();
    activateSessionBoundary(accountBBoundary, 'account-b-token');

    rejectRequest();

    await expect(request).rejects.toMatchObject({ code: 'ERR_CANCELED' });
    await request.catch((error) => {
      expect(error?.response?.data?.detail).toBeUndefined();
    });
  });
});

describe('httpClient mixed-version rollout isolation', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  const startLegacySession = (token) => {
    localStorage.setItem('taali_access_token', token);
    localStorage.setItem('taali_token_issued_at', String(Date.now()));
    initializeSessionBoundary();
  };

  it('keeps a v2 account-A request isolated from a later old-bundle token write', async () => {
    const accountAToken = legacyJwt('account-a', 10);
    const accountBToken = legacyJwt('account-b', 20);
    startLegacySession(accountAToken);
    let resolveRequest;
    const adapter = vi.fn((config) => new Promise((resolve) => {
      resolveRequest = () => resolve(response(config, { private: 'account-a-data' }));
    }));
    const request = api.get('/legacy-account-a', { adapter });
    await vi.waitFor(() => expect(adapter).toHaveBeenCalledTimes(1));

    localStorage.setItem('taali_access_token', accountBToken);
    resolveRequest();

    await expect(request).resolves.toMatchObject({
      data: { private: 'account-a-data' },
    });
    expect(localStorage.getItem('taali_access_token')).toBe(accountBToken);
    expect(getCurrentSessionSnapshot()).toMatchObject({ token: accountAToken });
  });

  it('does not let account-A 401 erase account B before the storage event', async () => {
    const accountAToken = legacyJwt('account-a', 10);
    const accountBToken = legacyJwt('account-b', 20);
    startLegacySession(accountAToken);
    let rejectRequest;
    const adapter = vi.fn((config) => new Promise((_resolve, reject) => {
      rejectRequest = () => reject({ config, response: { status: 401 } });
    }));
    const request = api.get('/legacy-account-a', { adapter });
    await vi.waitFor(() => expect(adapter).toHaveBeenCalledTimes(1));

    localStorage.setItem('taali_access_token', accountBToken);
    rejectRequest();

    await expect(request).rejects.toMatchObject({ code: 'ERR_CANCELED' });
    expect(localStorage.getItem('taali_access_token')).toBe(accountBToken);
  });

  it('does not adopt an old-bundle T2 after the scoped T1 receives a 401', async () => {
    const originalToken = legacyJwt('account-a', 10);
    const refreshedToken = legacyJwt('account-a', 20);
    startLegacySession(originalToken);
    let rejectRequest;
    const adapter = vi.fn((config) => new Promise((_resolve, reject) => {
      rejectRequest = () => reject({ config, response: { status: 401 } });
    }));
    const request = api.get('/legacy-account-a', { adapter });
    await vi.waitFor(() => expect(adapter).toHaveBeenCalledTimes(1));

    localStorage.setItem('taali_access_token', refreshedToken);
    localStorage.setItem('taali_token_issued_at', String(Date.now()));
    rejectRequest();

    await expect(request).rejects.toMatchObject({ code: 'ERR_CANCELED' });
    expect(localStorage.getItem('taali_access_token')).toBe(refreshedToken);
    expect(getCurrentSessionSnapshot()).toBeNull();
  });
});
