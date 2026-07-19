import { beforeEach, describe, expect, it } from 'vitest';

import {
  clearJobTrackingStorage,
  jobTrackingScope,
  scopedJobTrackingKey,
} from './jobTrackingStorage';

describe('job tracking storage', () => {
  beforeEach(() => localStorage.clear());

  it('isolates tracked jobs by organization and user', () => {
    const first = jobTrackingScope({ id: 7, organization_id: 10 });
    const second = jobTrackingScope({ id: 8, organization_id: 10 });
    expect(first).not.toBe(second);
    expect(scopedJobTrackingKey('tali_tracked_batch_roles', first))
      .toBe('tali_tracked_batch_roles:org-10:user-7');
  });

  it('clears every tracked-job key without touching unrelated preferences', () => {
    localStorage.setItem('tali_tracked_batch_roles:org-10:user-7', '[1]');
    localStorage.setItem('tali_tracked_process_roles:org-10:user-7', '[2]');
    localStorage.setItem('tali_dismissed_score_runs:org-10:user-7', '{"1":"1:99"}');
    localStorage.setItem('taali_theme', 'dark');
    clearJobTrackingStorage();
    expect(localStorage.getItem('tali_tracked_batch_roles:org-10:user-7')).toBeNull();
    expect(localStorage.getItem('tali_tracked_process_roles:org-10:user-7')).toBeNull();
    expect(localStorage.getItem('tali_dismissed_score_runs:org-10:user-7')).toBeNull();
    expect(localStorage.getItem('taali_theme')).toBe('dark');
  });
});
