import { pathForPage } from './routing';

const optionOr = (options, key, fallback = null) => (
  Object.prototype.hasOwnProperty.call(options, key) ? options[key] : fallback
);

// AppShell owns transient assessment context, while callers own role/candidate
// context. Keeping their route-option assembly beside pathForPage prevents the
// shell from growing whenever a canonical candidate link gains new context.
export const createPageNavigator = (navigate, context) => (page, options = {}) => {
  const nextPath = pathForPage(page, {
    assessmentToken: optionOr(options, 'assessmentToken', context.activeAssessmentToken),
    assessmentIdFromLink: optionOr(options, 'assessmentIdFromLink', context.assessmentIdFromLink),
    candidateApplicationId: optionOr(options, 'candidateApplicationId'),
    clientId: optionOr(options, 'clientId'),
    candidateDetailAssessmentId: optionOr(
      options,
      'candidateDetailAssessmentId',
      context.candidateDetailAssessmentId,
    ),
    resetPasswordToken: optionOr(options, 'resetPasswordToken', context.resetPasswordToken),
    verifyEmailToken: optionOr(options, 'verifyEmailToken', context.verifyEmailToken),
    roleId: optionOr(options, 'roleId'),
    fromRoleId: optionOr(options, 'fromRoleId'),
    fromHome: Boolean(options.fromHome),
    viewRoleId: optionOr(options, 'viewRoleId'),
    chatInitialQuery: optionOr(options, 'initialQuery', optionOr(options, 'chatInitialQuery')),
  });

  if (nextPath) {
    navigate(nextPath, { replace: Boolean(options.replace) });
  }
};
