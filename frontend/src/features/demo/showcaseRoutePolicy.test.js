import { describe, expect, it } from 'vitest';

import { demoReportViewMode } from './showcaseRoutePolicy';

describe('demo report route policy', () => {
  it('honours the locked client-view showcase URL', () => {
    expect(demoReportViewMode('demo', 'view=client&k=demo-token&showcase=1')).toBe('client');
  });

  it('does not let an incomplete query scrub a normal recruiter report', () => {
    expect(demoReportViewMode('demo', 'view=client&showcase=1')).toBeNull();
    expect(demoReportViewMode('42', 'view=client&k=demo-token&showcase=1')).toBeNull();
  });
});
