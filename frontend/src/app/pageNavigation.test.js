import { describe, expect, it, vi } from 'vitest';

import { createPageNavigator } from './pageNavigation';

const context = {
  activeAssessmentToken: 'assessment-token',
  assessmentIdFromLink: 42,
  candidateDetailAssessmentId: 84,
  resetPasswordToken: 'reset-token',
  verifyEmailToken: 'verify-token',
};

describe('createPageNavigator', () => {
  it('keeps candidate membership role and navigation origin distinct', () => {
    const navigate = vi.fn();
    const navigateToPage = createPageNavigator(navigate, context);

    navigateToPage('candidate-report', {
      candidateApplicationId: 7,
      fromRoleId: 135,
      viewRoleId: 246,
      replace: true,
    });

    expect(navigate).toHaveBeenCalledWith(
      '/candidates/7?from=jobs/135&view_role_id=246',
      { replace: true },
    );
  });

  it('preserves shell assessment defaults and the chat query alias', () => {
    const navigate = vi.fn();
    const navigateToPage = createPageNavigator(navigate, context);

    navigateToPage('assessment-results');
    navigateToPage('chat', { initialQuery: 'pyspark candidates' });

    expect(navigate).toHaveBeenNthCalledWith(1, '/assessments/84', { replace: false });
    expect(navigate).toHaveBeenNthCalledWith(
      2,
      '/chat?q=pyspark%20candidates',
      { replace: false },
    );
  });
});
