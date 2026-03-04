export const pathForPage = (page, options = {}) => {
  switch (page) {
    case 'landing':
      return '/';
    case 'login':
      return '/login';
    case 'register':
      return '/register';
    case 'forgot-password':
      return '/forgot-password';
    case 'reset-password':
      return `/reset-password${options.resetPasswordToken ? `?token=${encodeURIComponent(options.resetPasswordToken)}` : ''}`;
    case 'verify-email':
      return `/verify-email${options.verifyEmailToken ? `?token=${encodeURIComponent(options.verifyEmailToken)}` : ''}`;
    case 'dashboard':
    case 'assessments':
      return '/assessments';
    case 'demo':
      return '/demo';
    case 'candidates':
      return '/candidates';
    case 'candidate-report':
      return options.candidateApplicationId
        ? `/candidates/${encodeURIComponent(options.candidateApplicationId)}`
        : '/candidates';
    case 'candidate-detail':
    case 'assessment-results':
      return options.candidateDetailAssessmentId
        ? `/assessments/${encodeURIComponent(options.candidateDetailAssessmentId)}`
        : '/assessments';
    case 'assessment-client-report':
      return options.candidateDetailAssessmentId
        ? `/assessments/${encodeURIComponent(options.candidateDetailAssessmentId)}/client-report${options.print ? '?print=1' : ''}`
        : '/assessments';
    case 'tasks':
      return '/tasks';
    case 'analytics':
    case 'reporting':
      return '/reporting';
    case 'settings':
      return '/settings';
    case 'settings-workable':
      return '/settings/workable';
    case 'settings-billing':
      return '/settings/billing';
    case 'settings-team':
      return '/settings/team';
    case 'settings-enterprise':
      return '/settings/enterprise';
    case 'settings-preferences':
      return '/settings/preferences';
    case 'candidate-welcome':
      if (options.assessmentIdFromLink && options.assessmentToken) {
        return `/assessment/${options.assessmentIdFromLink}?token=${encodeURIComponent(options.assessmentToken)}`;
      }
      if (options.assessmentToken) {
        return `/assess/${encodeURIComponent(options.assessmentToken)}`;
      }
      return '/';
    case 'candidate-feedback':
      return options.assessmentToken
        ? `/assessment/${encodeURIComponent(options.assessmentToken)}/feedback`
        : '/';
    case 'assessment':
      return `/assessment/live${options.assessmentToken ? `?token=${encodeURIComponent(options.assessmentToken)}` : ''}`;
    case 'workable-callback':
      return '/settings/workable/callback';
    default:
      return null;
  }
};
