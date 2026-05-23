import { describe, it, expect } from 'vitest';

import { candidateReportHref } from './CandidateTriageDrawer';

describe('candidateReportHref', () => {
  it('omits ?from when no role id is supplied (never emits jobs/null)', () => {
    // Regression: Number(null) === 0 is finite, so the old guard produced
    // "?from=jobs/null", which the report back-link parser rejected and
    // fell back to "Back to home".
    expect(candidateReportHref({ id: 2393 })).toBe('/candidates/2393');
    expect(candidateReportHref({ id: 2393 }, null)).toBe('/candidates/2393');
    expect(candidateReportHref({ id: 2393 }, undefined)).toBe('/candidates/2393');
  });

  it('includes ?from=jobs/<id> for a real role id', () => {
    expect(candidateReportHref({ id: 2393 }, 31)).toBe('/candidates/2393?from=jobs/31');
    expect(candidateReportHref({ id: 2393 }, '31')).toBe('/candidates/2393?from=jobs/31');
  });

  it('falls back to /jobs without an application id', () => {
    expect(candidateReportHref(null)).toBe('/jobs');
    expect(candidateReportHref({})).toBe('/jobs');
  });
});
