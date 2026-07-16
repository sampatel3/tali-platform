import { describe, expect, it } from 'vitest';

import { isProtectedRecruiterPath, isPublicPath } from './routePolicy';


describe('recruiter and showcase route boundaries', () => {
  it('never makes /jobs public through query-string flags', () => {
    expect(isProtectedRecruiterPath('/jobs', '?demo=1&showcase=1')).toBe(true);
    expect(isPublicPath('/jobs', '?demo=1&showcase=1')).toBe(false);
  });

  it('serves the fixture-only Jobs demo from the public showcase namespace', () => {
    expect(isProtectedRecruiterPath('/showcase/jobs')).toBe(false);
    expect(isPublicPath('/showcase/jobs')).toBe(true);
  });
});
