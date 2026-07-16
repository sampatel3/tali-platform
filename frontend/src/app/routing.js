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
    case 'accept-invite':
      return `/accept-invite${options.acceptInviteToken ? `?token=${encodeURIComponent(options.acceptInviteToken)}` : ''}`;
    case 'dashboard':
      return '/dashboard';
    case 'home':
    case 'hub':
      return '/home';
    case 'jobs':
      return '/jobs';
    case 'requisitions':
      return '/requisitions';
    case 'chat': {
      const base = options.chatConversationId
        ? `/chat/${encodeURIComponent(options.chatConversationId)}`
        : '/chat';
      // Carrying a prefill from the global search → /chat keeps the
      // typed phrase visible in the composer so the user can refine
      // before sending.
      const q = options.chatInitialQuery ? String(options.chatInitialQuery).trim() : '';
      return q ? `${base}?q=${encodeURIComponent(q)}` : base;
    }
    case 'chat-agents':
      // The Search page's "Agents" tab — chat with each role's autonomous
      // agent (same threads as the Home dock, kept in sync server-side).
      // Optionally deep-links straight to one role's agent thread.
      return options.roleId
        ? `/chat/agents/${encodeURIComponent(options.roleId)}`
        : '/chat/agents';
    case 'job-pipeline':
      return options.roleId
        ? `/jobs/${encodeURIComponent(options.roleId)}`
        : '/jobs';
    case 'assessments':
      return '/assessments';
    case 'demo':
      return '/demo';
    case 'demo-lead':
      return '/demo-lead';
    case 'showcase':
      return '/showcase';
    case 'developers':
      return '/developers';
    case 'terms':
      return '/terms';
    case 'privacy':
      return '/privacy';
    case 'blog':
      return '/blog';
    case 'blog-post':
      return options.slug ? `/blog/${options.slug}` : '/blog';
    case 'candidate-report': {
      if (!options.candidateApplicationId) return '/jobs';
      const base = `/candidates/${encodeURIComponent(options.candidateApplicationId)}`;
      if (Number.isFinite(Number(options.fromRoleId))) {
        return `${base}?from=jobs/${encodeURIComponent(options.fromRoleId)}`;
      }
      if (options.fromHome) {
        return `${base}?from=home`;
      }
      return base;
    }
    case 'candidate-detail':
    case 'assessment-results':
      return options.candidateDetailAssessmentId
        ? `/assessments/${encodeURIComponent(options.candidateDetailAssessmentId)}`
        : '/assessments';
    case 'tasks':
      return '/tasks';
    case 'tasks-bespoke':
      return '/tasks/bespoke';
    case 'analytics':
      return '/analytics';
    case 'reporting':
      // Legacy alias — reporting folded into the dedicated Analytics page.
      return '/analytics';
    case 'settings':
      return '/settings';
    case 'settings-workable':
      return '/settings/workable';
    case 'settings-bullhorn':
      return '/settings/bullhorn';
    case 'settings-billing':
      return '/settings/billing';
    case 'settings-team':
      return '/settings/team';
    case 'settings-enterprise':
      return '/settings/enterprise';
    case 'settings-preferences':
      return '/settings/preferences';
    case 'settings-requisition-template':
      return '/settings/requisition-template';
    case 'candidate-welcome':
      if (options.assessmentIdFromLink && options.assessmentToken) {
        return `/assessment/${options.assessmentIdFromLink}?token=${encodeURIComponent(options.assessmentToken)}`;
      }
      if (options.assessmentToken) {
        return `/assess/${encodeURIComponent(options.assessmentToken)}`;
      }
      return '/';
    case 'assessment':
      return `/assessment/live${options.assessmentToken ? `?token=${encodeURIComponent(options.assessmentToken)}` : ''}`;
    case 'workable-callback':
      return '/settings/workable/callback';
    default:
      return null;
  }
};
