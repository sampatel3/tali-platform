import { describe, it, expect, beforeEach } from 'vitest';

import api, {
  publicApi,
  isPublicPath,
  shouldRefreshToken,
  setAccessToken,
  clearAccessToken,
  isUserActive,
  REFRESH_TOKEN_AFTER_MS,
  USER_IDLE_CUTOFF_MS,
} from './httpClient';

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
    expect(isPublicPath('/terms')).toBe(true);
    expect(isPublicPath('/privacy')).toBe(true);
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

  it('keeps showcase routes public without weakening /jobs', () => {
    expect(isPublicPath('/showcase/jobs')).toBe(true);
    expect(isPublicPath('/jobs', '?showcase=1&demo=1')).toBe(false);
  });
});

// Regression: a dropped connection used to hang requests forever (no timeout),
// freezing "Working…" states with locked composers. The shared client now has
// a sane default; long-poll/streaming callers pass their own larger override.
describe('httpClient default timeout', () => {
  it('sets a 60s default request timeout', () => {
    expect(api.defaults.timeout).toBe(60000);
    expect(publicApi.defaults.timeout).toBe(60000);
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
