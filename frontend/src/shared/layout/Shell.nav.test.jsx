import { describe, it, expect } from 'vitest';

import { NAV_TABS } from './Shell';
import { pathForPage } from '../../app/routing';

// Candidates live per-job (each role's pipeline) and the cross-job
// "what needs a decision" view is the Home hub, so the redundant top-level
// Candidates tab was removed. The candidate standing REPORT drill-down
// (/candidates/:applicationId) is unrelated and must stay.
describe('primary nav tabs', () => {
  it('no longer exposes a top-level Candidates tab', () => {
    const ids = NAV_TABS.map((tab) => tab.id);
    expect(ids).not.toContain('candidates');
    expect(NAV_TABS.some((tab) => tab.label === 'Candidates')).toBe(false);
  });

  it('keeps the surrounding recruiter tabs intact', () => {
    const ids = NAV_TABS.map((tab) => tab.id);
    expect(ids).toEqual(['home', 'jobs', 'chat', 'tasks', 'analytics', 'settings']);
  });
});

describe('candidate report route resolution', () => {
  it('still resolves the candidate standing report by application id', () => {
    expect(
      pathForPage('candidate-report', { candidateApplicationId: 'shr_abc123' }),
    ).toBe('/candidates/shr_abc123');
  });

  it('no longer resolves a top-level candidates list page', () => {
    expect(pathForPage('candidates')).toBeNull();
  });
});
