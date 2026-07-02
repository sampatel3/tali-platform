import { describe, it, expect } from 'vitest';

import { isPublicPath } from './httpClient';

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

  it('keeps the showcase iframe bypass', () => {
    expect(isPublicPath('/jobs', '?showcase=1&demo=1')).toBe(true);
    expect(isPublicPath('/jobs', '?showcase=1')).toBe(false);
  });
});
