import { describe, expect, it } from 'vitest';

import {
  resolveAssessmentId,
  resolveAssessmentStatus,
} from './assessmentApplicationState';

describe('assessment application state', () => {
  it('prefers the current score-summary identity and status', () => {
    const application = {
      score_summary: { assessment_id: 42, assessment_status: 'COMPLETED' },
      valid_assessment_id: 7,
      valid_assessment_status: 'pending',
    };

    expect(resolveAssessmentId(application)).toBe(42);
    expect(resolveAssessmentStatus(application)).toBe('completed');
  });

  it('retains the valid-assessment fallback and safe empty values', () => {
    expect(resolveAssessmentId({ valid_assessment_id: 7 })).toBe(7);
    expect(resolveAssessmentStatus({ valid_assessment_status: 'IN_PROGRESS' })).toBe('in_progress');
    expect(resolveAssessmentId(null)).toBeNull();
    expect(resolveAssessmentStatus(null)).toBe('');
  });
});
