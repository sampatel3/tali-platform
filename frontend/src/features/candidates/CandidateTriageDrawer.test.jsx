import { describe, expect, it } from 'vitest';

import { candidateReportHref } from './CandidateTriageDrawer';

describe('candidateReportHref', () => {
  it('returns the bare candidate path when no fromRoleId is provided', () => {
    expect(candidateReportHref({ id: 42 })).toBe('/candidates/42');
  });

  it('encodes ?from=jobs/{roleId} when fromRoleId is a number', () => {
    expect(candidateReportHref({ id: 42 }, 58)).toBe('/candidates/42?from=jobs/58');
  });

  it('coerces stringly-typed roleIds via Number.isFinite', () => {
    expect(candidateReportHref({ id: 42 }, '58')).toBe('/candidates/42?from=jobs/58');
  });

  it('falls back to bare path when fromRoleId is non-finite', () => {
    expect(candidateReportHref({ id: 42 }, NaN)).toBe('/candidates/42');
    expect(candidateReportHref({ id: 42 }, null)).toBe('/candidates/42');
    expect(candidateReportHref({ id: 42 }, undefined)).toBe('/candidates/42');
  });

  it('returns /candidates when application is null or has no id', () => {
    expect(candidateReportHref(null)).toBe('/candidates');
    expect(candidateReportHref({})).toBe('/candidates');
  });
});
