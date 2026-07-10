import { describe, it, expect } from 'vitest';

import api, { isPublicPath } from './httpClient';

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
  });

  it('still bounces recruiter routes', () => {
    expect(isPublicPath('/jobs')).toBe(false);
    expect(isPublicPath('/home')).toBe(false);
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
