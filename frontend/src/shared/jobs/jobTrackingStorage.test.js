import { beforeEach, describe, expect, it } from 'vitest';

import { clearJobTrackingStorage } from './jobTrackingStorage';

describe('jobTrackingStorage', () => {
  beforeEach(() => localStorage.clear());

  it('clears private job state without deleting unrelated preferences', () => {
    localStorage.setItem('tali_tracked_batch_roles', '[1]');
    localStorage.setItem('tali_tracked_process_roles:org-10:user-7', '[2]');
    localStorage.setItem('tali_dismissed_score_runs:org-10:user-7', '{"1":"1:99"}');
    localStorage.setItem('taali_theme', 'dark');

    clearJobTrackingStorage();

    expect(localStorage.getItem('tali_tracked_batch_roles')).toBeNull();
    expect(localStorage.getItem('tali_tracked_process_roles:org-10:user-7')).toBeNull();
    expect(localStorage.getItem('tali_dismissed_score_runs:org-10:user-7')).toBeNull();
    expect(localStorage.getItem('taali_theme')).toBe('dark');
  });
});
